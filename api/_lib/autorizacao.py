"""Autorização das rotas internas (relatório, reprocessamento) pela CRON_SECRET."""
import hmac
import os


def autorizado_cron(headers, params):
    """Aceita Authorization: Bearer <CRON_SECRET> (cron da Vercel) ou ?chave=<CRON_SECRET>."""
    esperado = os.environ.get("CRON_SECRET", "")
    recebido = (headers.get("Authorization") or "").removeprefix("Bearer ").strip() \
        or (params.get("chave") or [""])[0]
    return bool(esperado) and hmac.compare_digest(recebido.encode(), esperado.encode())
