"""GET /api/conciliacao: monta a conciliação Asaas × VHSYS de um dia e manda no WhatsApp.

Chamado pelo cron da Vercel às 07h (vercel.json), que envia
Authorization: Bearer <CRON_SECRET>. Para rodar à mão, use ?chave=<CRON_SECRET>.

Parâmetros: ?data=AAAA-MM-DD (padrão: ontem) · ?enviar=0 devolve o texto sem mandar.
Destino: CONCILIACAO_WHATSAPP (telefone com DDI ou JID de grupo).
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
import conciliar  # noqa: E402
import vhsys_api  # noqa: E402
import whatsapp  # noqa: E402


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
        data = (params.get("data") or [conciliar.ontem()])[0]
        try:
            dt.date.fromisoformat(data)
        except ValueError:
            return self._responder(400, {"erro": "data deve ser AAAA-MM-DD"})
        enviar = (params.get("enviar") or ["1"])[0] != "0"
        configurado = os.environ.get("CONCILIACAO_WHATSAPP", "")
        destino = whatsapp.normalizar(configurado)
        if enviar and not destino:
            return self._responder(400, {"erro": "CONCILIACAO_WHATSAPP inválido na Vercel: use 55 + "
                                                 "DDD + número, ex.: 5511987654321"})
        envio = {}
        try:
            relatorio = conciliar.montar(data, vhsys_api, asaas_api)
            mensagem = conciliar.texto(relatorio)
            enviado = False
            if enviar:
                numero = whatsapp.destino_verificado(destino)
                if not numero:
                    return self._responder(400, {"erro": f"o número {whatsapp.mascarar(destino)} "
                                                         "(CONCILIACAO_WHATSAPP) não tem WhatsApp",
                                                 "texto": mensagem})
                envio = {"destino": whatsapp.mascarar(numero),
                         **whatsapp.resumo_envio(whatsapp.enviar_texto(numero, mensagem))}
                enviado = True
        except (vhsys_api.ErroVhsys, asaas_api.ErroAsaas, whatsapp.ErroWhatsapp) as e:
            print(json.dumps({"conciliacao": data, "resultado": "erro", "motivo": str(e)},
                             ensure_ascii=False))
            return self._responder(502, {"erro": str(e)})
        except Exception as e:  # resposta inesperada de uma API: registra e devolve o erro
            motivo = f"{type(e).__name__}: {e}"
            print(json.dumps({"conciliacao": data, "resultado": "erro_inesperado", "motivo": motivo},
                             ensure_ascii=False))
            return self._responder(500, {"erro": motivo})
        print(json.dumps({"conciliacao": data, "enviado": enviado, "whatsapp": envio,
                          "recebimentos": len(relatorio["recebimentos"])}, ensure_ascii=False))
        self._responder(200, {"enviado": enviado, "whatsapp": envio, "texto": mensagem,
                              "relatorio": relatorio})
