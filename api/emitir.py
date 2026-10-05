"""GET /api/emitir: cria no Asaas as cobranças das receitas novas do ERP Lite.

Chamado pelo cron da Vercel a cada 15 minutos (vercel.json). Só grava quando
EMISSAO_MODO=ativo; antes disso, e em qualquer chamada manual sem aplicar=1, é prévia.

Uso manual (com a CRON_SECRET em ?chave=):
    ?chave=...                    prévia: mostra o que seria emitido
    ?chave=...&desde=AAAA-MM-DD   prévia a partir de outra data de cadastro
    ?chave=...&aplicar=1          emite de verdade (exige EMISSAO_MODO=ativo)
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))
import asaas_api  # noqa: E402
from autorizacao import autorizado_cron  # noqa: E402
import emissao  # noqa: E402
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
        pediu_aplicar = (params.get("aplicar") or ["0"])[0] == "1"
        do_cron = (self.headers.get("Authorization") or "").startswith("Bearer ")
        if pediu_aplicar and emissao.modo() != "ativo":
            return self._responder(409, {"erro": "a emissão está em simulação: crie "
                                                 "EMISSAO_MODO=ativo na Vercel antes de aplicar"})
        aplicar = emissao.modo() == "ativo" and (pediu_aplicar or do_cron)
        desde = None if aplicar else (params.get("desde") or [None])[0]
        try:
            resultado = emissao.emitir(vhsys_api, asaas_api, aplicar=aplicar, desde=desde)
        except ValueError as e:
            return self._responder(400, {"erro": str(e)})
        except (vhsys_api.ErroVhsys, asaas_api.ErroAsaas) as e:
            print(json.dumps({"emissao": "erro", "motivo": str(e)}, ensure_ascii=False))
            return self._responder(502, {"erro": str(e)})
        except Exception as e:  # resposta inesperada de uma API: registra e devolve o erro
            motivo = f"{type(e).__name__}: {e}"
            print(json.dumps({"emissao": "erro_inesperado", "motivo": motivo}, ensure_ascii=False))
            return self._responder(500, {"erro": motivo})
        print(json.dumps({"emissao": resultado["resumo"], "aplicado": aplicar},
                         ensure_ascii=False))
        self._responder(200, resultado)
