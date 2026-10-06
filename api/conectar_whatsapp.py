"""GET /api/conectar_whatsapp?chave=<CRON_SECRET>: conecta o WhatsApp da Zaptos por QR Code.

Página para o financeiro: mostra se a instância está conectada e, se não estiver, gera
o QR Code para ler no celular (WhatsApp > Aparelhos conectados > Conectar aparelho).
O token da Zaptos fica só no servidor; a página se atualiza sozinha até conectar.
"""
import html
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))
import atendimento  # noqa: E402
from autorizacao import autorizado_cron, token_webhook_whatsapp  # noqa: E402
import whatsapp  # noqa: E402

ATUALIZAR_A_CADA = 15  # segundos; o QR Code da Zaptos muda e expira em cerca de 2 minutos

PAGINA = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">{refresh}
<title>Conectar WhatsApp</title>
<style>
:root {{ --fundo: #f6f7f9; --cartao: #fff; --texto: #1d2329; --suave: #5b6670; --ok: #1a7f37;
         --erro: #b42318; --borda: #dde1e6; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --fundo: #111417; --cartao: #1b1f24; --texto: #e8ebee; --suave: #a3adb7;
           --ok: #4ac26b; --erro: #f97066; --borda: #2d333b; }} }}
body {{ margin: 0; background: var(--fundo); color: var(--texto);
        font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 460px; margin: 32px auto; padding: 0 16px; }}
.cartao {{ background: var(--cartao); border: 1px solid var(--borda); border-radius: 12px;
           padding: 24px; text-align: center; }}
h1 {{ font-size: 1.25rem; margin: 0 0 8px; }}
p {{ color: var(--suave); margin: 8px 0; }}
.ok {{ color: var(--ok); font-weight: 600; }} .erro {{ color: var(--erro); font-weight: 600; }}
img {{ width: 100%; max-width: 300px; background: #fff; padding: 12px; border-radius: 8px; }}
ol {{ text-align: left; color: var(--suave); padding-left: 20px; }}
</style></head>
<body><main><div class="cartao">{conteudo}</div></main></body></html>"""


def _qr_src(qrcode):
    return qrcode if qrcode.startswith("data:") else f"data:image/png;base64,{qrcode}"


def conteudo(st, qrcode):
    """HTML do cartão a partir do status da instância (e do QR Code, se houver)."""
    inst = st.get("instance") or {}
    if inst.get("status") == "connected" or (st.get("status") or {}).get("connected"):
        jid = (st.get("status") or {}).get("jid") or {}
        numero = jid.get("user") if isinstance(jid, dict) else str(jid).split("@")[0]
        numero = str(numero or "").split(":")[0]  # "551199...:5" -> o ":5" é o aparelho
        return ("<h1>WhatsApp conectado</h1>"
                f'<p class="ok">Conectado como {html.escape(inst.get("profileName") or "")}'
                f"{' (' + html.escape(numero) + ')' if numero else ''}</p>"
                "<p>Pode fechar esta página. As cobranças e a conciliação saem por este número.</p>")
    if qrcode:
        return ("<h1>Conectar o WhatsApp</h1>"
                f'<img alt="QR Code para conectar o WhatsApp" src="{html.escape(_qr_src(qrcode))}">'
                "<ol><li>No celular do número da empresa, abra o <b>WhatsApp</b>.</li>"
                "<li>Toque em <b>⋮</b> (Android) ou <b>Configurações</b> (iPhone) e em "
                "<b>Aparelhos conectados</b>.</li>"
                "<li>Toque em <b>Conectar aparelho</b> e aponte para este QR Code.</li></ol>"
                f"<p>O código muda sozinho; a página se atualiza a cada {ATUALIZAR_A_CADA} segundos "
                "até conectar.</p>")
    return ("<h1>Gerando o QR Code…</h1>"
            f"<p>A página se atualiza em {ATUALIZAR_A_CADA} segundos.</p>")


def secao_atendimento(recebendo, link_ligar, erro=None):
    """Estado do atendimento automático e o botão que cadastra o webhook na Zaptos."""
    modo = atendimento.modo()
    explica = {"desligado": "desligado (o robô não responde ninguém)",
               "teste": "teste (só responde aos números de teste)",
               "ativo": "ativo (responde todos os clientes)"}[modo]
    partes = ['<hr style="border:0;border-top:1px solid var(--borda);margin:20px 0">',
              "<h1>Atendimento automático</h1>",
              f"<p>Modo: <b>{explica}</b>. Muda em ATENDIMENTO_MODO na Vercel.</p>"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        partes.append('<p class="erro">Falta a ANTHROPIC_API_KEY na Vercel.</p>')
    if erro:
        partes.append(f'<p class="erro">{html.escape(erro)}</p>')
    if recebendo:
        partes.append('<p class="ok">Recebimento de mensagens ligado.</p>')
    else:
        partes.append("<p>Recebimento de mensagens desligado. "
                      f'<a href="{html.escape(link_ligar)}">Ligar recebimento</a></p>')
    return "".join(partes)


class handler(BaseHTTPRequestHandler):
    def _pagina(self, status, corpo, atualizar=False):
        refresh = f'\n<meta http-equiv="refresh" content="{ATUALIZAR_A_CADA}">' if atualizar else ""
        dados = PAGINA.format(refresh=refresh, conteudo=corpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex")
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if not autorizado_cron(self.headers, params):
            return self._pagina(401, '<h1>Acesso negado</h1><p class="erro">Abra o link com a '
                                     "chave (?chave=...).</p>")
        try:
            st = whatsapp.status()
            inst = st.get("instance") or {}
            conectado = inst.get("status") == "connected" or (st.get("status") or {}).get("connected")
            qrcode = inst.get("qrcode") if inst.get("status") == "connecting" else None
            if not conectado and not qrcode:  # desconectado ou QR expirado: pede um novo
                inst = (whatsapp.conectar() or {}).get("instance") or {}
                qrcode = inst.get("qrcode")
        except whatsapp.ErroWhatsapp as e:
            print(json.dumps({"conectar_whatsapp": "erro", "motivo": str(e)}, ensure_ascii=False))
            dica = ("Confira ZAPTOS_TOKEN (token da instância) e ZAPTOS_URL na Vercel."
                    if e.status in (401, 403, 404) else "Tente de novo em alguns segundos.")
            return self._pagina(502, '<h1>Não consegui falar com a Zaptos</h1>'
                                     f'<p class="erro">{html.escape(str(e))}</p><p>{dica}</p>')
        extra = ""
        if conectado:
            host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
            url_webhook = f"https://{host}/api/webhooks/zaptos?chave={token_webhook_whatsapp()}"
            chave = urllib.parse.quote((params.get("chave") or [""])[0])
            erro = None
            try:
                if (params.get("webhook") or [""])[0] == "1":
                    whatsapp.configurar_webhook(url_webhook)
                recebendo = any(h.get("enabled") and h.get("url") == url_webhook
                                for h in whatsapp.ver_webhook())
            except whatsapp.ErroWhatsapp as e:
                recebendo, erro = False, f"Não consegui configurar o recebimento: {e}"
            extra = secao_atendimento(recebendo, f"?chave={chave}&webhook=1", erro)
        print(json.dumps({"conectar_whatsapp": "conectado" if conectado else "aguardando_qr"}))
        self._pagina(200, conteudo(st, None if conectado else qrcode) + extra,
                     atualizar=not conectado)
