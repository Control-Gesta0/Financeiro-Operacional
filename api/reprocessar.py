"""GET /api/reprocessar: aplica a baixa automática aos recebimentos de um dia.

Uso (com a CRON_SECRET em ?chave= ou em Authorization: Bearer):
    ?data=AAAA-MM-DD            prévia: mostra o que faria, sem gravar
    ?data=AAAA-MM-DD&aplicar=1  grava no VHSYS (exige BAIXA_MODO=ativo)
"""
import datetime as dt
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))
import asaas_api  # noqa: E402
from autorizacao import autorizado_cron  # noqa: E402
import baixa  # noqa: E402
import reprocessamento  # noqa: E402
import vhsys_api  # noqa: E402


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
        data = (params.get("data") or [""])[0]
        try:
            dt.date.fromisoformat(data)
        except ValueError:
            return self._responder(400, {"erro": "informe ?data=AAAA-MM-DD"})
        aplicar = (params.get("aplicar") or ["0"])[0] == "1"
        if aplicar and baixa.modo() != "ativo":
            return self._responder(409, {"erro": "a baixa está em simulação: crie BAIXA_MODO=ativo "
                                                 "na Vercel antes de aplicar"})
        try:
            resultado = reprocessamento.reprocessar(data, vhsys_api, asaas_api, aplicar)
        except (vhsys_api.ErroVhsys, asaas_api.ErroAsaas) as e:
            print(json.dumps({"reprocessar": data, "resultado": "erro", "motivo": str(e)},
                             ensure_ascii=False))
            return self._responder(502, {"erro": str(e)})
        print(json.dumps({"reprocessar": data, "aplicado": aplicar,
                          "resumo": resultado["resumo"]}, ensure_ascii=False))
        self._responder(200, resultado)
