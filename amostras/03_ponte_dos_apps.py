# Origem: src/orbit/apps_bridge.py (trechos; partes longas marcadas com "# ...").
# Mostra: o único cliente HTTP do Orbit para os outros apps. Só loopback, rotas
# de uma lista fechada, token no cabeçalho, ação conferida contra a lista que o
# PRÓPRIO app declara, parâmetros validados pelo tipo declarado, um POST só, e
# timeout tratado como "não sei se executou" (nunca como falha limpa).

"""Regra de nascença do Orbit preservada: isto fala HTTP com apps que já estão
rodando; não executa shell, não abre processo e não escreve no banco de app
nenhum. O Orbit não altera dado de app; ele pede, o app decide e o app executa.

Todo byte que volta dos apps é tratado como DADO, nunca como instrução: texto é
limpo de caracteres de controle e truncado antes de chegar à fala ou à tela.
"""

# Só loopback. Constante, não parâmetro: não existe caminho de código que
# aponte este cliente para outra máquina.
LOOPBACK_HOST = "127.0.0.1"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_REQUEST_BYTES = 8 * 1024
ACTION_TIMEOUT_S = 10.0
TOKEN_HEADER = "X-Orbit-Token"

# Lista fechada de rotas de leitura.
ROUTE_RESUMO = "/api/orbit/resumo"
ROUTE_OCUPADO = "/api/ocupado"
ROUTE_HEALTH = "/api/health"
ROUTE_ACOES = "/api/orbit/acoes"
# ...
ALLOWED_ROUTES = frozenset({ROUTE_RESUMO, ROUTE_OCUPADO, ROUTE_HEALTH, ROUTE_ACOES})  # ...

# Rota de ESCRITA, em lista própria e disjunta da de leitura. Separadas para que
# `_read` não consiga nem montar a URL de escrita.
ROUTE_ACAO = "/api/orbit/acao"
ROTAS_DE_ESCRITA = frozenset({ROUTE_ACAO})

# `STATE_INCERTO` é o estado mais importante da escrita: um POST que estourou o
# tempo pode ter executado, e chamar isso de falha faria o dono mandar de novo —
# ligando o piloto duas vezes.
STATE_INCERTO = "incerto"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Recusa redirecionamento: um app local redirecionando o Orbit para outro
    endereço sairia do registro fixo sem passar por validação nenhuma."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


# Sem proxy: no Windows `getproxies()` lê o proxy do registro e um proxy de
# sistema engoliria a chamada ao 127.0.0.1.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler())


def build_local_url(slug: str, route: str, *, escrita: bool = False) -> str:
    """Única construtora de URL da ponte. Rota fora da lista fechada falha."""
    endpoint = _endpoint(slug)  # registro fixo: slug -> porta; desconhecido levanta erro
    permitidas = ROTAS_DE_ESCRITA if escrita else ALLOWED_ROUTES
    if route not in permitidas:
        raise ValueError("Rota fora da lista permitida da ponte.")
    return f"http://{LOOPBACK_HOST}:{endpoint.port}{route}"


def _params_validos(declarada: "AppAction", params: dict) -> dict | None:
    """Confere os parâmetros que o Orbit vai enviar. `None` recusa o POST.

    Quando o app declara o tipo, o tipo é cobrado (inclusive a lista do
    `enum:`). Quando não declara, o Orbit ainda assim só aceita escalar curto,
    e poucos: a forma é cobrada sempre, a declaração quando existe.
    """
    if not isinstance(params, dict) or len(params) > 4:
        return None
    limpos: dict[str, str | int | float | bool] = {}
    for chave, valor in params.items():
        if not isinstance(chave, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,32}", chave):
            return None
        declarado = declarada.params.get(chave, "")
        if isinstance(valor, bool):
            if declarado and declarado != "boolean":
                return None
            limpos[chave] = valor
            continue
        if isinstance(valor, (int, float)):
            if valor != valor or valor in (float("inf"), float("-inf")):
                return None
            if declarado and declarado != "number":
                return None
            limpos[chave] = valor
            continue
        if isinstance(valor, str):
            texto = _clean_text(valor, 64)  # ... (limite maior para dois casos específicos)
            if not texto:
                return None
            if declarado.startswith("enum:"):
                if texto not in declarado[5:].split("|"):
                    return None
            elif declarado and declarado != "string":
                return None
            limpos[chave] = texto
            continue
        return None  # lista, objeto, None: fora do contrato
    return limpos


