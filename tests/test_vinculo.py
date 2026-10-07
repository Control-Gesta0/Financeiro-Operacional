"""Testes do vínculo das receitas migradas com as cobranças que já existem no Asaas.

    python3 -m unittest discover -s tests
"""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock
import os

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api" / "_lib"))
import vinculo  # noqa: E402

HOJE = dt.date(2026, 10, 7)


class Vhsys:
    def __init__(self, receitas, clientes):
        self.receitas = {r["id_conta_rec"]: dict(r) for r in receitas}
        self.clientes = clientes

    def receitas_em_aberto(self):
        return [dict(r) for r in self.receitas.values()]

    def consultar_cliente(self, id_cliente):
        return self.clientes.get(id_cliente)

    def consultar_receita(self, id_receita):
        return dict(self.receitas[id_receita])

    def atualizar_receita(self, id_receita, campos):
        self.receitas[id_receita].update(campos)


class Asaas:
    def __init__(self, clientes, cobrancas):
        self.clientes, self.cobrancas = clientes, cobrancas

    def buscar_cliente_por_documento(self, doc):
        return self.clientes.get(doc)

    def cobrancas_do_cliente(self, cus):
        return [c for c in self.cobrancas if c["customer"] == cus]


def receita(id_, valor, venc, obs="Valor de R$ 100,00 (Parcela 1 de 3).", cliente=7,
            banco="1320902", original=None):
    return {"id_conta_rec": id_, "valor_rec": valor, "vencimento_rec": venc,
            "vencimento_original": original or venc, "observacoes_rec": obs, "id_banco": banco,
            "id_cliente": cliente, "nome_cliente": "VTEC CLIENTE"}


def cobranca(id_, valor, venc, status="PENDING", cus="cus_7"):
    return {"id": id_, "customer": cus, "value": valor, "dueDate": venc, "status": status,
            "invoiceNumber": id_[4:]}


def rodar(receitas, cobrancas, aplicar=False, clientes=None):
    v = Vhsys(receitas, clientes if clientes is not None else
              {7: {"cnpj_cliente": "11.222.333/0001-81"}})
    a = Asaas({"11222333000181": {"id": "cus_7"}}, cobrancas)
    with mock.patch.dict(os.environ, {"VHSYS_ID_BANCO_ASAAS": ""}):
        return vinculo.analisar(v, a, aplicar=aplicar, hoje=HOJE), v


class TestVinculo(unittest.TestCase):
    def test_classifica_cada_receita(self):
        receitas = [receita(1, "100.00", "2026-10-10"), receita(2, "100.00", "2026-11-10"),
                    receita(3, "100.00", "2026-12-10"), receita(4, "250.00", "2026-10-20"),
                    receita(5, "80.00", "2026-10-05", original="2026-09-30"),
                    receita(6, "50.00", "2026-10-01", obs="Cobranca em aberto no Asaas (pay_ja)"),
                    receita(7, "60.00", "2026-10-01", banco="999")]
        cobrancas = [cobranca("pay_out", "100.0", "2026-10-10"),
                     cobranca("pay_nov", 100, "2026-11-10", status="RECEIVED"),
                     cobranca("pay_a", 250, "2026-10-20"), cobranca("pay_b", 250, "2026-10-20"),
                     cobranca("pay_orig", 80, "2026-09-30", status="OVERDUE"),
                     cobranca("pay_ja", 50, "2026-10-01")]
        r, _ = rodar(receitas, cobrancas)
        por_id = {x["receita"]: x for x in r["resultados"]}
        self.assertEqual(set(por_id), {1, 2, 3, 4, 5})  # 6 já ligada, 7 é de outra conta
        self.assertEqual((por_id[1]["situacao"], por_id[1]["cobranca"]), ("vinculavel", "pay_out"))
        self.assertEqual(por_id[2]["situacao"], "paga_no_asaas")
        self.assertEqual(por_id[3]["situacao"], "sem_cobranca")
        self.assertEqual(por_id[4]["situacao"], "ambigua")
        self.assertEqual(por_id[5]["cobranca"], "pay_orig")  # pelo vencimento original
        self.assertEqual(r["resumo"], {"vinculavel": 2, "paga_no_asaas": 1, "sem_cobranca": 1,
                                       "ambigua": 1})
        self.assertEqual(r["valores"]["vinculavel"], "180.00")

    def test_previa_nao_grava_e_aplicar_so_nas_vinculaveis(self):
        receitas = [receita(1, "100.00", "2026-10-10"), receita(2, "100.00", "2026-11-10")]
        cobrancas = [cobranca("pay_out", 100, "2026-10-10"),
                     cobranca("pay_nov", 100, "2026-11-10", status="RECEIVED")]
        r, v = rodar(receitas, cobrancas)
        self.assertNotIn("pay_out", v.receitas[1]["observacoes_rec"])
        r, v = rodar(receitas, cobrancas, aplicar=True)
        self.assertTrue(v.receitas[1]["observacoes_rec"].startswith("Valor de R$ 100,00"))
        self.assertIn("Cobranca em aberto no Asaas (pay_out). Vínculo pela integração em "
                      "07/10/2026 (fatura out).", v.receitas[1]["observacoes_rec"])
        self.assertNotIn("pay_nov", v.receitas[2]["observacoes_rec"])
        item = next(x for x in r["resultados"] if x["receita"] == 1)
        self.assertTrue(item["vinculo_gravado"])

    def test_duas_parcelas_iguais_nao_usam_a_mesma_cobranca(self):
        receitas = [receita(1, "100.00", "2026-10-10"), receita(2, "100.00", "2026-10-10")]
        r, _ = rodar(receitas, [cobranca("pay_unica", 100, "2026-10-10")])
        self.assertEqual(sorted(x["situacao"] for x in r["resultados"]),
                         ["sem_cobranca", "vinculavel"])

    def test_sem_documento_e_sem_cliente_no_asaas(self):
        r, _ = rodar([receita(1, "100.00", "2026-10-10")], [], clientes={7: {"cnpj_cliente": ""}})
        self.assertEqual(r["resumo"], {"sem_documento": 1})
        r, _ = rodar([receita(1, "100.00", "2026-10-10")], [],
                     clientes={7: {"cnpj_cliente": "529.982.247-25"}})
        self.assertEqual(r["resumo"], {"sem_cliente_no_asaas": 1})


if __name__ == "__main__":
    unittest.main()
