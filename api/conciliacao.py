"""GET /api/conciliacao: monta a conciliação Asaas × VHSYS de um dia e manda no WhatsApp.

Chamado pelo cron da Vercel às 07h (vercel.json), que envia
Authorization: Bearer <CRON_SECRET>. Para rodar à mão, use ?chave=<CRON_SECRET>.

Parâmetros: ?data=AAAA-MM-DD (padrão: ontem) · ?enviar=0 devolve o texto sem mandar.
Destino: CONCILIACAO_WHATSAPP (telefone com DDI ou JID de grupo).
"""
import datetime as dt
import hmac
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))
import asaas_api  # noqa: E402
import conciliar  # noqa: E402
import vhsys_api  # noqa: E402
import whatsapp  # noqa: E402


def _autorizado(headers, params):
    esperado = os.environ.get("CRON_SECRET", "")
    recebido = (headers.get("Authorization") or "").removeprefix("Bearer ").strip() \
        or (params.get("chave") or [""])[0]
    return bool(esperado) and hmac.compare_digest(recebido.encode(), esperado.encode())


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
        if not _autorizado(self.headers, params):
            return self._responder(401, {"erro": "não autorizado"})
        data = (params.get("data") or [conciliar.ontem()])[0]
        try:
            dt.date.fromisoformat(data)
        except ValueError:
            return self._responder(400, {"erro": "data deve ser AAAA-MM-DD"})
        try:
            relatorio = conciliar.montar(data, vhsys_api, asaas_api)
            mensagem = conciliar.texto(relatorio)
            enviado = False
            if (params.get("enviar") or ["1"])[0] != "0":
                whatsapp.enviar_texto(os.environ.get("CONCILIACAO_WHATSAPP", ""), mensagem)
                enviado = True
        except (vhsys_api.ErroVhsys, asaas_api.ErroAsaas, whatsapp.ErroWhatsapp) as e:
            print(json.dumps({"conciliacao": data, "resultado": "erro", "motivo": str(e)},
                             ensure_ascii=False))
            return self._responder(502, {"erro": str(e)})
        print(json.dumps({"conciliacao": data, "enviado": enviado,
                          "recebimentos": len(relatorio["recebimentos"])}, ensure_ascii=False))
        self._responder(200, {"enviado": enviado, "texto": mensagem, "relatorio": relatorio})
