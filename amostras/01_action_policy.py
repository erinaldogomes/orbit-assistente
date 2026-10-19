# Origem: src/orbit/action_policy.py (arquivo inteiro, sem cortes).
# Mostra: a matriz ÚNICA de risco e confirmação. Todo pedido (digitado, falado,
# vindo do Telegram ou da IA) passa por aqui; a interface não pode afrouxar.
# Ação não classificada cai em DEFAULT_POLICY: risco médio, confirma sempre.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionPolicy:
    risk: str
    confirmation: str
    network: bool = False
    retains_data: bool = False
    description: str = ""


# Uma única matriz governa pedidos digitados e falados. A camada de UI não pode
# reduzir estas exigências com base na origem do comando.
ACTION_POLICIES: dict[str, ActionPolicy] = {
    "answer": ActionPolicy("low", "none", network=True, description="Resposta da IA"),
    "open_app": ActionPolicy("medium", "permission", description="Abrir aplicativo"),
    "open_url": ActionPolicy("medium", "permission", network=True, description="Abrir site"),
    "web_search": ActionPolicy("medium", "always", network=True, description="Enviar busca ao navegador"),
    # Pedido do dono (15/10/2026): música toca direto, sem "posso?" a cada faixa.
    "play_spotify": ActionPolicy("low", "none", network=True, description="Tocar música no Spotify"),
    "spotify_media": ActionPolicy("low", "none", description="Controlar a reprodução do Spotify aberto"),
    "copy_text": ActionPolicy("low", "none", description="Copiar texto"),
    "set_timer": ActionPolicy("low", "none", description="Criar timer local"),
    "set_reminder": ActionPolicy("low", "always", retains_data=True, description="Criar lembrete local"),
    "list_reminders": ActionPolicy("low", "none", description="Consultar agenda local"),
    "cancel_reminder": ActionPolicy("low", "always", retains_data=True, description="Excluir lembrete local"),
    "time_info": ActionPolicy("low", "none", description="Consultar relógio local"),
    "set_brightness": ActionPolicy("low", "none", description="Alterar brilho"),
    "switch_keyboard_layout": ActionPolicy("low", "none", description="Trocar layout do teclado"),
    "open_system_settings": ActionPolicy("medium", "permission", description="Abrir painel seguro do Windows"),
    "open_known_folder": ActionPolicy("low", "none", description="Abrir pasta conhecida do usuário"),
    "system_status": ActionPolicy("low", "none", description="Consultar status local do sistema"),
    "youtube_search": ActionPolicy("medium", "permission", network=True, description="Pesquisar no YouTube"),
    "screenshot": ActionPolicy("medium", "none", retains_data=True, description="Salvar captura local"),
    "look_at_screen": ActionPolicy(
        "high", "none", network=True, retains_data=True,
        description="Capturar a tela e analisar pela IA",
    ),
    "start_guided_mode": ActionPolicy(
        "high", "permission", network=True, retains_data=True,
        description="Iniciar sessão explícita de controle guiado",
    ),
    "generate_image": ActionPolicy("medium", "always", network=True, retains_data=True, description="Gerar imagem pela IA"),
    # Pedir ação a um app local. "critical" e "always" de propósito, mesmo
    # quando o app declara `precisaConfirmar: false`: isto liga produção de
    # vídeo, que gasta placa e tempo de máquina, e a regra de nascença do Orbit
    # é que nada que gasta placa acontece sem o dono dizer "pode". Um clique a
    # mais em "desligar o piloto" não custa nada; um `ligar_piloto` silencioso
    # custa horas de GPU. Afrouxar depois é possível pela permissão persistente.
    "app_action": ActionPolicy(
        "critical", "always", network=True,
        description="Pedir uma ação a um app local (piloto de produção)",
    ),
    "shutdown_pc": ActionPolicy("critical", "none", description="Desligar o computador"),
    # O agendamento confirma sempre: o horário vem de interpretação de texto ou
    # de fala, e um horário entendido errado desliga a máquina fora de hora.
    "schedule_shutdown": ActionPolicy("critical", "always", description="Agendar o desligamento do computador"),
    "cancel_shutdown": ActionPolicy("low", "none", description="Cancelar o desligamento agendado"),
    "shutdown_status": ActionPolicy("low", "none", description="Consultar o desligamento agendado"),
    # Leitura dos três apps locais do dono pela ponte HTTP de loopback. Rede
    # marcada porque sai socket, mas nada deixa a máquina e nada é alterado nos
    # apps: só GET em rota de lista fechada. Ação que muda estado em app vem
    # depois, com confirmação própria, e não reaproveita esta entrada.
    "apps_briefing": ActionPolicy(
        "low", "none", network=True,
        description="Consultar o resumo dos apps locais (Orbix, LYRA, Certeza Duvidosa)",
    ),
    # O Orbit mexendo nas próprias configurações. "none" aqui porque a prévia
    # é da própria ação: aparência vale na hora (com desfazer) e o resto abre a
    # janela Atual/Novo antes de aplicar. A lista do que pode mudar fica em
    # `settings_commands.AI_SETTING_KEYS`.
    "ajustar_config": ActionPolicy("low", "none", description="Ajustar uma configuração do próprio Orbit"),
    # Compatibilidade: o gerador existente permanece com seu fluxo próprio.
    "generate_receipt": ActionPolicy("high", "legacy", retains_data=True, description="Gerar recibo"),
}


DEFAULT_POLICY = ActionPolicy("medium", "always", description="Ação não classificada")


def action_policy(kind: str) -> ActionPolicy:
    return ACTION_POLICIES.get(str(kind), DEFAULT_POLICY)


def may_skip_confirmation(kind: str, has_persistent_permission: bool = False) -> bool:
    policy = action_policy(kind)
    if policy.confirmation == "none":
        return True
    if policy.confirmation == "permission":
        return bool(has_persistent_permission)
    return False
