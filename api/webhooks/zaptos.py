"""POST /api/webhooks/zaptos?chave=...: mensagens recebidas no WhatsApp do financeiro (Zaptos).

O webhook é cadastrado na Zaptos pela página /api/conectar_whatsapp (&webhook=1), com a
chave derivada da CRON_SECRET na URL. Cada mensagem passa pelo atendimento automático
(api/_lib/atendimento.py), que só age com ATENDIMENTO_MODO=teste ou ativo.

Responde sempre 200 depois de tratar (inclusive em erro): se a Zaptos reenviasse, o
cliente poderia receber a resposta duas vezes; a mensagem continua na conversa para a
equipe. Cada evento gera uma linha JSON nos Runtime Logs da Vercel, sem o texto.
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
import asaas_api  # noqa: E402
import atendimento  # noqa: E402
from autorizacao import autorizado_webhook_whatsapp  # noqa: E402
import vhsys_api  # noqa: E402

LIMITE_CORPO = 1_000_000


class handler(BaseHTTPRequestHandler):
    def _responder(self, status, corpo):
        dados = json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(dados)

    def do_POST(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if not autorizado_webhook_whatsapp(params):
            return self._responder(401, {"erro": "não autorizado"})
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > LIMITE_CORPO:
            return self._responder(413, {"erro": "corpo grande demais"})
        try:
            payload = json.loads(self.rfile.read(tamanho) or b"{}")
        except json.JSONDecodeError:
            return self._responder(400, {"erro": "JSON inválido"})
        if not isinstance(payload, dict):
            return self._responder(400, {"erro": "JSON inválido"})
        try:
            resultado = atendimento.processar(payload, vhsys_api, asaas_api)
        except Exception as e:  # registra e não pede reenvio (ver docstring)
            resultado = {"acao": "erro", "motivo": f"{type(e).__name__}: {e}"}
        msg = payload.get("message") or {}
        print(json.dumps({"whatsapp": resultado.get("acao"), "assunto": resultado.get("assunto"),
                          "motivo": resultado.get("motivo"), "falhas": resultado.get("falhas"),
                          "campos": sorted(payload)[:15], "tipo": msg.get("messageType")},
                         ensure_ascii=False))
        self._responder(200, resultado)
