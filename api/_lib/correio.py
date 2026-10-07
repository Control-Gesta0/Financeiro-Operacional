"""Envio de e-mail pelo SMTP da empresa (HostGator), para as cobranças.

Configuração na Vercel: SMTP_HOST (ex.: mail.controlgestao.com.br), SMTP_PORTA (465 =
SSL, padrão; 587 = STARTTLS), SMTP_USUARIO (financeiro@controlgestao.com.br) e
SMTP_SENHA (a senha da caixa). Opcionais: EMAIL_NOME (nome do remetente) e
EMAIL_ASSINATURA (texto no fim de cada e-mail).
"""
import os
import re
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

NOME_PADRAO = "Financeiro Control Gestão"
ASSINATURA_PADRAO = ("Financeiro Control Gestão\n"
                     "Dúvidas: responda este e-mail ou chame no WhatsApp (11) 5241-5807.")
EMAIL_VALIDO = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
LIMITE_PDF = 5_000_000


class ErroEmail(Exception):
    pass


def configurado():
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USUARIO")
                and os.environ.get("SMTP_SENHA"))


def remetente():
    return os.environ.get("SMTP_USUARIO", "")


def assinatura():
    return (os.environ.get("EMAIL_ASSINATURA") or ASSINATURA_PADRAO).replace("\\n", "\n")


def enderecos(texto):
    """E-mails válidos de um campo do cadastro (vários separados por ; , ou espaço)."""
    vistos = []
    for parte in re.split(r"[;,\s]+", str(texto or "")):
        parte = parte.strip().strip("<>").lower()
        if EMAIL_VALIDO.match(parte) and parte not in vistos:
            vistos.append(parte)
    return vistos[:5]


def mascarar(endereco):
    usuario, _, dominio = str(endereco).partition("@")
    return f"{usuario[:2]}***@{dominio}" if dominio else "***"


def baixar_pdf(url):
    """PDF do boleto (bytes) ou None se não der para baixar: o e-mail vai só com o link."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FinanceiroOperacional/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            dados = r.read(LIMITE_PDF + 1)
    except (OSError, ValueError):
        return None
    return dados if dados.startswith(b"%PDF") and len(dados) <= LIMITE_PDF else None


def enviar(destinos, assunto, texto, anexos=()):
    """Manda um e-mail em texto simples; anexos: [(nome, bytes)] em PDF.

    Devolve os destinatários recusados (vazio se todos aceitos). Se todos forem
    recusados, nada foi enviado e levanta ErroEmail."""
    if not configurado():
        raise ErroEmail("SMTP_HOST, SMTP_USUARIO ou SMTP_SENHA não configurados")
    if not destinos:
        raise ErroEmail("sem destinatário")
    msg = EmailMessage()
    msg["From"] = formataddr((os.environ.get("EMAIL_NOME") or NOME_PADRAO, remetente()))
    msg["To"] = ", ".join(destinos)
    msg["Subject"] = assunto
    msg["Message-ID"] = make_msgid(domain=remetente().partition("@")[2] or None)
    msg.set_content(f"{texto}\n\n--\n{assinatura()}")
    for nome, dados in anexos:
        msg.add_attachment(dados, maintype="application", subtype="pdf", filename=nome)
    host, porta = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORTA") or 465)
    contexto = ssl.create_default_context()
    try:
        if porta == 465:
            servidor = smtplib.SMTP_SSL(host, porta, timeout=30, context=contexto)
        else:
            servidor = smtplib.SMTP(host, porta, timeout=30)
            servidor.starttls(context=contexto)
        with servidor:
            servidor.login(remetente(), os.environ["SMTP_SENHA"])
            recusados = servidor.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise ErroEmail(f"SMTP {host}:{porta}: {e}") from e
    return sorted(recusados or {})
