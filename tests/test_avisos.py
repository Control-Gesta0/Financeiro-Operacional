"""Testes do desligamento dos avisos do Asaas para os clientes antigos.

    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api" / "_lib"))
import asaas_api  # noqa: E402
import avisos  # noqa: E402
from unittest import mock  # noqa: E402


class ErroFalso(Exception):
    pass


class Asaas:
    ErroAsaas = ErroFalso

    def __init__(self, clientes, falhar=()):
        self.clientes = {c["id"]: dict(c) for c in clientes}
        self.falhar, self.alteracoes = set(falhar), []

    def listar_clientes(self):
        return [dict(c) for c in self.clientes.values()]

    def atualizar_cliente(self, id_cliente, dados):
        if id_cliente in self.falhar:
            raise ErroFalso("HTTP 400")
        self.alteracoes.append((id_cliente, dados))
        self.clientes[id_cliente].update(dados)


def clientes(n, desligados=0):
    return [{"id": f"cus_{i}", "name": f"Cliente {i}", "notificationDisabled": i < desligados}
            for i in range(n)]


class TestAvisos(unittest.TestCase):
    def test_previa_nao_altera(self):
        a = Asaas(clientes(5, desligados=2))
        r = avisos.desligar(a)
        self.assertEqual((r["clientes"], r["com_avisos_ligados"], r["aplicado"]), (5, 3, False))
        self.assertEqual(a.alteracoes, [])

    def test_aplica_em_lotes_ate_zerar(self):
        a = Asaas(clientes(5))
        r = avisos.desligar(a, aplicar=True, lote=3)
        self.assertEqual((r["desligados_agora"], r["faltam"]), (3, 2))
        self.assertEqual(a.alteracoes[0], ("cus_0", {"notificationDisabled": True}))
        r = avisos.desligar(a, aplicar=True, lote=3)
        self.assertEqual((r["desligados_agora"], r["faltam"]), (2, 0))
        r = avisos.desligar(a, aplicar=True, lote=3)
        self.assertEqual((r["com_avisos_ligados"], r["desligados_agora"]), (0, 0))

    def test_erro_em_um_cliente_nao_trava_os_outros(self):
        a = Asaas(clientes(3), falhar={"cus_1"})
        r = avisos.desligar(a, aplicar=True)
        self.assertEqual(r["ids_desligados"], ["cus_0", "cus_2"])
        self.assertEqual(r["erros"][0]["id"], "cus_1")
        self.assertEqual(r["faltam"], 1)


class TestListarClientes(unittest.TestCase):
    def test_pagina_ate_acabar_e_ignora_excluidos(self):
        paginas = [{"data": [{"id": "a"}, {"id": "b", "deleted": True}], "hasMore": True},
                   {"data": [{"id": "c"}], "hasMore": False}]
        chamadas = []

        def get(caminho, params):
            chamadas.append(params["offset"])
            return paginas[len(chamadas) - 1]

        with mock.patch.object(asaas_api, "_get", get):
            self.assertEqual([c["id"] for c in asaas_api.listar_clientes()], ["a", "c"])
        self.assertEqual(chamadas, [0, 2])


if __name__ == "__main__":
    unittest.main()
