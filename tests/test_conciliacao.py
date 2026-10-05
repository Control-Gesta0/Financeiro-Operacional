"""Testes da conciliação diária Asaas × VHSYS.

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
import conciliar  # noqa: E402


class Vhsys:
    def __init__(self, receitas, despesas=()):
        self.receitas = {r["id_conta_rec"]: r for r in receitas}
        self.despesas = list(despesas)

    def listar_despesas_taxa(self, data):
        return self.despesas

    def consultar_receita(self, id_receita):
        return self.receitas.get(int(id_receita))

    def buscar_abertas(self, valor, vencimento):
        return [r for r in self.receitas.values() if r["liquidado_rec"] == "Nao"
                and r["valor_rec"] == valor and r["vencimento_rec"] == vencimento]

    def buscar_por_cobranca(self, id_cobranca, valor=None):
        return [r for r in self.receitas.values()
                if id_cobranca in (r.get("observacoes_rec") or "") + (r.get("obs_pagamento") or "")
                and (valor is not None or r["liquidado_rec"] == "Nao")
                and (valor is None or r["valor_rec"] == valor)]

    def listar_liquidadas(self, data):
        return [r for r in self.receitas.values()
                if r["liquidado_rec"] == "Sim" and r.get("data_pagamento") == data]


class Asaas:
    def __init__(self, extrato, cobrancas=None, clientes=None):
        self.extrato, self.cobrancas, self.clientes = extrato, cobrancas or {}, clientes or {}

    def configurado(self):
        return True

    def listar_extrato(self, data):
        return self.extrato

    def consultar_cobranca(self, id_):
        return self.cobrancas.get(id_)

    def consultar_cliente(self, id_):
        return self.clientes.get(id_)


def receita(id_, liquidado="Nao", obs="", valor="100.00", venc="2026-10-10", data_pag=None,
            taxa=None):
    return {"id_conta_rec": id_, "liquidado_rec": liquidado, "obs_pagamento": obs,
            "valor_rec": valor, "valor_pago": valor, "vencimento_rec": venc,
            "nome_cliente": "Cliente", "data_pagamento": data_pag, "valor_taxa": taxa}


def mov(tipo, valor, saldo, pid=None, desc=""):
    return {"type": tipo, "value": valor, "balance": saldo, "paymentId": pid, "description": desc}


DIA = "2026-10-11"


class TestMontar(unittest.TestCase):
    def setUp(self):
        self.vhsys = Vhsys([
            receita(1, "Sim", "Baixa automática Asaas pay_1 (PIX).", data_pag=DIA, taxa="1.99"),
            receita(2),                                   # aberta, achada por externalReference
            receita(3, valor="50.00"), receita(4, valor="50.00"),  # ambíguas
            receita(5, "Sim", "baixa feita à mão"),       # baixada fora da integração
            receita(6, "Sim", "Baixa automática Asaas pay_9 (BOLETO).", data_pag=DIA),
        ])
        self.asaas = Asaas(
            [mov("PAYMENT_RECEIVED", 100, 100, "pay_1"),
             mov("PAYMENT_RECEIVED", 100, 200, "pay_2"),
             mov("PAYMENT_RECEIVED", 50, 250, "pay_3"),
             mov("PAYMENT_RECEIVED", 100, 350, "pay_5"),
             mov("PAYMENT_FEE", -1.99, 348.01, "pay_1"),
             mov("PAYMENT_FEE", -1.99, 346.02, "pay_2"),
             mov("TRANSFER", -300, 46.02),
             mov("TRANSFER_FEE", -1, 45.02),
             mov("CREDIT", 5, 50.02, desc="Bônus")],
            {"pay_2": {"id": "pay_2", "externalReference": "2"},
             "pay_3": {"id": "pay_3", "value": 50, "dueDate": "2026-10-10"},
             "pay_5": {"id": "pay_5", "externalReference": "5"}})

    def status(self, r):
        return {x["pagamento"]: x["status"] for x in r["recebimentos"]}

    def test_simulacao(self):
        with mock.patch.dict(os.environ, {"BAIXA_MODO": ""}):
            r = conciliar.montar(DIA, self.vhsys, self.asaas)
        self.assertEqual(self.status(r), {"pay_1": "baixado", "pay_2": "simulado",
                                          "pay_3": "pendente", "pay_5": "manual"})
        self.assertEqual([x["receita"] for x in r["baixas_sem_credito"]], [6])
        self.assertEqual(r["qtd"], {"recebimento": 4, "taxa": 1, "transferencia": 1, "outro": 1})
        self.assertEqual(r["taxas_lancadas"], {"qtd": 1, "total": "1.99"})
        self.assertEqual(r["taxas_simuladas"], {"qtd": 1, "total": "1.99"})
        self.assertEqual(r["saldo_final"], 50.02)
        t = conciliar.texto(r)
        self.assertIn("Conciliação Asaas · 11/10/2026", t)
        self.assertIn("Recebimentos: 4 · R$ 350,00", t)
        self.assertIn("✅ 1 baixados no VHSYS pela integração", t)
        self.assertIn("✅ 1 já estavam baixados", t)
        self.assertIn("🟡 1 seriam baixados (modo simulação)", t)
        self.assertIn("receita 2 (externalReference)", t)
        self.assertIn("🔴 1 sem baixa", t)
        self.assertIn("2 receitas em aberto", t)
        self.assertIn("Taxas de cobrança lançadas (30.01.10): 1 · R$ 1,99", t)
        self.assertIn("🟡 1 taxas · R$ 1,99 seriam lançadas (simulação)", t)
        self.assertIn("Taxas sem cobrança vinculada: 1 · R$ -1,00", t)
        self.assertIn("Transferências e Pix enviados: 1 · R$ -300,00", t)
        self.assertIn("Bônus", t)
        self.assertIn("Saldo Asaas no fim do dia: R$ 50,02", t)
        self.assertNotIn("—", t)

    def test_ativo_receita_aberta_vira_pendencia(self):
        with mock.patch.dict(os.environ, {"BAIXA_MODO": "ativo"}):
            r = conciliar.montar(DIA, self.vhsys, self.asaas)
        self.assertEqual(self.status(r)["pay_2"], "pendente")
        self.assertNotIn("seriam baixados", conciliar.texto(r))

    def test_taxa_nao_gravada_na_receita_baixada(self):
        v = Vhsys([receita(1, "Sim", "Baixa automática Asaas pay_1 (PIX).", data_pag=DIA)])
        a = Asaas([mov("PAYMENT_RECEIVED", 100, 100, "pay_1"),
                   mov("PAYMENT_FEE", -1.99, 98.01, "pay_1")])
        r = conciliar.montar(DIA, v, a)
        self.assertEqual(r["taxas_divergentes"],
                         [{"receita": 1, "pagamento": "pay_1", "valor": "1.99", "lancado": "0"}])
        t = conciliar.texto(r)
        self.assertIn("🔴 1 cobranças com taxa diferente do extrato", t)
        self.assertIn("receita 1 · extrato R$ 1,99 · lançado R$ 0,00", t)

    def test_taxa_conferida_contra_a_despesa_lancada(self):
        # Pix 30/09: Taxa do Pix 1,85 + mensageria 0,99 = despesa de 2,84.
        v = Vhsys([receita(1, "Sim", "Baixa automática Asaas pay_1 (PIX).", data_pag=DIA)],
                  despesas=[{"valor_pag": "2.84", "vencimento_pag": DIA,
                             "observacoes_pag": "Taxas da cobrança Asaas pay_1 (receita 1)."}])
        a = Asaas([mov("PAYMENT_RECEIVED", 100, 100, "pay_1"),
                   mov("PAYMENT_FEE", -1.85, 98.15, "pay_1"),
                   mov("PAYMENT_MESSAGING_NOTIFICATION_FEE", -0.99, 97.16, "pay_1")])
        r = conciliar.montar(DIA, v, a)
        self.assertEqual(r["taxas_lancadas"], {"qtd": 2, "total": "2.84"})
        self.assertEqual(r["taxas_divergentes"], [])
        self.assertIn("Taxas de cobrança lançadas (30.01.10): 2 · R$ 2,84", conciliar.texto(r))

    def test_duas_linhas_de_taxa_somam_contra_a_receita(self):
        # Caso de 30/09/2026: cada cobrança teve duas linhas de taxa somando R$ 2,84.
        v = Vhsys([receita(1, "Sim", "Baixa automática Asaas pay_1 (PIX).", data_pag=DIA,
                           taxa="2.84")])
        a = Asaas([mov("PAYMENT_RECEIVED", 164.66, 164.66, "pay_1"),
                   mov("PAYMENT_FEE", -1.99, 162.67, "pay_1", "Taxa da cobrança"),
                   mov("PAYMENT_MESSAGING_NOTIFICATION_FEE", -0.85, 161.82, "pay_1",
                       "Taxa de mensageria")])
        r = conciliar.montar(DIA, v, a)
        self.assertEqual(r["taxas_lancadas"], {"qtd": 2, "total": "2.84"})
        self.assertEqual(r["taxas_divergentes"], [])
        self.assertEqual([x["descricao"] for x in r["detalhe_taxas"]],
                         ["Taxa da cobrança", "Taxa de mensageria"])

    def test_fatura_antecipada_se_anula_e_some(self):
        # Caso real de 02/10/2026: venda no cartão antecipada e já baixada no VHSYS.
        desc = "fatura nr. 870087308 WESLEY ADVOCACIA"
        a = Asaas([mov("PAYMENT_RECEIVED", 118.11, 2355.60, "pay_l34", f"Cobrança recebida - {desc}"),
                   mov("RECEIVABLE_ANTICIPATION_DEBIT", -118.11, 2237.49, None,
                       f"Baixa da antecipação - {desc}")])
        r = conciliar.montar(DIA, Vhsys([]), a)
        self.assertEqual(r["recebimentos"], [])
        self.assertEqual(r["qtd"], {})
        self.assertEqual([x["pagamento"] for x in r["antecipadas_compensadas"]], ["pay_l34"])
        t = conciliar.texto(r)
        self.assertIn("Nenhum recebimento a conciliar", t)
        self.assertNotIn("🔴", t)
        self.assertNotIn("antecipação", t)
        self.assertIn("Saldo Asaas no fim do dia: R$ 2.237,49", t)

    def test_antecipacao_de_outro_valor_nao_compensa(self):
        a = Asaas([mov("PAYMENT_RECEIVED", 118.11, 100, "pay_1",
                       "Cobrança recebida - fatura nr. 1"),
                   mov("RECEIVABLE_ANTICIPATION_DEBIT", -50, 50, None,
                       "Baixa da antecipação - fatura nr. 1")],
                  {"pay_1": {"id": "pay_1", "value": 118.11, "dueDate": "2026-10-10"}})
        r = conciliar.montar(DIA, Vhsys([]), a)
        self.assertEqual(len(r["recebimentos"]), 1)
        self.assertEqual(r["antecipadas_compensadas"], [])

    def test_taxa_de_cobranca_pendente_tem_rotulo_proprio(self):
        a = Asaas([mov("PAYMENT_RECEIVED", 828, 828, "pay_jn"),
                   mov("PAYMENT_FEE", -1.99, 826.01, "pay_jn"),
                   mov("TRANSFER_FEE", -1, 825.01)],
                  {"pay_jn": {"id": "pay_jn", "value": 828, "dueDate": "2026-09-05"}})
        t = conciliar.texto(conciliar.montar(DIA, Vhsys([]), a))
        self.assertIn("Taxas das cobranças sem baixa: 1 · R$ -1,99", t)
        self.assertIn("Taxas sem cobrança vinculada: 1 · R$ -1,00", t)

    def test_taxas_de_cobranca_baixada_a_mao(self):
        # Caso real de 21/09/2026: NBO PARTICIPACOES, receita já baixada à mão.
        def dia(taxa):
            v = Vhsys([receita(140331048, "Sim", "baixa manual", valor="197.00", taxa=taxa)])
            a = Asaas([mov("PAYMENT_RECEIVED", 197.0, 784.74, "pay_nbo"),
                       mov("PAYMENT_FEE", -1.85, 782.89, "pay_nbo", "Taxa do Pix"),
                       mov("PAYMENT_MESSAGING_NOTIFICATION_FEE", -0.99, 781.90, "pay_nbo",
                           "Taxa de mensageria")],
                      {"pay_nbo": {"id": "pay_nbo", "externalReference": "140331048"}})
            return conciliar.montar(DIA, v, a)

        sem_taxa = dia(None)
        t = conciliar.texto(sem_taxa)
        self.assertIn("✅ 1 já estavam baixados no VHSYS", t)
        self.assertIn("Taxas de cobranças baixadas à mão, sem a taxa na receita: 2 · R$ -2,84", t)
        self.assertNotIn("cobranças sem baixa", t)

        com_taxa = dia("2.84")
        self.assertEqual(com_taxa["taxas_lancadas"], {"qtd": 2, "total": "2.84"})
        self.assertNotIn("Ainda não lançados", conciliar.texto(com_taxa))

    def test_dia_sem_movimento(self):
        r = conciliar.montar(DIA, Vhsys([]), Asaas([]))
        self.assertIn("Sem movimento", conciliar.texto(r))

    def test_ontem_em_brasilia(self):
        self.assertRegex(conciliar.ontem(), r"^\d{4}-\d{2}-\d{2}$")


class Falso(http.server.BaseHTTPRequestHandler):
    """Asaas + VHSYS + uazapi falsos num servidor só, separados pelo caminho."""
    enviados = []

    def _json(self, corpo, status=200):
        dados = json.dumps(corpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        if self.path.startswith("/asaas/financialTransactions"):
            return self._json({"data": [mov("PAYMENT_RECEIVED", 10, 10, "pay_x")],
                               "hasMore": False})
        if self.path.startswith("/asaas/payments/pay_x"):
            return self._json({"id": "pay_x", "externalReference": "7"})
        if self.path.startswith("/vhsys/contas-receber/7"):
            return self._json({"status": "success", "data": receita(7)})
        if self.path.startswith("/vhsys/contas-receber"):
            return self._json({"status": "success", "data": [], "paging": {"total": 0}})
        self._json({}, 404)

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        Falso.enviados.append((self.path, self.headers["token"], json.loads(self.rfile.read(n))))
        self._json({"ok": True})

    def log_message(self, *a):
        pass


class TestHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.falso = http.server.HTTPServer(("127.0.0.1", 0), Falso)
        threading.Thread(target=cls.falso.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.falso.server_port}"
        cls.env = mock.patch.dict(os.environ, {
            "CRON_SECRET": "cron", "ASAAS_API_KEY": "k", "UAZAPI_URL": f"{base}/uaz",
            "UAZAPI_TOKEN": "tok", "CONCILIACAO_WHATSAPP": "5511999999999",
            "BAIXA_MODO": "", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        cls.env.start()
        import asaas_api
        import vhsys_api
        cls.patches = [mock.patch.object(asaas_api, "BASE_URL", f"{base}/asaas"),
                       mock.patch.object(vhsys_api, "BASE_URL", f"{base}/vhsys")]
        for p in cls.patches:
            p.start()
        import conciliacao as rota  # api/conciliacao.py (o handler)
        cls.rota = rota
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), rota.handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.falso.shutdown()
        for p in cls.patches:
            p.stop()
        cls.env.stop()

    def setUp(self):
        Falso.enviados = []

    def get(self, query="", auth=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.srv.server_port}/api/conciliacao{query}",
            headers={"Authorization": auth} if auth else {})
        abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with abridor.open(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_sem_segredo_e_recusado(self):
        self.assertEqual(self.get("?data=2026-10-11")[0], 401)
        self.assertEqual(self.get("?chave=errada")[0], 401)
        self.assertEqual(Falso.enviados, [])

    def test_previa_nao_envia(self):
        with mock.patch("sys.stdout"):
            status, corpo = self.get("?data=2026-10-11&enviar=0&chave=cron")
        self.assertEqual((status, corpo["enviado"]), (200, False))
        self.assertIn("seriam baixados", corpo["texto"])
        self.assertEqual(Falso.enviados, [])

    def test_cron_envia_pelo_whatsapp(self):
        with mock.patch("sys.stdout"):
            status, corpo = self.get("?data=2026-10-11", auth="Bearer cron")
        self.assertEqual((status, corpo["enviado"]), (200, True))
        caminho, token, body = Falso.enviados[0]
        self.assertEqual((caminho, token, body["number"]), ("/uaz/send/text", "tok",
                                                            "5511999999999"))
        self.assertIn("Conciliação Asaas · 11/10/2026", body["text"])

    def test_data_invalida(self):
        self.assertEqual(self.get("?data=11/10/2026&chave=cron")[0], 400)


if __name__ == "__main__":
    unittest.main()
