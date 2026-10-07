"""GET /api/saude: confirma que o deploy está no ar e quais credenciais estão configuradas.

Nunca devolve os valores, só se cada variável existe. Serve para conferir o
cadastro das variáveis de ambiente na Vercel antes de ligar o webhook.
"""
import json
import os
from http.server import BaseHTTPRequestHandler

VARIAVEIS = ("VHSYS_ACCESS_TOKEN", "VHSYS_SECRET_ACCESS_TOKEN", "ASAAS_API_KEY",
             "ASAAS_WEBHOOK_TOKEN", "CRON_SECRET", "ZAPTOS_URL", "ZAPTOS_TOKEN",
             "UAZAPI_URL", "UAZAPI_TOKEN", "CONCILIACAO_WHATSAPP", "ANTHROPIC_API_KEY",
             "SMTP_HOST", "SMTP_USUARIO", "SMTP_SENHA")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        corpo = json.dumps({
            "ok": True,
            "baixa_modo": "ativo" if os.environ.get("BAIXA_MODO", "").strip().lower() == "ativo"
            else "simulacao",
            "emissao_modo": "ativo" if os.environ.get("EMISSAO_MODO", "").strip().lower() == "ativo"
            else "simulacao",
            "emissao_a_partir_de": os.environ.get("EMISSAO_A_PARTIR_DE") or None,
            "whatsapp_cobranca_modo": "ativo" if os.environ.get(
                "WHATSAPP_COBRANCA_MODO", "").strip().lower() == "ativo" else "simulacao",
            "email_cobranca_modo": "ativo" if os.environ.get(
                "EMAIL_COBRANCA_MODO", "").strip().lower() == "ativo" else "simulacao",
            "atendimento_modo": (os.environ.get("ATENDIMENTO_MODO", "").strip().lower()
                                 if os.environ.get("ATENDIMENTO_MODO", "").strip().lower()
                                 in ("teste", "ativo") else "desligado"),
            "credenciais": {v: bool(os.environ.get(v)) for v in VARIAVEIS},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)
