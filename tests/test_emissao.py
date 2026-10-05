"""Testes da emissão automática de cobranças no Asaas a partir do VHSYS.

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
import emissao  # noqa: E402

HOJE, DESDE = "2026-10-05", "2026-10-05"


class ErroAsaasFalso(Exception):
    pass


class Vhsys:
    def __init__(self, receitas, clientes, ignora_obs=False):
        self.receitas = {r["id_conta_rec"]: dict(r) for r in receitas}
        self.clientes = clientes
        self.ignora_obs = ignora_obs
        self.atualizacoes = []

    def receitas_modificadas_desde(self, data):
        return list(self.receitas.values())

    def consultar_cliente(self, id_cliente):
        return self.clientes.get(id_cliente)

    def consultar_receita(self, id_receita):
        return self.receitas.get(id_receita)

    def atualizar_receita(self, id_receita, campos):
        self.atualizacoes.append((id_receita, campos))
        if not self.ignora_obs:
            self.receitas[id_receita].update(campos)


class Asaas:
    ErroAsaas = ErroAsaasFalso

    def __init__(self, clientes=None, cobrancas=None, recusar_cpf=None):
        self.clientes = clientes or []
        self.cobrancas = cobrancas or []
        self.recusar_cpf = recusar_cpf
        self.clientes_criados, self.clientes_atualizados, self.cobrancas_criadas = [], [], []

    def buscar_cliente_por_documento(self, doc):
        return next((c for c in self.clientes if c["cpfCnpj"] == doc), None)

    def criar_cliente(self, dados):
        if dados["cpfCnpj"] == self.recusar_cpf:
            raise ErroAsaasFalso("HTTP 400 CPF inválido")
        novo = {**dados, "id": f"cus_{len(self.clientes) + 1}"}
        self.clientes.append(novo)
        self.clientes_criados.append(dados)
        return novo

    def atualizar_cliente(self, id_cliente, dados):
        self.clientes_atualizados.append((id_cliente, dados))

    def buscar_cobranca_por_referencia(self, ref):
        return next((c for c in self.cobrancas if c["externalReference"] == ref), None)

    def criar_cobranca(self, dados):
        n = len(self.cobrancas) + 1
        nova = {**dados, "id": f"pay_novo{n}", "invoiceNumber": f"9000{n}",
                "bankSlipUrl": f"https://www.asaas.com/b/pdf/novo{n}"}
        self.cobrancas.append(nova)
        self.cobrancas_criadas.append(dados)
        return nova


def receita(id_, banco="1320902", cad="2026-10-05 09:00:00", venc="2026-10-20", valor="350.00",
            obs="", liquidado="Nao", cliente=77):
    return {"id_conta_rec": id_, "id_banco": banco, "data_cad_rec": cad, "vencimento_rec": venc,
            "valor_rec": valor, "observacoes_rec": obs, "liquidado_rec": liquidado,
            "id_cliente": cliente, "nome_cliente": "CSL DISTRIBUIDORA",
            "nome_conta": "Mensalidade outubro"}


CLIENTE = {"id_cliente": 77, "razao_cliente": "CSL DISTRIBUIDORA LTDA",
           "cnpj_cliente": "12.345.678/0001-90", "email_cliente": "fin@csl.com.br",
           "celular_cliente": "(11) 98888-7777", "cep_cliente": "01310-100",
           "endereco_cliente": "Av. Paulista", "numero_cliente": "1000",
           "bairro_cliente": "Bela Vista"}


def rodar(vhsys, asaas, aplicar=True):
    return emissao.emitir(vhsys, asaas, aplicar=aplicar, desde=DESDE, dia=HOJE)


class TestEmissao(unittest.TestCase):
    def test_emite_boleto_com_cliente_novo_e_sem_avisos(self):
        v, a = Vhsys([receita(1)], {77: CLIENTE}), Asaas()
        r = rodar(v, a)
        self.assertEqual(r["resumo"], {"emitida": 1})
        cli = a.clientes_criados[0]
        self.assertEqual((cli["cpfCnpj"], cli["name"], cli["notificationDisabled"]),
                         ("12345678000190", "CSL DISTRIBUIDORA LTDA", True))
        self.assertEqual((cli["mobilePhone"], cli["postalCode"]), ("11988887777", "01310100"))
        cob = a.cobrancas_criadas[0]
        self.assertEqual(cob, {"customer": "cus_1", "billingType": "BOLETO", "value": 350.0,
                               "dueDate": "2026-10-20", "description": "Mensalidade outubro",
                               "externalReference": "1"})
        res = r["resultados"][0]
        self.assertEqual((res["cobranca"], res["link_gravado"]), ("pay_novo1", True))
        self.assertIn("Cobrança Asaas pay_novo1 (fatura 90001). Boleto: https://www.asaas.com/b/pdf/novo1",
                      v.receitas[1]["observacoes_rec"])

    def test_cliente_existente_tem_os_avisos_desligados(self):
        a = Asaas(clientes=[{"id": "cus_9", "cpfCnpj": "12345678000190",
                             "notificationDisabled": False}])
        r = rodar(Vhsys([receita(1)], {77: CLIENTE}), a)
        self.assertEqual(a.clientes_criados, [])
        self.assertEqual(a.clientes_atualizados, [("cus_9", {"notificationDisabled": True})])
        self.assertEqual(a.cobrancas_criadas[0]["customer"], "cus_9")
        self.assertEqual(r["resultados"][0]["cliente_asaas"], "existente, avisos do Asaas desligados")

    def test_nao_duplica_se_a_cobranca_ja_existe_no_asaas(self):
        a = Asaas(cobrancas=[{"id": "pay_old", "externalReference": "1", "invoiceNumber": "5",
                              "bankSlipUrl": "https://b/old"}])
        v = Vhsys([receita(1)], {77: CLIENTE})
        r = rodar(v, a)
        self.assertEqual((r["resumo"], a.cobrancas_criadas), ({"ja_emitida": 1}, []))
        self.assertIn("pay_old", v.receitas[1]["observacoes_rec"])
        # Rodar de novo: a receita já tem a cobrança nas observações e sai do escopo.
        self.assertEqual(rodar(v, a)["resultados"], [])

    def test_avisa_quando_o_vhsys_nao_grava_a_observacao(self):
        v, a = Vhsys([receita(1)], {77: CLIENTE}, ignora_obs=True), Asaas()
        r = rodar(v, a)
        self.assertIs(r["resultados"][0]["link_gravado"], False)
        self.assertEqual(r["resultados"][0]["boleto"], "https://www.asaas.com/b/pdf/novo1")

    def test_mantem_o_que_ja_estava_nas_observacoes(self):
        v, a = Vhsys([receita(1, obs="Pedido 1402")], {77: CLIENTE}), Asaas()
        rodar(v, a)
        self.assertTrue(v.receitas[1]["observacoes_rec"].startswith("Pedido 1402\nCobrança Asaas"))

    def test_fora_do_escopo_nem_aparece(self):
        v = Vhsys([receita(1, banco="999"), receita(2, cad="2026-09-30 10:00:00"),
                   receita(3, obs="Cobranca em aberto no Asaas (pay_abc)"),
                   receita(4, liquidado="Sim")], {77: CLIENTE})
        a = Asaas()
        r = rodar(v, a)
        self.assertEqual((r["resultados"], a.cobrancas_criadas), ([], []))

    def test_diagnostico_mostra_as_ignoradas_com_o_motivo(self):
        v = Vhsys([receita(1, banco="999"), receita(2, cad="2026-09-30 10:00:00")], {77: CLIENTE})
        r = emissao.emitir(v, Asaas(), desde=DESDE, dia=HOJE, diagnostico=True)
        self.assertEqual({x["receita"]: x["motivo"] for x in r["ignoradas"]},
                         {1: "outra conta bancária", 2: "cadastrada antes do início da emissão"})
        self.assertEqual(r["conta_asaas"], "1320902")

    def test_aceita_data_no_formato_brasileiro(self):
        v, a = Vhsys([receita(1, cad="05/10/2026 09:00", venc="20/10/2026")], {77: CLIENTE}), Asaas()
        r = rodar(v, a, aplicar=False)
        self.assertEqual(r["resumo"], {"seria_emitida": 1})

    def test_vencida_e_valor_zerado_aparecem_como_nao_emitidas(self):
        v = Vhsys([receita(1, venc="2026-10-01"), receita(2, valor="0.00")], {77: CLIENTE})
        r = rodar(v, Asaas())
        motivos = {x["receita"]: x["motivo"] for x in r["resultados"]}
        self.assertIn("vencida", motivos[1])
        self.assertEqual(motivos[2], "valor zerado")

    def test_cliente_sem_documento_ou_recusado_nao_trava_os_demais(self):
        sem_doc = {**CLIENTE, "id_cliente": 78, "cnpj_cliente": ""}
        recusado = {**CLIENTE, "id_cliente": 79, "cnpj_cliente": "111"}
        v = Vhsys([receita(1, cliente=78), receita(2, cliente=79), receita(3)],
                  {77: CLIENTE, 78: sem_doc, 79: recusado})
        a = Asaas(recusar_cpf="111")
        r = rodar(v, a)
        por_receita = {x["receita"]: x for x in r["resultados"]}
        self.assertIn("sem CPF/CNPJ", por_receita[1]["motivo"])
        self.assertIn("CPF inválido", por_receita[2]["motivo"])
        self.assertEqual(por_receita[3]["resultado"], "emitida")

    def test_simulacao_nao_grava_nada(self):
        v, a = Vhsys([receita(1)], {77: CLIENTE}), Asaas()
        r = rodar(v, a, aplicar=False)
        res = r["resultados"][0]
        self.assertEqual((res["resultado"], res["cliente_asaas"]), ("seria_emitida", "seria cadastrado"))
        self.assertEqual(res["cobranca"]["value"], 350.0)
        self.assertEqual((a.clientes_criados, a.cobrancas_criadas, v.atualizacoes), ([], [], []))

    def test_sem_data_de_inicio_recusa(self):
        with mock.patch.dict(os.environ, {"EMISSAO_A_PARTIR_DE": ""}):
            with self.assertRaises(ValueError):
                emissao.emitir(Vhsys([], {}), Asaas())


class TestRota(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {"CRON_SECRET": "cron", "EMISSAO_MODO": "",
                                               "EMISSAO_A_PARTIR_DE": "",
                                               "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        cls.env.start()
        import emitir as rota  # api/emitir.py (o handler)
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), rota.handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.env.stop()

    def get(self, query):
        abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with abridor.open(f"http://127.0.0.1:{self.srv.server_port}/api/emitir{query}",
                              timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_sem_chave(self):
        self.assertEqual(self.get("")[0], 401)

    def test_aplicar_com_emissao_em_simulacao_e_recusado(self):
        status, corpo = self.get("?chave=cron&aplicar=1")
        self.assertEqual(status, 409)
        self.assertIn("EMISSAO_MODO=ativo", corpo["erro"])

    def test_previa_sem_data_de_inicio_pede_a_data(self):
        status, corpo = self.get("?chave=cron")
        self.assertEqual(status, 400)
        self.assertIn("EMISSAO_A_PARTIR_DE", corpo["erro"])


if __name__ == "__main__":
    unittest.main()
