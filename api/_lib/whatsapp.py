"""Envio pelo WhatsApp via Zaptos (API no mesmo formato da uazapi).

Configuração na Vercel: ZAPTOS_URL (ex.: https://api.zaptos.com.br, o servidor onde
está a instância) e ZAPTOS_TOKEN (token da instância, nunca o admintoken). Os nomes
antigos UAZAPI_URL e UAZAPI_TOKEN continuam valendo.
Documentação: https://docs.zaptos.com.br/
"""
import json
import os
import re
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
    resposta = _chamar("POST", caminho, corpo)
    # a Zaptos pode responder 200 e ainda assim não entregar: confere o corpo
    if isinstance(resposta, dict) and (resposta.get("error") or resposta.get("status") == "failed"):
        raise ErroWhatsapp(f"WhatsApp {caminho}: {resposta.get('error') or 'envio falhou'}")
    return resposta


def normalizar(numero):
    """Telefone em só dígitos com DDI 55 (aceita espaços, +, parênteses, traço e o 0 da
    operadora/DDD), JID de grupo como está, ou None se não parecer um número válido."""
    texto = str(numero or "").strip()
    if texto.endswith("@g.us"):
        return texto
    digitos = re.sub(r"\D", "", texto).lstrip("0")
    if len(digitos) in (10, 11):
        digitos = "55" + digitos
    return digitos if digitos.startswith("55") and len(digitos) in (12, 13) else None


def mascarar(numero):
    numero = str(numero or "")
    return numero if numero.endswith("@g.us") else (numero[:4] + "*" * (len(numero) - 8) + numero[-4:])


def destino_verificado(numero):
    """Confere na Zaptos (/chat/check) se o número tem WhatsApp e devolve o número como o
    WhatsApp o conhece (no Brasil, contas antigas não têm o nono dígito), ou None."""
    if numero.endswith("@g.us"):
        return numero
    resposta = _chamar("POST", "/chat/check", {"numbers": [numero]})
    item = resposta[0] if isinstance(resposta, list) and resposta else {}
    if not item.get("isInWhatsapp"):
        return None
    jid = str(item.get("jid") or "")
    return jid.split("@")[0].split(":")[0] if jid else numero


def resumo_envio(resposta):
    """Campos úteis da resposta de um envio (para o relatório e os logs)."""
    resposta = resposta if isinstance(resposta, dict) else {}
    return {k: v for k, v in {"status": resposta.get("status"),
                              "mensagem": resposta.get("messageid") or resposta.get("id"),
                              "retorno": (resposta.get("response") or {}).get("message")
                              if isinstance(resposta.get("response"), dict) else None}.items() if v}


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


# ------------------------------------------------------------------ atendimento
def detalhes_chat(numero):
    """Conversa na Zaptos (campos lead_*, chatbot_disableUntil...)."""
    return _chamar("POST", "/chat/details", {"number": numero, "preview": True}) or {}


def editar_chat(chatid, campos):
    """Grava campos da conversa (ex.: chatbot_disableUntil, lead_field19)."""
    return _chamar("POST", "/chat/editLead", {"id": chatid, **campos})


def historico(chatid, limite=10):
    resposta = _chamar("POST", "/message/find", {"chatid": chatid, "limit": limite}) or {}
    return resposta.get("messages") or [] if isinstance(resposta, dict) else []


def ver_webhook():
    resposta = _chamar("GET", "/webhook")
    return resposta if isinstance(resposta, list) else []


def configurar_webhook(url):
    """Webhook único da instância: só mensagens, sem as enviadas pela API (evita loop) e
    sem grupos."""
    return _chamar("POST", "/webhook", {"url": url, "events": ["messages"], "enabled": True,
                                        "excludeMessages": ["wasSentByApi", "isGroupYes"]})
