"""GET /api/emitir: cria no Asaas as cobranças das receitas novas do ERP Lite.

Chamado pelo cron da Vercel a cada 15 minutos (vercel.json). Só grava quando
EMISSAO_MODO=ativo; antes disso, e em qualquer chamada manual sem aplicar=1, é prévia.

Depois de emitir, manda o boleto ao cliente pelo WhatsApp e pelo e-mail (etapa "boleto" de
cobranca_whatsapp: na hora para a cobrança única e a 1ª parcela; as demais parcelas
10 dias antes do vencimento), só com
WHATSAPP_COBRANCA_MODO=ativo e, no cron, em horário comercial.

Uso manual (com a CRON_SECRET em ?chave=):
    ?chave=...                    prévia: mostra o que seria emitido
    ?chave=...&desde=AAAA-MM-DD   prévia a partir de outra data de cadastro
    ?chave=...&aplicar=1          emite de verdade (exige EMISSAO_MODO=ativo)
    ?chave=...&diagnostico=1      prévia que lista também as receitas ignoradas e o motivo
    ?chave=...&receita=ID         prévia da sincronização dos campos de boleto de uma
                                  receita já emitida (com &aplicar=1 grava)
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))
import asaas_api  # noqa: E402
from autorizacao import autorizado_cron  # noqa: E402
import cobranca_whatsapp  # noqa: E402
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
        diagnostico = (params.get("diagnostico") or ["0"])[0] == "1"
        aplicar = emissao.modo() == "ativo" and (pediu_aplicar or do_cron) and not diagnostico
        desde = None if aplicar else (params.get("desde") or [None])[0]
        id_receita = (params.get("receita") or [""])[0]
        try:
            if id_receita:
                if not id_receita.isdigit():
                    return self._responder(400, {"erro": "receita deve ser o número da receita"})
                resultado = emissao.sincronizar_receita(int(id_receita), vhsys_api, asaas_api,
                                                        aplicar=aplicar)
                print(json.dumps({"sincronizar": resultado}, ensure_ascii=False))
                return self._responder(200, resultado)
            resultado = emissao.emitir(vhsys_api, asaas_api, aplicar=aplicar, desde=desde,
                                       diagnostico=diagnostico)
        except ValueError as e:
            return self._responder(400, {"erro": str(e)})
        except (vhsys_api.ErroVhsys, asaas_api.ErroAsaas) as e:
            print(json.dumps({"emissao": "erro", "motivo": str(e)}, ensure_ascii=False))
            return self._responder(502, {"erro": str(e)})
        except Exception as e:  # resposta inesperada de uma API: registra e devolve o erro
            motivo = f"{type(e).__name__}: {e}"
            print(json.dumps({"emissao": "erro_inesperado", "motivo": motivo}, ensure_ascii=False))
            return self._responder(500, {"erro": motivo})
        if not diagnostico:
            for canal in cobranca_whatsapp.canais_configurados():
                resultado[canal] = self._envio(canal, pediu_aplicar, do_cron, desde)
        print(json.dumps({"emissao": resultado["resumo"], "aplicado": aplicar,
                          **{c: resultado.get(c, {}).get("resumo")
                             for c in cobranca_whatsapp.CANAIS if c in resultado}},
                         ensure_ascii=False))
        self._responder(200, resultado)

    def _envio(self, canal, pediu_aplicar, do_cron, desde):
        """Envio do boleto ao cliente por um canal (whatsapp/email); um erro aqui não desfaz
        nem esconde a emissão."""
        enviar = cobranca_whatsapp.modo(canal) == "ativo" and (pediu_aplicar or do_cron)
        if do_cron and not enviar:
            return {"modo": "simulacao"}  # no cron, sem envio ligado, nem consulta
        if enviar and not cobranca_whatsapp.em_horario_comercial():
            return {"adiado": "fora do horário comercial; sai no primeiro cron das "
                              f"{cobranca_whatsapp.ENVIO_DAS}h"}
        try:
            return cobranca_whatsapp.cobrar(vhsys_api, asaas_api, ("boleto",), aplicar=enviar,
                                            desde=None if enviar else desde, canal=canal)
        except Exception as e:  # registra e segue: a emissão já foi feita
            motivo = f"{type(e).__name__}: {e}"
            print(json.dumps({canal: "erro", "motivo": motivo}, ensure_ascii=False))
            return {"erro": motivo}
