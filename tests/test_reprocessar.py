"""Testes do reprocessamento de um dia (baixa a partir do extrato do Asaas).

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
import reprocessamento  # noqa: E402

DIA = "2026-09-30"
OBS_JN = "Cobranca em aberto no Asaas (pay_jn). Cobrança de P 1402"


class Vhsys:
    def __init__(self, receitas):
        self.receitas = {r["id_conta_rec"]: dict(r) for r in receitas}
        self.liquidadas = []

    def consultar_receita(self, id_receita):
        return self.receitas.get(int(id_receita))

    def buscar_por_cobranca(self, pid, valor=None):
        return [r for r in self.receitas.values() if pid in r.get("observacoes_rec", "")]

    def buscar_abertas(self, valor, vencimento):
        return [r for r in self.receitas.values() if r["liquidado_rec"] == "Nao"
                and r["valor_rec"] == valor and r["vencimento_rec"] == vencimento]

    def liquidar(self, id_receita, campos):
        self.liquidadas.append((id_receita, campos))
        self.receitas[id_receita].update(campos, liquidado_rec="Sim")


class Asaas:
    def __init__(self, extrato, cobrancas):
        self.extrato, self.cobrancas = extrato, cobrancas

    def configurado(self):
        return True

    def listar_extrato(self, data):
        return self.extrato

    def consultar_cobranca(self, pid):
        return self.cobrancas.get(pid)

    def consultar_cliente(self, pid):
        return None


def receita(id_, valor, venc, obs="", liquidado="Nao"):
    return {"id_conta_rec": id_, "valor_rec": valor, "vencimento_rec": venc,
            "observacoes_rec": obs, "liquidado_rec": liquidado, "nome_cliente": "X"}


def mov(tipo, valor, pid, desc=""):
    return {"type": tipo, "value": valor, "paymentId": pid, "description": desc}


def cenario():
    extrato = [mov("PAYMENT_RECEIVED", 828.0, "pay_jn"),
               mov("PAYMENT_FEE", -1.85, "pay_jn"),
               mov("PAYMENT_MESSAGING_NOTIFICATION_FEE", -0.99, "pay_jn"),
               mov("PAYMENT_RECEIVED", 50.0, "pay_ja", "Cobrança recebida - fatura nr. 1"),
               mov("PAYMENT_RECEIVED", 118.11, "pay_ant", "Cobrança recebida - fatura nr. 9"),
               mov("RECEIVABLE_ANTICIPATION_DEBIT", -118.11, None,
                   "Baixa da antecipação - fatura nr. 9")]
    cobrancas = {
        "pay_jn": {"id": "pay_jn", "value": 828.0, "netValue": 826.15, "dueDate": "2026-09-05",
                   "paymentDate": DIA, "billingType": "PIX"},
        "pay_ja": {"id": "pay_ja", "value": 50.0, "netValue": 48.15, "dueDate": "2026-09-10",
                   "paymentDate": DIA, "externalReference": "77"},
    }
    vhsys = Vhsys([receita(140738277, "828.00", "2026-09-29", OBS_JN),
                   receita(77, "50.00", "2026-09-10", liquidado="Sim")])
    return vhsys, Asaas(extrato, cobrancas)


class TestReprocessar(unittest.TestCase):
    def test_previa_nao_grava_mesmo_com_baixa_ligada(self):
        v, a = cenario()
        with mock.patch.dict(os.environ, {"BAIXA_MODO": "ativo"}):
            r = reprocessamento.reprocessar(DIA, v, a, aplicar=False)
        self.assertEqual(v.liquidadas, [])
        self.assertEqual(r["resumo"], {"liquidada": 1, "ja_liquidada": 1})
        self.assertEqual(r["antecipadas_ignoradas"], 1)
        jn = next(x for x in r["resultados"] if x["cobranca"] == "pay_jn")
        self.assertEqual((jn["modo"], jn["receita"]), ("simulacao", 140738277))

    def test_aplica_com_taxa_somada(self):
        v, a = cenario()
        with mock.patch.dict(os.environ, {"BAIXA_MODO": "ativo"}):
            r = reprocessamento.reprocessar(DIA, v, a, aplicar=True)
        self.assertEqual([i for i, _ in v.liquidadas], [140738277])
        campos = v.liquidadas[0][1]
        self.assertEqual((campos["valor_taxa"], campos["data_pagamento"]), ("2.84", DIA))
        self.assertTrue(r["aplicado"])

    def test_rodar_de_novo_nao_baixa_em_dobro(self):
        v, a = cenario()
        with mock.patch.dict(os.environ, {"BAIXA_MODO": "ativo"}):
            reprocessamento.reprocessar(DIA, v, a, aplicar=True)
            r = reprocessamento.reprocessar(DIA, v, a, aplicar=True)
        self.assertEqual(len(v.liquidadas), 1)
        self.assertEqual(r["resumo"], {"ja_liquidada": 2})


class TestRota(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {"CRON_SECRET": "cron", "BAIXA_MODO": "",
                                               "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        cls.env.start()
        import reprocessar as rota  # api/reprocessar.py (o handler)
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), rota.handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.env.stop()

    def get(self, query):
        abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with abridor.open(f"http://127.0.0.1:{self.srv.server_port}/api/reprocessar{query}",
                              timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_sem_chave(self):
        self.assertEqual(self.get(f"?data={DIA}")[0], 401)

    def test_sem_data(self):
        self.assertEqual(self.get("?chave=cron")[0], 400)

    def test_aplicar_com_baixa_em_simulacao_e_recusado(self):
        status, corpo = self.get(f"?data={DIA}&aplicar=1&chave=cron")
        self.assertEqual(status, 409)
        self.assertIn("BAIXA_MODO=ativo", corpo["erro"])


if __name__ == "__main__":
    unittest.main()
