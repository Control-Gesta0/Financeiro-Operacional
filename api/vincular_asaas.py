"""GET /api/vincular_asaas: liga as receitas migradas às cobranças que já existem no Asaas.

Uso (com a CRON_SECRET em ?chave=):
    ?chave=...            prévia: para cada receita em aberto na conta Asaas sem código do
                          Asaas, diz se há cobrança igual no Asaas (vinculavel, paga_no_asaas,
                          ambigua, sem_cobranca...)
    ?chave=...&aplicar=1  grava o vínculo só nas "vinculavel"
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))
import asaas_api  # noqa: E402
from autorizacao import autorizado_cron  # noqa: E402
import vhsys_api  # noqa: E402
import vinculo  # noqa: E402


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
            resultado = vinculo.analisar(vhsys_api, asaas_api, aplicar=aplicar)
        except (vhsys_api.ErroVhsys, asaas_api.ErroAsaas) as e:
            return self._responder(502, {"erro": str(e)})
        print(json.dumps({"vincular_asaas": resultado["resumo"], "aplicado": aplicar},
                         ensure_ascii=False))
        self._responder(200, resultado)
