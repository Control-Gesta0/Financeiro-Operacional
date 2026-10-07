"""GET /api/desligar_avisos_asaas: desliga os avisos do Asaas para os clientes antigos.

Uso (com a CRON_SECRET em ?chave=):
    ?chave=...            prévia: quantos clientes ainda recebem avisos do Asaas
    ?chave=...&aplicar=1  desliga em lotes de 60; repita até "faltam": 0

Cada lote registra nos Runtime Logs os ids alterados (para religar, se precisar,
com notificationDisabled=false nesses clientes).
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))
import asaas_api  # noqa: E402
from autorizacao import autorizado_cron  # noqa: E402
import avisos  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _responder(self, status, corpo):
        dados = json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if not autorizado_cron(self.headers, params):
            return self._responder(401, {"erro": "não autorizado"})
        aplicar = (params.get("aplicar") or ["0"])[0] == "1"
        try:
            resultado = avisos.desligar(asaas_api, aplicar=aplicar)
        except asaas_api.ErroAsaas as e:
            return self._responder(502, {"erro": str(e)})
        print(json.dumps({"avisos_asaas": {k: v for k, v in resultado.items() if k != "exemplos"}},
                         ensure_ascii=False))
        self._responder(200, resultado)
