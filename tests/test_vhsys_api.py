"""Testes do cliente VHSYS contra um servidor falso com a semântica "a partir de".

    python3 -m unittest discover -s tests
"""
import http.server
import io
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api" / "_lib"))
import vhsys_api  # noqa: E402


def rec(id_, valor, venc, liquidado="Nao", data_pag=None, venc_original=None, obs=""):
    return {"id_conta_rec": id_, "valor_rec": valor, "vencimento_rec": venc,
            "observacoes_rec": obs,
            "vencimento_original": venc_original or venc, "liquidado_rec": liquidado,
            "data_pagamento": data_pag, "nome_cliente": "J.N DOS REIS LOCACOES FESTAS E EVENTOS"}


# Contrato mensal de R$ 828,00 (caso real de 30/09/2026) + outros títulos maiores.
RECEITAS = [rec(1, "828.00", "2026-09-05",
                obs="Cobranca em aberto no Asaas (pay_4r7akexz0qfrn793)."),
            rec(2, "828.00", "2026-10-05"),
            rec(3, "828.00", "2026-11-05"), rec(4, "900.00", "2026-09-05"),
            rec(5, "828.00", "2026-08-05", venc_original="2026-09-05"),
            rec(6, "828.00", "2026-09-30", "Sim", data_pag="2026-09-30"),
            rec(7, "828.00", "2026-10-30", "Sim", data_pag="2026-10-01")]


class VhsysAPartirDe(http.server.BaseHTTPRequestHandler):
    """Imita o VHSYS: valor sozinho é "a partir de"; ignora as datas (o pior caso)."""
    consultas = []

    def do_GET(self):
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
        VhsysAPartirDe.consultas.append(q)
        dados = list(RECEITAS)
        if "valor_receita" in q:
            minimo, _, maximo = q["valor_receita"].partition(",")
            dados = [r for r in dados if float(r["valor_rec"]) >= float(minimo)
                     and (not maximo or float(r["valor_rec"]) <= float(maximo))]
        if q.get("liquidado"):
            dados = [r for r in dados if r["liquidado_rec"] == q["liquidado"]]
        offset, limit = int(q.get("offset", 0)), int(q.get("limit", 250))
        corpo = {"status": "success", "data": dados[offset:offset + limit],
                 "paging": {"total": len(dados)}}
        status = 200
        if not corpo["data"]:  # como o VHSYS real: consulta vazia vira 403
            status, corpo = 403, {"code": 403, "status": "error", "data": "Nenhuma receita encontrada!"}
        dados_json = json.dumps(corpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(dados_json)

    def log_message(self, *a):
        pass


class TestFiltros(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), VhsysAPartirDe)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.patches = [mock.patch.object(vhsys_api, "BASE_URL",
                                         f"http://127.0.0.1:{cls.srv.server_port}"),
                       mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1",
                                                    "no_proxy": "127.0.0.1"})]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for p in cls.patches:
            p.stop()

    def setUp(self):
        VhsysAPartirDe.consultas = []

    def test_consulta_sem_resultado_vira_lista_vazia(self):
        # caso real (06/10/2026): HTTP 403 "Nenhuma receita encontrada!" derrubava o atendimento
        self.assertEqual(vhsys_api.buscar_abertas("1.00", "2026-09-05"), [])
        self.assertEqual(vhsys_api._todas({"valor_receita": "5.00,5.00"}), [])

    def test_403_de_verdade_continua_erro(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
                "u", 403, "Forbidden", {}, io.BytesIO(b'{"data":"nome_conta muito longo"}'))):
            with self.assertRaises(vhsys_api.ErroVhsys):
                vhsys_api.buscar_abertas("1.00", "2026-09-05")
            with self.assertRaises(vhsys_api.ErroVhsys):
                vhsys_api.cadastrar_despesa({"nome_conta": "x"})

    def test_busca_exata_de_valor_e_vencimento(self):
        achadas = vhsys_api.buscar_abertas("828.00", "2026-09-05")
        # A de 05/09 e a prorrogada (vencimento original 05/09); não as dos meses seguintes.
        self.assertEqual(sorted(r["id_conta_rec"] for r in achadas), [1, 5])
        self.assertEqual(VhsysAPartirDe.consultas[0]["valor_receita"], "828.00,828.00")

    def test_busca_pelo_id_da_cobranca(self):
        com_valor = vhsys_api.buscar_por_cobranca("pay_4r7akexz0qfrn793", "828.00")
        self.assertEqual([r["id_conta_rec"] for r in com_valor], [1])
        self.assertEqual(VhsysAPartirDe.consultas[0]["valor_receita"], "828.00,828.00")
        sem_valor = vhsys_api.buscar_por_cobranca("pay_4r7akexz0qfrn793")
        self.assertEqual([r["id_conta_rec"] for r in sem_valor], [1])
        self.assertEqual(vhsys_api.buscar_por_cobranca("pay_inexistente", "828.00"), [])
        self.assertEqual(vhsys_api.buscar_por_cobranca("pay_4r7akexz0qfrn79", "828.00"), [])

    def test_liquidadas_so_do_dia(self):
        achadas = vhsys_api.listar_liquidadas("2026-09-30")
        self.assertEqual([r["id_conta_rec"] for r in achadas], [6])


if __name__ == "__main__":
    unittest.main()