class AppsBridge:
    """`opener` e `token_store` são injetados para o teste não tocar em socket
    real nem no Gerenciador de Credenciais."""

    def __init__(self, token_store: "AppTokenStore | None" = None, opener=None) -> None:
        self.tokens = token_store or AppTokenStore()
        self._opener = opener or _OPENER

    def executar_acao(
        self, slug: str, acao: str, params: dict | None = None, *, confirmado: bool = False,
    ) -> "AppActionResult":
        """Pede uma ação ao app, validando contra a lista que ELE declara.

        A lista é relida agora, não guardada: se o dono desligou algo no app
        entre a abertura da tela e o clique, quem manda é a resposta de agora.
        Ação fora da lista nem sai do Orbit — e nem monta URL.
        """
        endpoint = _endpoint(slug)
        nome = (acao or "").strip()
        if not re.fullmatch(r"[a-z0-9_]{1,48}", nome):
            return AppActionResult(
                slug, endpoint.label, nome[:48], STATE_ACAO_DESCONHECIDA, executou=False,
                detail="nome de ação fora do formato aceito",
            )

        declaradas = {item.acao: item for item in self.acoes(slug)}
        if not declaradas:
            # Não conseguir ler a lista não autoriza chutar o POST.
            # ...
            return AppActionResult(slug, endpoint.label, nome, STATE_ROUTE_MISSING, executou=False)

        declarada = declaradas.get(nome)
        if declarada is None:
            return AppActionResult(
                slug, endpoint.label, nome, STATE_ACAO_DESCONHECIDA, executou=False,
                detail="esta ação não está na lista que o app declara",
            )

        limpos = _params_validos(declarada, params or {})
        if limpos is None:
            return AppActionResult(
                slug, endpoint.label, nome, STATE_ACAO_DESCONHECIDA, executou=False,
                detail="parâmetro fora do que o app declara aceitar",
            )
        return self._write(slug, nome, limpos, confirmado)

    def _write(self, slug: str, acao: str, params: dict, confirmado: bool) -> "AppActionResult":
        """O único caminho de escrita do Orbit para outro app.

        - Token sempre obrigatório.
        - Nenhuma repetição automática. Um POST que estourou o tempo pode ter
          executado do outro lado. Repetir ligaria o piloto duas vezes.
        - `executou` pode ser `None`: timeout não é "não fez", é "não sei se fez".
        """
        endpoint = _endpoint(slug)
        token = self.tokens.load(slug)
        if token is None:
            return AppActionResult(
                slug, endpoint.label, acao, STATE_NEEDS_TOKEN, executou=False,
                detail="token ainda não foi colado em Configurações",
            )

        corpo = json.dumps(
            # O `confirmado` do protocolo nasce aqui e em nenhum outro lugar.
            # Quem decide o valor é o fluxo de confirmação da interface.
            {"acao": acao, "params": params, "confirmado": bool(confirmado)},
            ensure_ascii=False,
        ).encode("utf-8")
        if len(corpo) > MAX_REQUEST_BYTES:
            return AppActionResult(slug, endpoint.label, acao, STATE_ERROR, executou=False,
                                   detail="pedido maior que o limite")

        request = urllib.request.Request(
            build_local_url(slug, ROUTE_ACAO, escrita=True),
            data=corpo,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "Orbit",
                TOKEN_HEADER: token,  # cabeçalho, nunca query string
            },
            method="POST",
        )

        try:
            with self._opener.open(request, timeout=ACTION_TIMEOUT_S) as response:
                content_type = (response.headers.get("Content-Type") or "").casefold()
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            # ... 404 em JSON = ação desconhecida; 404 em HTML = rota não existe;
            # 401/403 = token recusado; resto = erro com a mensagem do app.
            ...
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                return AppActionResult(
                    slug, endpoint.label, acao, STATE_INCERTO, executou=None,
                    detail="o app não respondeu a tempo, e eu não sei se executou",
                )
            # Conexão recusada: o único caso em que é CERTO que não executou.
            return AppActionResult(slug, endpoint.label, acao, STATE_CLOSED, executou=False,
                                   detail=_clean_text(str(exc.reason), 80))
        except OSError as exc:
            # Falha de rede depois de o pedido sair também é incerta.
            return AppActionResult(slug, endpoint.label, acao, STATE_INCERTO, executou=None,
                                   detail=_clean_text(str(exc), 80))

        # ... resposta grande demais, não-JSON ou JSON inválido: também INCERTO.

        # O app pode devolver 200 dizendo "isto precisa de confirmação". Não é
        # erro: é o app recusando executar sem o "pode" do dono.
        if payload.get("precisaConfirmar") is True:
            return AppActionResult(
                slug, endpoint.label, acao, STATE_PRECISA_CONFIRMAR, executou=False,
                detail=_clean_text(payload.get("descricao"), 240),
                payload=payload,
            )
        return AppActionResult(slug, endpoint.label, acao, STATE_OK, executou=True,
                               detail=_clean_text(payload.get("detalhe") or payload.get("mensagem"), 240),
                               payload=payload)
