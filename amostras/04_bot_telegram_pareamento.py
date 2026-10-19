# Origem: src/orbit/telegram_bot.py (docstring do módulo, pareamento, filtro de
# chat e execução do pedido pendente; o roteamento de conversa foi omitido).
# Mostra: como uma conversa de fora do PC continua sob as mesmas travas:
# só chat pareado, código de uso único com prazo, toda ação pede "sim" no
# MESMO chat e o pedido expira. O token do bot fica no Gerenciador de
# Credenciais do Windows e nunca aparece em mensagem de erro.

"""Bot do Telegram para conversar com o Orbit de fora do PC.

Travas, na ordem em que valem:
- só responde a chats cadastrados; qualquer outro chat é ignorado sem resposta;
- o cadastro exige um código de pareamento mostrado no PC e válido por pouco tempo;
- só pede ações que a lista do Orbit já aceita (`validate_ai_plan`), nunca shell;
- toda ação pede "sim" no próprio Telegram, e o pedido expira em pouco tempo;
- o token fica no Gerenciador de Credenciais, nunca em arquivo ou log.

A API do Telegram exige o token no caminho da URL, por desenho deles. Por isso
nenhum erro daqui repete a URL: a mensagem de falha só diz o tipo da exceção.
"""

PENDENTE_S = 120.0
PAREAR_S = 300.0


class TelegramBot:
    # ... (__init__ recebe store, api, planner, bridge e um relógio injetável `agora`)

    def gerar_codigo_pareamento(self) -> str:
        codigo = f"{secrets.randbelow(900000) + 100000}"
        self._pareamento = (codigo, self.agora() + PAREAR_S)
        return codigo

    def processar(self, update: dict) -> list[tuple[int, str]]:
        mensagem = update.get("message") or {}
        chat_id = (mensagem.get("chat") or {}).get("id")
        texto = str(mensagem.get("text") or "").strip()
        if not isinstance(chat_id, int) or not texto:
            return []
        if texto.startswith("/parear"):
            return self._parear(chat_id, texto)
        if chat_id not in self.store.chats():
            return []  # chat desconhecido: silêncio, nem "não autorizado"
        pendente = self._pendentes.pop(chat_id, None)
        if pendente and self.agora() <= pendente.expira_em:
            resposta = texto.casefold()
            if resposta in ("sim", "s"):
                return [(chat_id, self._executar(pendente))]
            if resposta in ("não", "nao", "n"):
                return [(chat_id, "Cancelado.")]
        return [(chat_id, self._responder(texto, chat_id))]

    def _parear(self, chat_id: int, texto: str) -> list[tuple[int, str]]:
        partes = texto.split()
        # Motivo da recusa vai para o terminal (nunca para o Telegram).
        if self._pareamento is None:
            print("Pareamento recusado: não há código ativo. Reinicie o bot para gerar um novo.", flush=True)
            return []
        if len(partes) != 2:
            print("Pareamento recusado: mande /parear e o código na mesma linha.", flush=True)
            return []
        codigo, expira_em = self._pareamento
        if self.agora() > expira_em:
            print("Pareamento recusado: o código expirou. Reinicie o bot para gerar outro.", flush=True)
            return []
        if not secrets.compare_digest(partes[1], codigo):
            print("Pareamento recusado: código diferente do mostrado no terminal.", flush=True)
            return []
        self.store.adicionar_chat(chat_id)
        self._pareamento = None  # código de uso único
        return [(chat_id, "Pareado. Só este chat fala com o Orbit.")]

    # ... _responder: casadores locais (sem IA) para pedidos frequentes; o resto
    # vai ao planejador, passa por `validate_ai_plan` e, se for ação de app,
    # vira um _Pendente(slug, acao, params, expira_em) com a pergunta
    # "Responda 'sim' para confirmar ou 'não' para cancelar."

    def _executar(self, pendente) -> str:
        # ...
        try:
            resultado = self.bridge.executar_acao(pendente.slug, pendente.acao, pendente.params, confirmado=True)
        except Exception:
            return "Não consegui concluir o pedido. Confira no app antes de pedir de novo."
        return resultado.frase()
