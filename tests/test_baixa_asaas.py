"""Testes da baixa automática Asaas -> VHSYS.

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
sys.path.insert(0, str(RAIZ / "api" / "webhooks"))
import baixa  # noqa: E402


class VhsysFalso:
    def __init__(self, receitas, liquida_na_criacao=True):
        self.receitas = {r["id_conta_rec"]: dict(r) for r in receitas}
        self.liquidadas, self.desliquidadas = [], []
        self.despesas, self.despesas_liquidadas = {}, []
        self.liquida_na_criacao = liquida_na_criacao

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

    def liquidar(self, id_receita, campos):
        self.liquidadas.append((id_receita, campos))
        self.receitas[id_receita].update(campos, liquidado_rec="Sim")

    def desliquidar(self, id_receita):
        self.desliquidadas.append(id_receita)

    # despesas de taxa
    def buscar_despesa_taxa(self, pid, data):
        return [d for d in self.despesas.values()
                if pid in d["observacoes_pag"] and d["vencimento_pag"] == data]

    def listar_despesas_taxa(self, data):
        return [d for d in self.despesas.values() if d["vencimento_pag"] == data]

    def cadastrar_despesa(self, campos):
        id_ = 900 + len(self.despesas)
        pago = "Sim" if self.liquida_na_criacao else "Nao"
        self.despesas[id_] = {**campos, "id_conta_pag": id_, "liquidado_pag": pago}
        return {"id_conta_pag": id_}

    def consultar_despesa(self, id_):
        return self.despesas.get(id_)

    def liquidar_despesa(self, id_, valor, data):
        self.despesas_liquidadas.append(id_)
        self.despesas[id_]["liquidado_pag"] = "Sim"


class AsaasFalso:
    def __init__(self, clientes=None, extrato=None):
        self.clientes = clientes or {}
        self.extrato = extrato or []
        self.consultas_extrato = []

    def configurado(self):
        return True

    def listar_extrato(self, data):
        self.consultas_extrato.append(data)
        return self.extrato

    def consultar_cliente(self, id_cliente):
        return self.clientes.get(id_cliente)


def receita(id_, valor="100.00", venc="2026-10-10", cliente="José da Silva", liquidado="Nao",
            obs=""):
    return {"id_conta_rec": id_, "valor_rec": valor, "vencimento_rec": venc,
            "nome_cliente": cliente, "liquidado_rec": liquidado, "observacoes_rec": obs,
            "id_banco": "7001"}


def evento(tipo="PAYMENT_RECEIVED", **pagamento):
    p = {"id": "pay_1", "customer": "cus_1", "value": 100.0, "netValue": 98.01,
         "dueDate": "2026-10-10", "paymentDate": "2026-10-11", "billingType": "BOLETO"}
    p.update(pagamento)
    return {"event": tipo, "payment": p}


@mock.patch.dict(os.environ, {"BAIXA_MODO": "ativo"})
class TestRegra(unittest.TestCase):
    def test_baixa_pelo_external_reference(self):
        v = VhsysFalso([receita(10)])
        r = baixa.processar_evento(evento(externalReference="10"), v, AsaasFalso())
        self.assertEqual(r["resultado"], "liquidada")
        self.assertEqual(r["localizada_por"], "externalReference")
        id_, campos = v.liquidadas[0]
        self.assertEqual(id_, 10)
        self.assertEqual(campos["valor_rec"], "100.00")
        self.assertEqual(campos["valor_pago"], "100.00")
        self.assertEqual(campos["data_pagamento"], "2026-10-11")
        self.assertIn("pay_1", campos["obs_pagamento"])
        self.assertNotIn("valor_taxa", campos)  # o VHSYS ignora; a taxa vira despesa

    def test_taxa_vira_despesa_paga_na_categoria_de_taxas(self):
        v = VhsysFalso([receita(10)])
        r = baixa.processar_evento(evento(externalReference="10", invoiceNumber="868268326"),
                                   v, AsaasFalso())
        self.assertEqual(r["taxa"]["status"], "lancada")
        self.assertIs(r["taxa"]["paga"], True)
        d = v.despesas[r["taxa"]["despesa"]]
        self.assertEqual((d["valor_pag"], d["valor_pago"]), ("1.99", "1.99"))
        self.assertEqual((d["id_categoria"], d["categoria_pag"]),
                         ("9653020", "30.01.10 - Taxas de Cobrança Boleto"))
        self.assertEqual(d["id_banco"], "7001")  # a mesma conta da receita (Asaas)
        self.assertEqual((d["vencimento_pag"], d["data_pagamento"], d["liquidado_pag"]),
                         ("2026-10-11", "2026-10-11", "Sim"))
        self.assertEqual(d["nome_conta"], "Taxas Asaas 868268326 - José da Silva")
        self.assertIn("Taxas da cobrança Asaas pay_1 (receita 10)", d["observacoes_pag"])

    def test_nome_da_despesa_cabe_em_45_caracteres(self):
        # Caso real de 05/10/2026: o VHSYS recusou o nome com o cliente inteiro.
        nome = baixa.nome_despesa_taxa("868268326", "J.N DOS REIS LOCACOES FESTAS E EVENTOS")
        self.assertLessEqual(len(nome), 45)
        self.assertTrue(nome.startswith("Taxas Asaas 868268326 - J.N DOS REIS"))
        self.assertEqual(baixa.nome_despesa_taxa("pay_4r7akexz0qfrn793", ""),
                         "Taxas Asaas pay_4r7akexz0qfrn793")
        r = receita(10, cliente="J.N DOS REIS LOCACOES FESTAS E EVENTOS")
        campos = baixa.campos_despesa_taxa({"id": "pay_1", "invoiceNumber": "868268326",
                                            "paymentDate": "2026-09-30"}, r, "2.84")
        self.assertIn("J.N DOS REIS LOCACOES FESTAS E EVENTOS", campos["observacoes_pag"])

    def test_taxa_soma_pix_e_mensageria_do_extrato(self):
        # Caso real de 30/09/2026: Taxa do Pix 1,85 + Taxa de mensageria 0,99.
        extrato = [
            {"type": "PAYMENT_RECEIVED", "paymentId": "pay_1", "value": 100.0},
            {"type": "PAYMENT_FEE", "paymentId": "pay_1", "value": -1.85},
            {"type": "PAYMENT_MESSAGING_NOTIFICATION_FEE", "paymentId": "pay_1", "value": -0.99},
            {"type": "PAYMENT_FEE", "paymentId": "pay_outro", "value": -1.85},
        ]
        v = VhsysFalso([receita(10)])
        a = AsaasFalso(extrato=extrato)
        r = baixa.processar_evento(evento(externalReference="10", netValue=98.15), v, a)
        self.assertEqual((r["taxa"]["valor"], r["taxa"]["origem"]), ("2.84", "extrato"))
        self.assertEqual(a.consultas_extrato, ["2026-10-11"])

    def test_sem_linhas_no_extrato_usa_bruto_menos_liquido(self):
        v = VhsysFalso([receita(10)])
        r = baixa.processar_evento(evento(externalReference="10"), v, AsaasFalso())
        self.assertEqual((r["taxa"]["valor"], r["taxa"]["origem"]), ("1.99", "valor_liquido"))

    def test_liquida_a_despesa_se_o_vhsys_nao_liquidar_na_criacao(self):
        v = VhsysFalso([receita(10)], liquida_na_criacao=False)
        r = baixa.processar_evento(evento(externalReference="10"), v, AsaasFalso())
        self.assertEqual(v.despesas_liquidadas, [r["taxa"]["despesa"]])
        self.assertIs(r["taxa"]["paga"], True)

    def test_sem_taxa_nao_lanca_despesa(self):
        v = VhsysFalso([receita(10)])
        r = baixa.processar_evento(evento(externalReference="10", netValue=100.0), v,
                                   AsaasFalso())
        self.assertIsNone(r["taxa"])
        self.assertEqual(v.despesas, {})

    def test_receita_ja_baixada_pela_integracao_so_ganha_a_taxa(self):
        # Caso real de 30/09/2026: J.N e Viviane baixadas antes, taxa ignorada pelo VHSYS.
        r10 = receita(10, liquidado="Sim")
        r10["obs_pagamento"] = "Baixa automática Asaas pay_1 (PIX). Pago R$ 100.00."
        v = VhsysFalso([r10])
        r = baixa.processar_evento(evento(externalReference="10"), v, AsaasFalso())
        self.assertEqual((r["resultado"], r["taxa"]["status"]), ("ja_liquidada", "lancada"))
        self.assertEqual(v.liquidadas, [])
        r2 = baixa.processar_evento(evento(externalReference="10"), v, AsaasFalso())
        self.assertEqual(r2["taxa"]["status"], "ja_lancada")
        self.assertEqual(len(v.despesas), 1)

    def test_receita_baixada_a_mao_nao_ganha_taxa(self):
        v = VhsysFalso([receita(10, liquidado="Sim")])
        r = baixa.processar_evento(evento(externalReference="10"), v, AsaasFalso())
        self.assertEqual(r["resultado"], "ja_liquidada")
        self.assertNotIn("taxa", r)
        self.assertEqual(v.despesas, {})

    def test_sem_conta_bancaria_nao_lanca(self):
        sem_conta = receita(10)
        sem_conta["id_banco"] = None
        v = VhsysFalso([sem_conta])
        r = baixa.processar_evento(evento(externalReference="10"), v, AsaasFalso())
        self.assertEqual(r["taxa"]["status"], "sem_conta_bancaria")
        self.assertEqual(v.despesas, {})

    def test_pagamento_com_juros_mantem_valor_original(self):
        v = VhsysFalso([receita(10)])
        baixa.processar_evento(evento(externalReference="10", value=103.5, originalValue=100.0),
                               v, AsaasFalso())
        campos = v.liquidadas[0][1]
        self.assertEqual((campos["valor_rec"], campos["valor_pago"]), ("100.00", "103.50"))

    def test_conta_do_asaas_quando_configurada(self):
        v = VhsysFalso([receita(10)])
        with mock.patch.dict(os.environ, {"VHSYS_ID_BANCO_ASAAS": "555"}):
            baixa.processar_evento(evento(externalReference="10"), v, AsaasFalso())
        self.assertEqual(v.liquidadas[0][1]["id_banco"], "555")

    def test_reenvio_nao_liquida_duas_vezes(self):
        v = VhsysFalso([receita(10, liquidado="Sim")])
        r = baixa.processar_evento(evento(externalReference="10"), v, AsaasFalso())
        self.assertEqual(r["resultado"], "ja_liquidada")
        self.assertEqual(v.liquidadas, [])

    def test_external_reference_inexistente_nao_cai_na_heuristica(self):
        v = VhsysFalso([receita(11)])
        r = baixa.processar_evento(evento(externalReference="99"), v, AsaasFalso())
        self.assertEqual(r["resultado"], "nao_conciliado")
        self.assertEqual(v.liquidadas, [])

    def test_acha_pelo_id_da_cobranca_nas_observacoes(self):
        # Caso real de 30/09/2026: J.N DOS REIS, receita vence 29/09 e a cobrança 05/09.
        obs = "Cobranca em aberto no Asaas (pay_4r7akexz0qfrn793). Cobrança de P 1402"
        v = VhsysFalso([receita(140738277, "828.00", "2026-09-29", obs=obs),
                        receita(2, "828.00", "2026-10-29")])
        p = {"id": "pay_4r7akexz0qfrn793", "value": 828.0, "dueDate": "2026-09-05"}
        r = baixa.processar_evento(evento(**p), v, AsaasFalso())
        self.assertEqual((r["resultado"], r["receita"]), ("liquidada", 140738277))
        self.assertEqual(r["localizada_por"], "id da cobrança nas observações")

    def test_id_nas_observacoes_de_receita_ja_baixada(self):
        v = VhsysFalso([receita(7, liquidado="Sim", obs="Asaas (pay_1)")])
        r = baixa.processar_evento(evento(), v, AsaasFalso())
        self.assertEqual((r["resultado"], r["receita"]), ("ja_liquidada", 7))

    def test_acha_pelas_observacoes_mesmo_com_valor_diferente(self):
        v = VhsysFalso([receita(8, "95.00", obs="Asaas (pay_1)")])
        r = baixa.processar_evento(evento(), v, AsaasFalso())
        self.assertEqual(r["receita"], 8)

    def test_sem_referencia_acha_por_valor_vencimento_cliente(self):
        v = VhsysFalso([receita(10, cliente="JOSE DA SILVA"), receita(11, cliente="Maria")])
        a = AsaasFalso({"cus_1": {"name": "José  da Silva"}})
        r = baixa.processar_evento(evento(), v, a)
        self.assertEqual((r["resultado"], r["receita"]), ("liquidada", 10))
        self.assertEqual(r["localizada_por"], "valor+vencimento+cliente")

    def test_sem_referencia_e_ambiguo_nao_mexe(self):
        v = VhsysFalso([receita(10), receita(11)])
        r = baixa.processar_evento(evento(), v, AsaasFalso())
        self.assertEqual(r["resultado"], "nao_conciliado")
        self.assertIn("2 receitas", r["motivo"])
        self.assertEqual(v.liquidadas, [])

    def test_cliente_diferente_nao_concilia(self):
        v = VhsysFalso([receita(10, cliente="Maria")])
        a = AsaasFalso({"cus_1": {"name": "José da Silva"}})
        r = baixa.processar_evento(evento(), v, a)
        self.assertEqual(r["resultado"], "nao_conciliado")

    def test_estorno_desliquida(self):
        v = VhsysFalso([receita(10, liquidado="Sim")])
        r = baixa.processar_evento(evento("PAYMENT_REFUNDED", externalReference="10"), v,
                                   AsaasFalso())
        self.assertEqual((r["resultado"], v.desliquidadas), ("desliquidada", [10]))

    def test_estorno_sem_referencia_fica_manual(self):
        v = VhsysFalso([receita(10, liquidado="Sim")])
        r = baixa.processar_evento(evento("PAYMENT_REFUNDED"), v, AsaasFalso())
        self.assertEqual(r["resultado"], "nao_conciliado")
        self.assertEqual(v.desliquidadas, [])

    def test_cobranca_antecipada_e_ignorada(self):
        v = VhsysFalso([receita(10)])
        r = baixa.processar_evento(evento(externalReference="10", anticipated=True), v,
                                   AsaasFalso())
        self.assertEqual((r["resultado"], r["motivo"]), ("ignorado", "cobrança antecipada"))
        self.assertEqual(v.liquidadas, [])

    def test_eventos_sem_acao(self):
        v = VhsysFalso([receita(10)])
        for tipo, esperado in (("PAYMENT_CREATED", "ignorado"), ("PAYMENT_CONFIRMED", "ignorado"),
                               ("PAYMENT_CHARGEBACK_REQUESTED", "alerta")):
            r = baixa.processar_evento(evento(tipo, externalReference="10"), v, AsaasFalso())
            self.assertEqual(r["resultado"], esperado, tipo)
        self.assertEqual((v.liquidadas, v.desliquidadas), ([], []))


class TestSimulacao(unittest.TestCase):
    def test_padrao_e_simulacao_e_nao_grava(self):
        v = VhsysFalso([receita(10)])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BAIXA_MODO", None)
            r = baixa.processar_evento(evento(externalReference="10"), v, AsaasFalso())
        self.assertEqual((r["modo"], r["resultado"]), ("simulacao", "liquidada"))
        self.assertEqual(r["taxa"]["status"], "simulada")
        self.assertEqual((v.liquidadas, v.despesas), ([], {}))


class VhsysHttpFalso(http.server.BaseHTTPRequestHandler):
    """Imita a API do VHSYS para testar o handler com HTTP de verdade."""
    puts, posts = [], []
    falhar = False

    def _json(self, status, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        if self.falhar:
            return self._json(503, {"status": "error"})
        assert self.headers["access-token"] == "tk"
        if self.path.startswith("/contas-pagar"):
            return self._json(200, {"status": "success", "data": [], "paging": {"total": 0}})
        self._json(200, {"status": "success", "data": receita(10)})

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        VhsysHttpFalso.posts.append((self.path, json.loads(self.rfile.read(n))))
        self._json(200, {"status": "success",
                         "data": {"id_conta_pag": 901, "liquidado_pag": "Sim"}})

    def do_PUT(self):
        n = int(self.headers["Content-Length"])
        VhsysHttpFalso.puts.append((self.path, json.loads(self.rfile.read(n))))
        self._json(200, {"status": "success", "data": {}})

    def log_message(self, *a):
        pass


class TestHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vh = http.server.HTTPServer(("127.0.0.1", 0), VhsysHttpFalso)
        threading.Thread(target=cls.vh.serve_forever, daemon=True).start()
        cls.env = mock.patch.dict(os.environ, {
            "ASAAS_WEBHOOK_TOKEN": "segredo", "BAIXA_MODO": "ativo",
            "VHSYS_ACCESS_TOKEN": "tk", "VHSYS_SECRET_ACCESS_TOKEN": "sk",
            "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        cls.env.start()
        import asaas as webhook
        import vhsys_api
        cls.vhsys_base = mock.patch.object(vhsys_api, "BASE_URL",
                                           f"http://127.0.0.1:{cls.vh.server_port}")
        cls.vhsys_base.start()
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), webhook.handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.vh.shutdown()
        cls.vhsys_base.stop()
        cls.env.stop()

    def setUp(self):
        VhsysHttpFalso.puts, VhsysHttpFalso.posts = [], []
        VhsysHttpFalso.falhar = False

    def post(self, corpo, token="segredo"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.srv.server_port}/api/webhooks/asaas",
            data=json.dumps(corpo).encode(), method="POST",
            headers={"Content-Type": "application/json", "asaas-access-token": token})
        abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with abridor.open(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_token_errado_e_recusado(self):
        status, _ = self.post(evento(externalReference="10"), token="outro")
        self.assertEqual(status, 401)
        self.assertEqual(VhsysHttpFalso.puts, [])

    def test_baixa_de_ponta_a_ponta(self):
        with mock.patch("sys.stdout"):
            status, corpo = self.post(evento(externalReference="10"))
        self.assertEqual((status, corpo["resultado"]), (200, "liquidada"))
        caminho, body = VhsysHttpFalso.puts[0]
        self.assertEqual(caminho, "/contas-receber/10")
        self.assertEqual(body["liquidado_rec"], "Sim")
        self.assertEqual(body["data_pagamento"], "2026-10-11")
        caminho, despesa = VhsysHttpFalso.posts[0]
        self.assertEqual((caminho, despesa["valor_pag"], despesa["id_categoria"]),
                         ("/contas-pagar", "1.99", "9653020"))
        self.assertEqual(corpo["taxa"]["status"], "lancada")

    def test_vhsys_fora_do_ar_pede_reenvio(self):
        VhsysHttpFalso.falhar = True
        with mock.patch("sys.stdout"):
            status, _ = self.post(evento(externalReference="10"))
        self.assertEqual(status, 500)


if __name__ == "__main__":
    unittest.main()
