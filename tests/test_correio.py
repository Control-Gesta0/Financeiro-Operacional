"""Testes do envio de e-mail pelo SMTP (HostGator).

    python3 -m unittest discover -s tests
"""
import io
import os
import smtplib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api" / "_lib"))
import correio  # noqa: E402

ENV = {"SMTP_HOST": "mail.exemplo.com.br", "SMTP_PORTA": "", "SMTP_USUARIO": "financeiro@exemplo.com.br",
       "SMTP_SENHA": "segredo", "EMAIL_NOME": "", "EMAIL_ASSINATURA": ""}


class SmtpFalso:
    instancias = []

    def __init__(self, host, porta, timeout=None, context=None):
        self.host, self.porta, self.mensagens, self.login_feito = host, porta, [], None
        self.recusar = set()
        SmtpFalso.instancias.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, usuario, senha):
        self.login_feito = (usuario, senha)

    def send_message(self, msg):
        self.mensagens.append(msg)
        return {d: (550, b"nao existe") for d in self.recusar}


class TestCorreio(unittest.TestCase):
    def setUp(self):
        SmtpFalso.instancias = []
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_enderecos_do_cadastro(self):
        self.assertEqual(correio.enderecos("Fin@Csl.com.br; outro@csl.com.br , fin@csl.com.br"),
                         ["fin@csl.com.br", "outro@csl.com.br"])
        self.assertEqual(correio.enderecos("sem email; @x; a@b"), [])
        self.assertEqual(correio.mascarar("financeiro@csl.com.br"), "fi***@csl.com.br")

    def test_envia_por_ssl_com_anexo_e_assinatura(self):
        with mock.patch.object(smtplib, "SMTP_SSL", SmtpFalso):
            recusados = correio.enviar(["a@csl.com.br"], "Boleto", "Olá!",
                                       [("boleto-1.pdf", b"%PDF-1.4 x")])
        (smtp,) = SmtpFalso.instancias
        self.assertEqual((smtp.host, smtp.porta, smtp.login_feito, recusados),
                         ("mail.exemplo.com.br", 465, ("financeiro@exemplo.com.br", "segredo"), []))
        msg = smtp.mensagens[0]
        self.assertEqual(msg["From"], "Financeiro Control Gestão <financeiro@exemplo.com.br>")
        self.assertEqual((msg["To"], msg["Subject"]), ("a@csl.com.br", "Boleto"))
        corpo = msg.get_body(("plain",)).get_content()
        self.assertIn("Olá!", corpo)
        self.assertIn("WhatsApp (11) 5241-5807", corpo)
        (anexo,) = list(msg.iter_attachments())
        self.assertEqual((anexo.get_filename(), anexo.get_content()), ("boleto-1.pdf", b"%PDF-1.4 x"))

    def test_porta_587_usa_starttls(self):
        with mock.patch.dict(os.environ, {"SMTP_PORTA": "587"}), \
                mock.patch.object(smtplib, "SMTP", SmtpFalso):
            correio.enviar(["a@csl.com.br"], "Boleto", "Olá!")
        self.assertTrue(SmtpFalso.instancias[0].tls)

    def test_recusa_parcial_devolve_os_recusados(self):
        class Recusa(SmtpFalso):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.recusar = {"b@csl.com.br"}
        with mock.patch.object(smtplib, "SMTP_SSL", Recusa):
            self.assertEqual(correio.enviar(["a@csl.com.br", "b@csl.com.br"], "B", "x"),
                             ["b@csl.com.br"])

    def test_falha_do_servidor_e_falta_de_configuracao(self):
        def quebra(*a, **k):
            raise smtplib.SMTPAuthenticationError(535, b"senha errada")
        with mock.patch.object(smtplib, "SMTP_SSL", quebra):
            with self.assertRaises(correio.ErroEmail):
                correio.enviar(["a@csl.com.br"], "B", "x")
        with mock.patch.dict(os.environ, {"SMTP_SENHA": ""}):
            self.assertFalse(correio.configurado())
            with self.assertRaises(correio.ErroEmail):
                correio.enviar(["a@csl.com.br"], "B", "x")

    def test_baixar_pdf_so_aceita_pdf(self):
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(b"%PDF-1.4 ok")):
            self.assertEqual(correio.baixar_pdf("https://x/b.pdf"), b"%PDF-1.4 ok")
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(b"<html>")):
            self.assertIsNone(correio.baixar_pdf("https://x/b.pdf"))
        with mock.patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            self.assertIsNone(correio.baixar_pdf("https://x/b.pdf"))


if __name__ == "__main__":
    unittest.main()
