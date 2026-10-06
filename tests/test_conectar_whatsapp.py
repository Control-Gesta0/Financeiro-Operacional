"""Testes da página de conexão do WhatsApp (QR Code da Zaptos).

    python3 -m unittest discover -s tests
"""
import http.server
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "api" / "_lib"))
sys.path.insert(0, str(RAIZ / "api"))

QR = "data:image/png;base64,iVBORw0KGgoQRFALSO"


class Zaptos(http.server.BaseHTTPRequestHandler):
    estado, chamadas, http_status = "disconnected", [], 200

    def _json(self, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(Zaptos.http_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        Zaptos.chamadas.append(("GET", self.path, self.headers["token"]))
        if Zaptos.http_status != 200:
            return self._json({"error": "Invalid token"})
        inst = {"status": Zaptos.estado, "profileName": "Control Gestão"}
        if Zaptos.estado == "connecting":
            inst["qrcode"] = QR
        conectado = Zaptos.estado == "connected"
        self._json({"instance": inst, "status": {
            "connected": conectado,
            "jid": {"user": "5511900001111", "server": "s.whatsapp.net"} if conectado else None}})

    def do_POST(self):
        Zaptos.chamadas.append(("POST", self.path, self.headers["token"]))
        Zaptos.estado = "connecting"
        self._json({"connected": False, "instance": {"status": "connecting", "qrcode": QR}})

    def log_message(self, *a):
        pass


class TestConectarWhatsapp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zaptos = http.server.HTTPServer(("127.0.0.1", 0), Zaptos)
        threading.Thread(target=cls.zaptos.serve_forever, daemon=True).start()
        cls.env = mock.patch.dict(os.environ, {
            "CRON_SECRET": "cron", "ZAPTOS_URL": f"http://127.0.0.1:{cls.zaptos.server_port}",
            "ZAPTOS_TOKEN": "tok-secreto", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        cls.env.start()
        import conectar_whatsapp as rota  # api/conectar_whatsapp.py (o handler)
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), rota.handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.zaptos.shutdown()
        cls.env.stop()

    def setUp(self):
        Zaptos.estado, Zaptos.chamadas, Zaptos.http_status = "disconnected", [], 200

    def get(self, query="?chave=cron"):
        abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://127.0.0.1:{self.srv.server_port}/api/conectar_whatsapp{query}"
        try:
            with mock.patch("sys.stdout"), abridor.open(url, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_sem_chave_nao_fala_com_a_zaptos(self):
        status, pagina = self.get("")
        self.assertEqual(status, 401)
        self.assertEqual(Zaptos.chamadas, [])

    def test_desconectado_gera_qr_code(self):
        status, pagina = self.get()
        self.assertEqual(status, 200)
        self.assertEqual([c[:2] for c in Zaptos.chamadas],
                         [("GET", "/instance/status"), ("POST", "/instance/connect")])
        self.assertEqual({c[2] for c in Zaptos.chamadas}, {"tok-secreto"})
        self.assertIn(f'src="{QR}"', pagina)
        self.assertIn('http-equiv="refresh"', pagina)
        self.assertNotIn("tok-secreto", pagina)

    def test_conectando_reaproveita_o_qr_sem_pedir_outro(self):
        Zaptos.estado = "connecting"
        status, pagina = self.get()
        self.assertEqual([c[:2] for c in Zaptos.chamadas], [("GET", "/instance/status")])
        self.assertIn(f'src="{QR}"', pagina)

    def test_conectado_mostra_o_numero_e_para_de_atualizar(self):
        Zaptos.estado = "connected"
        status, pagina = self.get()
        self.assertEqual(status, 200)
        self.assertIn("WhatsApp conectado", pagina)
        self.assertIn("Control Gestão (5511900001111)", pagina)
        self.assertNotIn("refresh", pagina)
        self.assertNotIn("<img", pagina)
        self.assertEqual(len(Zaptos.chamadas), 1)

    def test_token_errado_explica_o_que_conferir(self):
        Zaptos.http_status = 401
        status, pagina = self.get()
        self.assertEqual(status, 502)
        self.assertIn("Confira ZAPTOS_TOKEN", pagina)
        self.assertNotIn("tok-secreto", pagina)


if __name__ == "__main__":
    unittest.main()
