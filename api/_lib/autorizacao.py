"""Autorização das rotas internas (relatório, reprocessamento) pela CRON_SECRET."""
import hmac
import os


def autorizado_cron(headers, params):
    """Aceita Authorization: Bearer <CRON_SECRET> (cron da Vercel) ou ?chave=<CRON_SECRET>."""
    esperado = os.environ.get("CRON_SECRET", "")
    recebido = (headers.get("Authorization") or "").removeprefix("Bearer ").strip() \
        or (params.get("chave") or [""])[0]
    return bool(esperado) and hmac.compare_digest(recebido.encode(), esperado.encode())


def token_webhook_whatsapp():
    """Segredo da URL do webhook da Zaptos, derivado da CRON_SECRET (trocar a CRON_SECRET
    exige reconfigurar o webhook pela página /api/conectar_whatsapp)."""
    segredo = os.environ.get("CRON_SECRET", "")
    if not segredo:
        return ""
    return hmac.new(segredo.encode(), b"webhook-whatsapp", "sha256").hexdigest()[:40]


def autorizado_webhook_whatsapp(params):
    esperado, recebido = token_webhook_whatsapp(), (params.get("chave") or [""])[0]
    return bool(esperado) and hmac.compare_digest(recebido.encode(), esperado.encode())
