"""Envio de texto pelo WhatsApp via uazapi (mesmo transporte dos alertas dos agentes)."""
import json
import os
import urllib.error
import urllib.request


class ErroWhatsapp(Exception):
    pass


def enviar_texto(destino, texto):
    """destino: telefone com DDI (5511999999999) ou JID de grupo (...@g.us)."""
    url, token = os.environ.get("UAZAPI_URL", "").rstrip("/"), os.environ.get("UAZAPI_TOKEN")
    if not (url and token and destino):
        raise ErroWhatsapp("UAZAPI_URL, UAZAPI_TOKEN ou destino não configurados")
    req = urllib.request.Request(
        f"{url}/send/text", method="POST",
        data=json.dumps({"number": destino, "text": texto}).encode(),
        headers={"token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        raise ErroWhatsapp(f"uazapi HTTP {e.code} {e.read()[:200]!r}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ErroWhatsapp(f"uazapi: {e}") from e
