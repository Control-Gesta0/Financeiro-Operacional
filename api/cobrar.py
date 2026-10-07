"""GET /api/cobrar: lembretes de cobrança pelo WhatsApp (dia do vencimento e atraso).

Chamado pelo cron da Vercel às 10h de Brasília (vercel.json). Só envia com
WHATSAPP_COBRANCA_MODO=ativo; antes disso, e em chamada manual sem aplicar=1, é prévia.
O envio do boleto logo após a emissão fica no cron de /api/emitir.

Uso manual (com a CRON_SECRET em ?chave=):
    ?chave=...                          prévia dos lembretes de hoje
    ?chave=...&data=AAAA-MM-DD          prévia como se fosse outro dia
    ?chave=...&aplicar=1                envia os lembretes de hoje
    ?chave=...&receita=ID&etapa=boleto  prévia de uma receita (etapa boleto, vencimento
                                        ou atraso); com &aplicar=1 envia, e com
                                        &reenviar=1 manda de novo uma etapa já enviada;
                                        &canal=email para o e-mail (padrão: whatsapp)

Os lembretes saem por todos os canais configurados (WhatsApp e, com SMTP na Vercel,
e-mail), cada um só com o seu modo ativo (WHATSAPP_COBRANCA_MODO / EMAIL_COBRANCA_MODO).
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
import cobranca_whatsapp  # noqa: E402
import correio  # noqa: E402
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
        p = {k: v[0] for k, v in params.items()}
        if not autorizado_cron(self.headers, params):
            return self._responder(401, {"erro": "não autorizado"})
        pediu_aplicar = p.get("aplicar") == "1"
        do_cron = (self.headers.get("Authorization") or "").startswith("Bearer ")
        canal = p.get("canal") or "whatsapp"
        if canal not in cobranca_whatsapp.CANAIS:
            return self._responder(400, {"erro": "canal deve ser whatsapp ou email"})
        canais = [canal] if p.get("receita") else cobranca_whatsapp.canais_configurados()
        ativos = [c for c in canais if cobranca_whatsapp.modo(c) == "ativo"]
        if pediu_aplicar and not ativos:
            return self._responder(409, {"erro": "a cobrança está em simulação: crie "
                                                 "WHATSAPP_COBRANCA_MODO=ativo (ou "
                                                 "EMAIL_COBRANCA_MODO=ativo) na Vercel antes "
                                                 "de aplicar"})
        aplicar = bool(ativos) and (pediu_aplicar or do_cron)
        try:
            hoje = dt.date.fromisoformat(p["data"]) if p.get("data") else None
        except ValueError:
            return self._responder(400, {"erro": "data deve ser AAAA-MM-DD"})
        if hoje and aplicar:
            return self._responder(400, {"erro": "data é só para prévia"})
        try:
            if p.get("receita"):
                resultado = self._uma_receita(p, aplicar, hoje, canal)
            else:
                resultado = {c: cobranca_whatsapp.cobrar(
                    vhsys_api, asaas_api, ("vencimento", "atraso"),
                    aplicar=aplicar and c in ativos, hoje=hoje, canal=c) for c in canais}
        except ValueError as e:
            return self._responder(400, {"erro": str(e)})
        except (vhsys_api.ErroVhsys, asaas_api.ErroAsaas, whatsapp.ErroWhatsapp,
                correio.ErroEmail) as e:
            print(json.dumps({"cobrar": "erro", "motivo": str(e)}, ensure_ascii=False))
            return self._responder(502, {"erro": str(e)})
        except Exception as e:  # resposta inesperada de uma API: registra e devolve o erro
            motivo = f"{type(e).__name__}: {e}"
            print(json.dumps({"cobrar": "erro_inesperado", "motivo": motivo}, ensure_ascii=False))
            return self._responder(500, {"erro": motivo})
        resumo = (resultado.get("resultado") if p.get("receita")
                  else {c: r.get("resumo") for c, r in resultado.items()})
        print(json.dumps({"cobrar": resumo, "aplicado": aplicar}, ensure_ascii=False))
        self._responder(200, resultado)

    def _uma_receita(self, p, aplicar, hoje, canal):
        if not p["receita"].isdigit():
            raise ValueError("receita deve ser o número da receita")
        etapa = p.get("etapa") or "boleto"
        if etapa not in cobranca_whatsapp.ETAPAS:
            raise ValueError(f"etapa deve ser uma de: {', '.join(cobranca_whatsapp.ETAPAS)}")
        receita = vhsys_api.consultar_receita(int(p["receita"]))
        if not receita:
            raise ValueError(f"receita {p['receita']} não encontrada no ERP Lite")
        if receita.get("liquidado_rec") == "Sim":
            return {"receita": receita.get("id_conta_rec"), "resultado": "nao_enviado",
                    "motivo": "receita já liquidada"}
        return cobranca_whatsapp.cobrar_receita(
            receita, etapa, vhsys_api, asaas_api, aplicar,
            hoje or cobranca_whatsapp.agora().date(), reenviar=p.get("reenviar") == "1",
            canal=canal)
