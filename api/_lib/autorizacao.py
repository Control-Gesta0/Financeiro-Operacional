"""Autorização das rotas internas (relatório, reprocessamento) pela CRON_SECRET."""
import hmac
import os


def autorizado_cron(headers, params):
    """Aceita Authorization: Bearer <CRON_SECRET> (cron da Vercel) ou ?chave=<CRON_SECRET>."""
    # strip: espaço ou quebra de linha colados junto com a senha (na Vercel ou no link)
    esperado = os.environ.get("CRON_SECRET", "").strip()
    recebido = (headers.get("Authorization") or "").removeprefix("Bearer ").strip() \
        or (params.get("chave") or [""])[0].strip()
    return bool(esperado) and hmac.compare_digest(recebido.encode(), esperado.encode())


def token_webhook_whatsapp():
    """Segredo da URL do webhook da Zaptos, derivado da CRON_SECRET (trocar a CRON_SECRET
    exige reconfigurar o webhook pela página /api/conectar_whatsapp)."""
    segredo = os.environ.get("CRON_SECRET", "").strip()
    if not segredo:
        return ""
    return hmac.new(segredo.encode(), b"webhook-whatsapp", "sha256").hexdigest()[:40]


def autorizado_webhook_whatsapp(params):
    esperado, recebido = token_webhook_whatsapp(), (params.get("chave") or [""])[0]
    return bool(esperado) and hmac.compare_digest(recebido.encode(), esperado.encode())
