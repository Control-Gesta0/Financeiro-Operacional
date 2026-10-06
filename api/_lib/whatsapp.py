"""Envio pelo WhatsApp via Zaptos (API no mesmo formato da uazapi).

Configuração na Vercel: ZAPTOS_URL (ex.: https://api.zaptos.com.br, o servidor onde
está a instância) e ZAPTOS_TOKEN (token da instância, nunca o admintoken). Os nomes
antigos UAZAPI_URL e UAZAPI_TOKEN continuam valendo.
Documentação: https://docs.zaptos.com.br/
"""
import json
import os
import urllib.error
import urllib.request

# A Zaptos fica atrás do Cloudflare, que recusa o User-Agent padrão do Python
# ("Python-urllib/...") com HTTP 403 "error code: 1010".
USER_AGENT = "FinanceiroOperacional/1.0"


class ErroWhatsapp(Exception):
    def __init__(self, mensagem, status=None):
        super().__init__(mensagem)
        self.status = status  # código HTTP quando a API respondeu (400 = pedido recusado)


def configurado():
    return bool(_url() and _token())


def _url():
    return (os.environ.get("ZAPTOS_URL") or os.environ.get("UAZAPI_URL") or "").rstrip("/")


def _token():
    return os.environ.get("ZAPTOS_TOKEN") or os.environ.get("UAZAPI_TOKEN")


def _chamar(metodo, caminho, corpo=None):
    url, token = _url(), _token()
    if not (url and token):
        raise ErroWhatsapp("ZAPTOS_URL ou ZAPTOS_TOKEN não configurados")
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(f"{url}{caminho}", method=metodo, data=dados,
                                 headers={"token": token, "Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resposta = r.read()
    except urllib.error.HTTPError as e:
        raise ErroWhatsapp(f"WhatsApp {caminho}: HTTP {e.code} {e.read()[:200]!r}", e.code) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ErroWhatsapp(f"WhatsApp {caminho}: {e}") from e
    try:
        return json.loads(resposta or b"{}")
    except json.JSONDecodeError:
        return {}


def _post(caminho, corpo):
    if not corpo.get("number"):
        raise ErroWhatsapp("destino do WhatsApp não configurado")
    return _chamar("POST", caminho, corpo)


def status():
    """{"instance": {status, qrcode, paircode, profileName, ...}, "status": {connected, jid}}."""
    return _chamar("GET", "/instance/status")


def conectar():
    """Inicia a conexão por QR Code (válido por cerca de 2 minutos)."""
    return _chamar("POST", "/instance/connect", {})


def enviar_texto(destino, texto):
    """destino: telefone com DDI (5511999999999) ou JID de grupo (...@g.us)."""
    return _post("/send/text", {"number": destino, "text": texto})


def enviar_documento(destino, url_arquivo, nome_arquivo, legenda=None):
    """Documento (ex.: PDF do boleto) por URL, com legenda opcional (/send/media)."""
    corpo = {"number": destino, "type": "document", "file": url_arquivo, "docName": nome_arquivo}
    if legenda:
        corpo["text"] = legenda
    return _post("/send/media", corpo)
