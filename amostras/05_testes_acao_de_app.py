# Origem: tests/test_apps_acao.py (trecho: dublês + 3 das 6 classes de teste do
# arquivo original; casos de parâmetros e de respostas HTTP omitidos).
# Mostra: testes da única escrita do Orbit para outro app, com rede e cofre de
# credenciais substituídos por dublês injetados. O arquivo original foi
# executado em 18/10/2026 com a rede bloqueada: OK.

"""Fase 3: a única escrita do Orbit para outro app.

Até aqui o Orbit só lia. Isto é o primeiro caminho em que ele pede a um app que
faça algo — ligar ou desligar o piloto de produção. Os testes aqui protegem
exatamente as coisas que, se derem errado, custam trabalho ou dinheiro do dono:

- ação que o app não declara não vira pedido nenhum;
- `confirmado` nunca viaja como `true` sem alguém ter dito isso de propósito;
- POST que estourou o tempo é **"não sei se executou"**, não "falhou" — porque
  tratar como falha faria o dono mandar de novo e ligar o piloto duas vezes;
- nenhuma repetição automática de escrita;
- o token vai no cabeçalho, sempre, e nunca na URL.

As formas conferidas aqui vieram da leitura do código real do Certeza
(`lib/orbit.js` e `server.js`, 06/10/2026), não do contrato escrito: o `acoes()`
dele devolve **array no topo** e **não declara `params`**, embora `ligar_piloto`
precise de `params.qual`.
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error

from src.orbit import apps_bridge
from src.orbit.apps_bridge import (
    APP_ENDPOINTS,
    AppsBridge,
    STATE_ACAO_DESCONHECIDA,
    STATE_CLOSED,
    STATE_ERROR,
    STATE_INCERTO,
    STATE_NEEDS_TOKEN,
    STATE_OK,
    STATE_PRECISA_CONFIRMAR,
    STATE_ROUTE_MISSING,
    TOKEN_HEADER,
)

# Lista que o Certeza declara de verdade, na forma que ele devolve (array no
# topo, sem campo `params`).
LISTA_DO_CERTEZA = [
    {"acao": "atualizar_numeros", "descricao": "Lê de novo as views.", "precisaConfirmar": False},
    {"acao": "desligar_piloto", "descricao": 'Desliga o piloto "shorts" ou "longo".', "precisaConfirmar": False},
    {"acao": "ligar_piloto", "descricao": 'Liga o piloto "shorts" ou "longo".', "precisaConfirmar": True},
]


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class FakeTokenStore:
    def __init__(self, tokens: dict[str, str] | None = None) -> None:
        if tokens is None:
            tokens = {slug: f"token-{slug}" for slug in APP_ENDPOINTS}
        self._tokens = dict(tokens)

    def load(self, slug):
        return self._tokens.get(slug)

    def save(self, slug, token):
        self._tokens[slug] = token

    def delete(self, slug):
        self._tokens.pop(slug, None)

    def configured_slugs(self):
        return list(self._tokens)


class FakeOpener:
    def __init__(self, handler) -> None:
        self._handler = handler
        self.requests: list = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        result = self._handler(request)
        if isinstance(result, Exception):
            raise result
        return result


def _resposta_da_acao(request, resposta_post):
    """Serve a lista no GET e o que o teste programou no POST."""
    if request.get_method() == "GET":
        return FakeResponse(json.dumps(LISTA_DO_CERTEZA).encode("utf-8"))
    return resposta_post


def _bridge(resposta_post, tokens: dict[str, str] | None = None):
    opener = FakeOpener(lambda req: _resposta_da_acao(req, resposta_post))
    bridge = AppsBridge(token_store=FakeTokenStore(tokens), opener=opener)
    return bridge, opener


def _ok(**extra) -> FakeResponse:
    corpo = {"acao": "ligar_piloto", "ok": True, "piloto": {"shorts": False, "longo": True}}
    corpo.update(extra)
    return FakeResponse(json.dumps(corpo).encode("utf-8"))


def _posts(opener) -> list:
    return [r for r in opener.requests if r.get_method() == "POST"]


def _corpo(request) -> dict:
    return json.loads(request.data.decode("utf-8"))


class ListaFechadaTests(unittest.TestCase):
    def test_le_a_lista_em_array_no_topo(self) -> None:
        # Era o defeito silencioso: `_read` recusava payload que não é objeto,
        # então a lista real do Certeza chegava vazia e o piloto parecia não
        # existir — sem erro nenhum na tela.
        bridge, _ = _bridge(_ok())
        nomes = [a.acao for a in bridge.acoes("certeza")]
        self.assertIn("ligar_piloto", nomes)
        self.assertIn("desligar_piloto", nomes)

    def test_acao_fora_da_lista_nao_sai_do_orbit(self) -> None:
        bridge, opener = _bridge(_ok())
        r = bridge.executar_acao("certeza", "apagar_tudo", confirmado=True)
        self.assertEqual(r.state, STATE_ACAO_DESCONHECIDA)
        self.assertIs(r.executou, False)
        self.assertEqual(_posts(opener), [])

    def test_nome_mal_formado_nem_le_a_lista(self) -> None:
        bridge, opener = _bridge(_ok())
        for nome in ("../apagar", "ligar piloto", "LIGAR_PILOTO", "a" * 60, ""):
            with self.subTest(nome=nome):
                r = bridge.executar_acao("certeza", nome, confirmado=True)
                self.assertEqual(r.state, STATE_ACAO_DESCONHECIDA)
        self.assertEqual(opener.requests, [])

    def test_lista_vazia_nao_autoriza_chutar_o_post(self) -> None:
        # É o estado do Orbix hoje: `acoes` devolve `[]` porque o POST dele
        # ainda não existe. Escrever às cegas aí seria inventar contrato.
        opener = FakeOpener(lambda req: FakeResponse(b"[]"))
        bridge = AppsBridge(token_store=FakeTokenStore(), opener=opener)
        r = bridge.executar_acao("orbix", "ligar_piloto", {"qual": "longo"}, confirmado=True)
        self.assertIs(r.executou, False)
        self.assertEqual(_posts(opener), [])
        self.assertEqual(r.state, STATE_ROUTE_MISSING)


class CorpoDoPedidoTests(unittest.TestCase):
    def test_manda_acao_params_e_confirmado(self) -> None:
        bridge, opener = _bridge(_ok())
        r = bridge.executar_acao("certeza", "ligar_piloto", {"qual": "longo"}, confirmado=True)
        self.assertTrue(r.ok)
        corpo = _corpo(_posts(opener)[0])
        self.assertEqual(corpo, {"acao": "ligar_piloto", "params": {"qual": "longo"}, "confirmado": True})

    def test_confirmado_nasce_falso(self) -> None:
        # O padrão nunca é permissão: quem não disse nada não disse "pode".
        bridge, opener = _bridge(FakeResponse(json.dumps(
            {"ok": False, "precisaConfirmar": True, "acao": "ligar_piloto", "descricao": "Liga o piloto"},
        ).encode("utf-8")))
        r = bridge.executar_acao("certeza", "ligar_piloto", {"qual": "longo"})
        self.assertFalse(_corpo(_posts(opener)[0])["confirmado"])
        self.assertEqual(r.state, STATE_PRECISA_CONFIRMAR)
        self.assertIs(r.executou, False)

    def test_token_vai_no_cabecalho_e_nunca_na_url(self) -> None:
        bridge, opener = _bridge(_ok())
        bridge.executar_acao("certeza", "desligar_piloto", {"qual": "shorts"}, confirmado=True)
        post = _posts(opener)[0]
        self.assertEqual(post.get_header(TOKEN_HEADER.capitalize()), "token-certeza")
        self.assertEqual(post.full_url, "http://127.0.0.1:3200/api/orbit/acao")
        self.assertNotIn("token", post.full_url)

    def test_sem_token_nao_dispara_pedido(self) -> None:
        bridge, opener = _bridge(_ok(), tokens={})
        r = bridge.executar_acao("certeza", "ligar_piloto", {"qual": "longo"}, confirmado=True)
        self.assertEqual(r.state, STATE_NEEDS_TOKEN)
        self.assertEqual(opener.requests, [])


class NaoSeiSeExecutouTests(unittest.TestCase):
    """O estado mais importante do módulo."""

    def test_timeout_nao_e_falha_e_nao_repete(self) -> None:
        erro = urllib.error.URLError(TimeoutError("tempo"))
        bridge, opener = _bridge(erro)
        r = bridge.executar_acao("certeza", "ligar_piloto", {"qual": "longo"}, confirmado=True)
        self.assertEqual(r.state, STATE_INCERTO)
        self.assertIsNone(r.executou)
        self.assertFalse(r.ok)
        self.assertIn("não sei se executou", r.frase())
        # Nenhuma repetição automática: um POST só.
        self.assertEqual(len(_posts(opener)), 1)

    def test_erro_de_rede_depois_do_envio_tambem_e_incerto(self) -> None:
        bridge, _ = _bridge(OSError("conexão caiu no meio"))
        r = bridge.executar_acao("certeza", "desligar_piloto", {"qual": "shorts"}, confirmado=True)
        self.assertEqual(r.state, STATE_INCERTO)
        self.assertIsNone(r.executou)

    def test_resposta_ilegivel_e_incerta_nao_falha_limpa(self) -> None:
        for resposta in (
            FakeResponse(b"{ nao e json"),
            FakeResponse(b'["lista"]'),
            FakeResponse(b"x" * (apps_bridge.MAX_RESPONSE_BYTES + 10)),
        ):
            with self.subTest(resposta=resposta):
                bridge, _ = _bridge(resposta)
                r = bridge.executar_acao("certeza", "ligar_piloto", {"qual": "longo"}, confirmado=True)
                self.assertEqual(r.state, STATE_INCERTO)
                self.assertIsNone(r.executou)

    def test_app_fechado_e_o_unico_nao_executou_com_certeza(self) -> None:
        bridge, _ = _bridge(urllib.error.URLError(ConnectionRefusedError("recusada")))
        r = bridge.executar_acao("certeza", "ligar_piloto", {"qual": "longo"}, confirmado=True)
        self.assertEqual(r.state, STATE_CLOSED)
        self.assertIs(r.executou, False)



if __name__ == "__main__":
    unittest.main()
