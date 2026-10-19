# Origem: tests/test_telegram_bot.py (trecho: dublês + pareamento + confirmação
# + token; casos de conversa ligados a outros apps foram omitidos).
# Mostra: relógio injetável (Relogio) para testar prazo sem dormir, e
# verificação de que nada chega à ponte dos apps sem o "sim" do chat pareado.
# O "TOKEN" abaixo é um valor fictício do próprio teste.

from __future__ import annotations

import json
import sys
import unittest

from src.orbit.actions import ActionPlan
from src.orbit.telegram_bot import PAREAR_S, PENDENTE_S, TelegramApi, TelegramBot, TelegramStore, limpar_token

DONO = 111
ESTRANHO = 999
TOKEN = "123456:SEGREDO-DO-BOT"


class FakeStore:
    def __init__(self, chats=()):
        self._chats = set(chats)

    def chats(self):
        return set(self._chats)

    def adicionar_chat(self, chat_id):
        self._chats.add(chat_id)


class FakePlanner:
    def __init__(self, *planos):
        self.planos = list(planos)
        self.contextos = []

    def plan(self, texto, apps_context=""):
        self.contextos.append(apps_context)
        return self.planos.pop(0)


class FakeBridge:
    def __init__(self):
        self.chamadas = []

    def executar_acao(self, slug, acao, params, confirmado=False):
        self.chamadas.append((slug, acao, params, confirmado))
        return _Resultado("Certeza Duvidosa: " + acao + " feito.")


class _Resultado:
    def __init__(self, texto):
        self._texto = texto

    def frase(self):
        return self._texto


class FakeApi:
    def __init__(self):
        self.enviadas = []

    def send(self, chat_id, texto):
        self.enviadas.append((chat_id, texto))


class Relogio:
    def __init__(self):
        self.agora = 1000.0

    def __call__(self):
        return self.agora


def _msg(chat_id, texto):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": texto}}


def _app(acao, params=None, slug="certeza"):
    return ActionPlan("app_action", json.dumps({"slug": slug, "acao": acao, "params": params or {}}), "x")


def _bot(planner=None, bridge=None, chats=(DONO,), relogio=None):
    relogio = relogio or Relogio()
    return TelegramBot(FakeStore(chats), FakeApi(), planner or FakePlanner(), bridge or FakeBridge(), agora=relogio), relogio


class PareamentoTests(unittest.TestCase):
    def test_chat_nao_pareado_e_ignorado_sem_resposta(self):
        bot, _ = _bot(chats=())
        self.assertEqual(bot.processar(_msg(ESTRANHO, "oi")), [])

    def test_codigo_certo_cadastra_o_chat(self):
        store = FakeStore()
        relogio = Relogio()
        bot = TelegramBot(store, FakeApi(), FakePlanner(), FakeBridge(), agora=relogio)
        codigo = bot.gerar_codigo_pareamento()
        respostas = bot.processar(_msg(DONO, f"/parear {codigo}"))
        self.assertEqual(respostas[0][0], DONO)
        self.assertIn(DONO, store.chats())

    def test_codigo_errado_ou_expirado_nao_cadastra(self):
        store = FakeStore()
        relogio = Relogio()
        bot = TelegramBot(store, FakeApi(), FakePlanner(), FakeBridge(), agora=relogio)
        bot.gerar_codigo_pareamento()
        self.assertEqual(bot.processar(_msg(ESTRANHO, "/parear 000000")), [])
        self.assertNotIn(ESTRANHO, store.chats())
        codigo = bot._pareamento[0]
        relogio.agora += PAREAR_S + 1
        self.assertEqual(bot.processar(_msg(ESTRANHO, f"/parear {codigo}")), [])
        self.assertNotIn(ESTRANHO, store.chats())


class ConversaTests(unittest.TestCase):
    # ... (casos de resposta direta e de casadores locais omitidos)

    def test_chat_estranho_nao_recebe_nada_mesmo_com_pedido_pendente(self):
        planner = FakePlanner(_app("ligar_piloto", {"qual": "longo"}))
        bridge = FakeBridge()
        bot, _ = _bot(planner=planner, bridge=bridge)
        bot.processar(_msg(DONO, "liga o piloto"))
        self.assertEqual(bot.processar(_msg(ESTRANHO, "sim")), [])
        self.assertEqual(bridge.chamadas, [])

    def test_pedido_de_app_so_executa_com_sim(self):
        bridge = FakeBridge()
        bot, _ = _bot(planner=FakePlanner(_app("ligar_piloto", {"qual": "longo"})), bridge=bridge)
        respostas = bot.processar(_msg(DONO, "liga o piloto longo"))
        self.assertIn("Responda 'sim'", respostas[0][1])
        self.assertEqual(bridge.chamadas, [])
        final = bot.processar(_msg(DONO, "sim"))
        self.assertEqual(bridge.chamadas, [("certeza", "ligar_piloto", {"qual": "longo"}, True)])
        self.assertIn("feito", final[0][1])

    def test_nao_cancela_o_pedido(self):
        bridge = FakeBridge()
        bot, _ = _bot(planner=FakePlanner(_app("ligar_piloto", {"qual": "longo"})), bridge=bridge)
        bot.processar(_msg(DONO, "liga"))
        self.assertEqual(bot.processar(_msg(DONO, "não")), [(DONO, "Cancelado.")])
        self.assertEqual(bridge.chamadas, [])

    def test_sim_depois_do_prazo_nao_executa(self):
        bridge = FakeBridge()
        relogio = Relogio()
        bot, _ = _bot(planner=FakePlanner(_app("ligar_piloto", {"qual": "longo"})), bridge=bridge, relogio=relogio)
        bot.processar(_msg(DONO, "liga"))
        relogio.agora += PENDENTE_S + 1
        bot.processar(_msg(DONO, "sim"))
        self.assertEqual(bridge.chamadas, [])

    def test_shell_e_abrir_app_nao_passam(self):
        for plano in (ActionPlan("open_app", "powershell", "x"), ActionPlan("shutdown_pc", "system", "x")):
            with self.subTest(kind=plano.kind):
                bridge = FakeBridge()
                bot, _ = _bot(planner=FakePlanner(plano), bridge=bridge)
                respostas = bot.processar(_msg(DONO, "faz isso"))
                self.assertIn("Isso eu faço pelo PC", respostas[0][1])
                self.assertEqual(bridge.chamadas, [])

    def test_acao_fora_da_lista_e_recusada(self):
        bridge = FakeBridge()
        bot, _ = _bot(planner=FakePlanner(_app("apagar_tudo", {})), bridge=bridge)
        respostas = bot.processar(_msg(DONO, "apaga tudo"))
        self.assertIn("lista permitida", respostas[0][1])
        self.assertEqual(bridge.chamadas, [])


class TokenTests(unittest.TestCase):
    def test_erro_de_rede_nao_mostra_o_token(self):
        class OpenerQueFalha:
            def open(self, pedido, timeout=None):
                raise OSError(f"falhou em {pedido.full_url}")

        api = TelegramApi(TOKEN, opener=OpenerQueFalha())
        with self.assertRaises(RuntimeError) as contexto:
            api.send(DONO, "oi")
        self.assertNotIn(TOKEN, str(contexto.exception))
        self.assertNotIn("api.telegram.org", str(contexto.exception))



if __name__ == "__main__":
    unittest.main()
