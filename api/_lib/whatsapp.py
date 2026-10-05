"""Envio pelo WhatsApp via Zaptos (API no mesmo formato da uazapi).

Configuração na Vercel: ZAPTOS_URL (ex.: https://api.zaptoswpp.com/whatsapp, o servidor
onde está a instância) e ZAPTOS_TOKEN (token da instância). Os nomes antigos
UAZAPI_URL e UAZAPI_TOKEN continuam valendo.
Documentação: https://zaptoswpp-api-ptbr.apidocumentation.com/zaptoswpp-api-pt-br
"""
import json
import os
import urllib.error
import urllib.request


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


def _post(caminho, corpo):
    url, token = _url(), _token()
    if not (url and token and corpo.get("number")):
        raise ErroWhatsapp("ZAPTOS_URL, ZAPTOS_TOKEN ou destino não configurados")
    req = urllib.request.Request(
        f"{url}{caminho}", method="POST", data=json.dumps(corpo).encode(),
        headers={"token": token, "Content-Type": "application/json"})
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


def enviar_texto(destino, texto):
    """destino: telefone com DDI (5511999999999) ou JID de grupo (...@g.us)."""
    return _post("/send/text", {"number": destino, "text": texto})


def enviar_documento(destino, url_arquivo, nome_arquivo, legenda=None):
    """Documento (ex.: PDF do boleto) por URL, com legenda opcional (/send/media)."""
    corpo = {"number": destino, "type": "document", "file": url_arquivo, "docName": nome_arquivo}
    if legenda:
        corpo["text"] = legenda
    return _post("/send/media", corpo)
