"""POST /api/webhooks/asaas: recebe eventos de cobrança do Asaas e dá baixa no VHSYS.

Autenticação: o Asaas envia o token cadastrado no webhook no header
asaas-access-token; ele precisa bater com ASAAS_WEBHOOK_TOKEN.

Respostas: 200 quando o evento foi tratado, inclusive quando não achou a receita
(reenviar não ajudaria); 500 em falha de comunicação com VHSYS/Asaas, para o
Asaas tentar de novo. Cada evento gera uma linha JSON nos Runtime Logs da Vercel.
"""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
import asaas_api  # noqa: E402
import baixa  # noqa: E402
import vhsys_api  # noqa: E402


def _autorizado(recebido):
    esperado = os.environ.get("ASAAS_WEBHOOK_TOKEN", "")
    return bool(esperado) and hmac.compare_digest(recebido.encode(), esperado.encode())


class handler(BaseHTTPRequestHandler):
    def _responder(self, status, corpo):
        dados = json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(dados)

    def do_POST(self):
        if not _autorizado(self.headers.get("asaas-access-token", "")):
            return self._responder(401, {"erro": "token inválido"})
        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
            evento = json.loads(self.rfile.read(tamanho) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._responder(400, {"erro": "JSON inválido"})
        try:
            resultado = baixa.processar_evento(evento, vhsys_api, asaas_api)
        except (vhsys_api.ErroVhsys, asaas_api.ErroAsaas) as e:
            print(json.dumps({"evento": evento.get("event"), "resultado": "erro",
                              "motivo": str(e)}, ensure_ascii=False))
            return self._responder(500, {"erro": "falha temporária, reenviar"})
        except Exception as e:  # resposta inesperada de uma API: registra e pede reenvio
            print(json.dumps({"evento": evento.get("event"), "resultado": "erro_inesperado",
                              "motivo": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
            return self._responder(500, {"erro": "erro inesperado, reenviar"})
        print(json.dumps(resultado, ensure_ascii=False))
        self._responder(200, resultado)

    def do_GET(self):
        self._responder(405, {"erro": "use POST"})
