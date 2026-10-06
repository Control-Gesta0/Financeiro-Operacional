"""Testes do atendimento automático do WhatsApp (Zaptos + ERP Lite + Asaas + Claude).

    python3 -m unittest discover -s tests
"""
import datetime as dt
import http.server
import json
import os
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "api" / "_lib"))
sys.path.insert(0, str(RAIZ / "api" / "webhooks"))
import atendimento  # noqa: E402
import whatsapp  # noqa: E402

AGORA = dt.datetime(2026, 10, 7, 14, 0, tzinfo=dt.timezone.utc)  # 11h em Brasília
NUMERO = "5511988887777"
CHAT = f"{NUMERO}@s.whatsapp.net"
OBS = "Cobrança Asaas pay_abc (fatura 930). Boleto: https://www.asaas.com/b/pdf/abc"


class Vhsys:
    def __init__(self, clientes, receitas):
        self.clientes, self.receitas = clientes, receitas
        self.varreduras = 0

    def listar_clientes(self):
        self.varreduras += 1
        return list(self.clientes)

    def consultar_cliente(self, id_cliente):
        return next((c for c in self.clientes if str(c["id_cliente"]) == str(id_cliente)), None)

    def receitas_do_cliente(self, id_cliente):
        return [r for r in self.receitas if str(r["id_cliente"]) == str(id_cliente)]


class Asaas:
    ErroAsaas = ValueError

    def __init__(self, status=None):
        self.status = status or {}

    def consultar_cobranca(self, pid):
        return {"id": pid, "status": self.status.get(pid, "PENDING"), "invoiceNumber": "930",
                "invoiceUrl": f"https://www.asaas.com/i/{pid[4:]}",
                "bankSlipUrl": f"https://www.asaas.com/b/pdf/{pid[4:]}"}


class Zaptos:
    """Registra o que o atendimento faria na Zaptos."""

    def __init__(self, chat=None, sem_whatsapp=()):
        self.chat = chat if chat is not None else {}
        self.textos, self.pdfs, self.edicoes = [], [], []
        self.sem_whatsapp = set(sem_whatsapp)

    def patches(self):
        return [mock.patch.object(whatsapp, "enviar_texto", lambda n, t: self.textos.append((n, t))),
                mock.patch.object(whatsapp, "enviar_documento",
                                  lambda n, u, nome, legenda=None: self.pdfs.append((n, u, nome))),
                mock.patch.object(whatsapp, "editar_chat",
                                  lambda c, campos: (self.edicoes.append((c, campos)),
                                                     self.chat.update(campos))),
                mock.patch.object(whatsapp, "detalhes_chat", lambda n: dict(self.chat)),
                mock.patch.object(whatsapp, "historico", lambda c, limite=10: [
                    {"messageid": "m0", "fromMe": True, "text": "Bom dia!", "messageTimestamp": 1},
                    {"messageid": "m1", "fromMe": False, "text": "oi", "messageTimestamp": 2}]),
                mock.patch.object(whatsapp, "destino_verificado",
                                  lambda n: None if n in self.sem_whatsapp else n)]


CLIENTE = {"id_cliente": 77, "razao_cliente": "CSL DISTRIBUIDORA LTDA",
           "celular_cliente": "(11) 8888-7777", "fone_cliente": "", "cnpj_cliente": "11.222.333/0001-81"}


def receita(id_, venc, liquidado="Nao", obs=OBS, pago=None, valor="350.00"):
    return {"id_conta_rec": id_, "id_cliente": 77, "nome_conta": f"Mensalidade {id_}",
            "valor_rec": valor, "vencimento_rec": venc, "liquidado_rec": liquidado,
            "lixeira": "Nao", "observacoes_rec": obs, "data_pagamento": pago, "valor_pago": valor}


def evento(texto="quero a segunda via do boleto", **msg):
    base = {"messageid": "m2", "chatid": CHAT, "fromMe": False, "isGroup": False,
            "messageType": "Conversation", "text": texto, "messageTimestamp": 3}
    base.update(msg)
    return {"EventType": "messages", "message": base}


def rodar(payload, vhsys=None, asaas=None, decisao=None, zaptos=None, modo="ativo", env=None):
    zaptos = zaptos or Zaptos()
    vhsys = vhsys or Vhsys([CLIENTE], [receita(1, "2026-10-20")])
    chamadas = []

    def ia(contexto):
        chamadas.append(contexto)
        return decisao or {"assunto": "outro", "acao": "nao_responder", "resposta": "",
                           "enviar_pdfs": []}

    patches = zaptos.patches() + [mock.patch.dict(os.environ, {
        "ATENDIMENTO_MODO": modo, "ATENDIMENTO_TESTE": "", "CONCILIACAO_WHATSAPP": "",
        "ATENDIMENTO_ENCAMINHAR": "", **(env or {})})]
    for p in patches:
        p.start()
    try:
        r = atendimento.processar(payload, vhsys, asaas or Asaas(), ia=ia, agora=AGORA)
    finally:
        for p in patches:
            p.stop()
    return r, zaptos, chamadas


class TestDocumento(unittest.TestCase):
    def test_cpf_e_cnpj_validos_com_ou_sem_pontuacao(self):
        self.assertEqual(atendimento.documento_na_mensagem("meu cnpj é 11.222.333/0001-81"),
                         "11222333000181")
        self.assertEqual(atendimento.documento_na_mensagem("CPF 529.982.247-25 ok"), "52998224725")
        self.assertIsNone(atendimento.documento_na_mensagem("11.222.333/0001-82"))
        self.assertIsNone(atendimento.documento_na_mensagem("111.111.111-11"))
        self.assertIsNone(atendimento.documento_na_mensagem("boleto de 350,00 vence 20/10"))

    def test_variantes_do_nono_digito(self):
        self.assertEqual(atendimento.variantes("(11) 98888-7777"), {"5511988887777", "551188887777"})
        self.assertEqual(atendimento.variantes("551188887777"), {"551188887777", "5511988887777"})
        self.assertEqual(atendimento.variantes("1144181102"), {"551144181102"})


class TestFiltros(unittest.TestCase):
    def test_resposta_da_equipe_pelo_celular_pausa_12_horas(self):
        r, z, ia = rodar(evento("já te mando", fromMe=True))
        self.assertEqual(r["acao"], "pausado")
        limite = int((AGORA + dt.timedelta(hours=12)).timestamp())
        self.assertEqual(z.edicoes, [(CHAT, {"chatbot_disableUntil": limite})])
        self.assertEqual((z.textos, ia), ([], []))

    def test_desligado_nao_faz_nada(self):
        r, z, ia = rodar(evento(), modo="")
        self.assertEqual((r["acao"], z.textos, ia), ("ignorado", [], []))

    def test_modo_teste_so_responde_aos_numeros_de_teste(self):
        r, _, ia = rodar(evento(), modo="teste", env={"ATENDIMENTO_TESTE": "5511900001111"})
        self.assertEqual((r["motivo"], ia), ("modo teste: número fora da lista", []))
        r, _, ia = rodar(evento(), modo="teste", env={"CONCILIACAO_WHATSAPP": "+55 11 98888-7777"})
        self.assertEqual(len(ia), 1)

    def test_conversa_pausada_nao_responde(self):
        z = Zaptos({"chatbot_disableUntil": int(AGORA.timestamp()) + 60})
        r, z, ia = rodar(evento(), zaptos=z)
        self.assertEqual((r["motivo"], ia), ("atendimento humano em andamento", []))

    def test_pausa_vencida_volta_a_responder(self):
        z = Zaptos({"chatbot_disableUntil": int(AGORA.timestamp()) - 60})
        _, _, ia = rodar(evento(), zaptos=z)
        self.assertEqual(len(ia), 1)

    def test_mesma_mensagem_duas_vezes_responde_uma(self):
        z = Zaptos()
        rodar(evento(), zaptos=z)
        r, _, ia = rodar(evento(), zaptos=z)
        self.assertEqual((r["motivo"], ia), ("mensagem já tratada", []))

    def test_grupo_audio_e_outros_eventos(self):
        self.assertEqual(rodar(evento(isGroup=True))[0]["acao"], "ignorado")
        self.assertEqual(rodar(evento("", messageType="AudioMessage"))[0]["motivo"],
                         "mensagem sem texto (áudio, imagem...)")
        self.assertEqual(rodar({"EventType": "connection"})[0]["acao"], "ignorado")
        self.assertEqual(rodar(evento(wasSentByApi=True))[0]["acao"], "ignorado")


class TestIdentificacao(unittest.TestCase):
    def test_acha_pelo_celular_sem_nono_digito_e_guarda_o_vinculo(self):
        v = Vhsys([CLIENTE], [receita(1, "2026-10-20")])
        z = Zaptos()
        _, _, ia = rodar(evento(), vhsys=v, zaptos=z)
        self.assertTrue(ia[0]["cliente_identificado"])
        self.assertEqual(z.chat["lead_field19"], "77")
        rodar(evento(messageid="m3"), vhsys=v, zaptos=z)
        self.assertEqual(v.varreduras, 1)  # a segunda mensagem usa o vínculo guardado

    def test_cliente_ligado_que_foi_para_a_lixeira_refaz_a_busca(self):
        apagado = dict(CLIENTE, id_cliente=78, lixeira="Sim")
        v = Vhsys([CLIENTE, apagado], [receita(1, "2026-10-20")])
        v.listar_clientes = lambda: (setattr(v, "varreduras", v.varreduras + 1), [CLIENTE])[1]
        z = Zaptos({"lead_field19": "77,78"})
        _, _, ia = rodar(evento(), vhsys=v, zaptos=z)
        self.assertEqual((v.varreduras, z.chat["lead_field19"]), (1, "77"))
        self.assertEqual(len(ia[0]["dados_do_cliente"]), 1)

    def test_desconhecido_varre_no_maximo_uma_vez_por_dia(self):
        v = Vhsys([], [])
        z = Zaptos()
        _, _, ia = rodar(evento(), vhsys=v, zaptos=z)
        self.assertEqual((ia[0]["cliente_identificado"], ia[0]["dados_do_cliente"]), (False, []))
        self.assertEqual(z.chat["lead_field19"], "nao:2026-10-07")
        rodar(evento(messageid="m3"), vhsys=v, zaptos=z)
        self.assertEqual(v.varreduras, 1)

    def test_cnpj_de_numero_desconhecido_nao_mostra_dados(self):
        for clientes in ([dict(CLIENTE, celular_cliente="")], []):  # achando ou não
            v = Vhsys(clientes, [receita(1, "2026-10-20")])
            r, z, ia = rodar(evento("11.222.333/0001-81"), vhsys=v)
            self.assertEqual(ia, [])  # nem chega à IA
            self.assertEqual(z.textos, [(NUMERO, atendimento.RESPOSTA_DOCUMENTO)])
            self.assertNotIn("350", z.textos[0][1])
            self.assertIn("chatbot_disableUntil", z.chat)


class TestDados(unittest.TestCase):
    def test_boletos_com_link_situacao_e_pagamentos_recentes(self):
        receitas = [receita(1, "2026-10-20"), receita(2, "2026-10-01", obs="sem cobrança"),
                    receita(3, "2026-10-25", obs="Cobranca em aberto no Asaas (pay_pago)"),
                    receita(4, "2026-09-10", liquidado="Sim", pago="2026-09-12"),
                    receita(5, "2026-05-10", liquidado="Sim", pago="2026-05-12")]
        _, _, ia = rodar(evento(), vhsys=Vhsys([CLIENTE], receitas),
                         asaas=Asaas({"pay_pago": "RECEIVED"}))
        dados = ia[0]["dados_do_cliente"][0]
        boletos = {b["id"]: b for b in dados["boletos_em_aberto"]}
        self.assertEqual([b["id"] for b in dados["boletos_em_aberto"]], [2, 1, 3])
        self.assertEqual((boletos[1]["valor"], boletos[1]["vencimento"], boletos[1]["link"],
                          boletos[1]["pdf"]),
                         ("R$ 350,00", "20/10/2026", "https://www.asaas.com/i/abc", True))
        self.assertEqual((boletos[2]["situacao"], boletos[2]["link"]), ("vencido", None))
        self.assertEqual((boletos[3]["situacao"], boletos[3]["pdf"]),
                         ("pago, aguardando baixa no sistema", False))
        self.assertEqual(dados["pagamentos_recentes"],
                         [{"descricao": "Mensalidade 4", "valor": "R$ 350,00", "pago_em": "12/09/2026"}])
        self.assertEqual(ia[0]["conversa_recente"],
                         [{"de": "financeiro", "texto": "Bom dia!"}, {"de": "cliente", "texto": "oi"}])


class TestAcoes(unittest.TestCase):
    def test_responder_com_pdf_so_dos_boletos_validos(self):
        decisao = {"assunto": "financeiro", "acao": "responder",
                   "resposta": "Segue o boleto de R$ 350,00.", "enviar_pdfs": [1, 99]}
        r, z, _ = rodar(evento(), decisao=decisao)
        self.assertEqual(z.textos, [(NUMERO, "Segue o boleto de R$ 350,00.")])
        self.assertEqual(z.pdfs, [(NUMERO, "https://www.asaas.com/b/pdf/abc", "boleto-930.pdf")])
        self.assertEqual(r["mensagens"], ["resposta", "pdf 1"])

    def test_tecnico_responde_e_encaminha_para_o_4418(self):
        decisao = {"assunto": "tecnico", "acao": "encaminhar", "enviar_pdfs": [],
                   "resposta": "Esse assunto é com a equipe técnica no (11) 4418-1102."}
        r, z, _ = rodar(evento("o sistema não abre"), decisao=decisao)
        (n1, t1), (n2, t2) = z.textos
        self.assertEqual((n1, n2), (NUMERO, "551144181102"))
        self.assertIn("Cliente: CSL DISTRIBUIDORA LTDA", t2)
        self.assertIn("WhatsApp: +5511988887777", t2)
        self.assertIn("Mensagem: o sistema não abre", t2)
        self.assertEqual(r["encaminhado_para"], "5511****1102")

    def test_no_teste_o_encaminhamento_volta_para_o_numero_de_teste(self):
        decisao = {"assunto": "comercial", "acao": "encaminhar", "enviar_pdfs": [],
                   "resposta": "Vou repassar para o comercial."}
        _, z, _ = rodar(evento("quero um orçamento"), decisao=decisao, modo="teste",
                        env={"ATENDIMENTO_TESTE": NUMERO})
        self.assertEqual(z.textos[1][0], NUMERO)
        self.assertTrue(z.textos[1][1].startswith("[TESTE: iria para +551144181102]"))

    def test_passar_para_financeiro_pausa_a_conversa(self):
        decisao = {"assunto": "financeiro", "acao": "passar_para_financeiro", "enviar_pdfs": [],
                   "resposta": "Vou passar para a equipe financeira."}
        r, z, _ = rodar(evento("quero renegociar"), decisao=decisao)
        self.assertTrue(r["pausado"])
        self.assertGreater(z.chat["chatbot_disableUntil"], AGORA.timestamp())

    def test_nao_responder_nao_envia(self):
        r, z, _ = rodar(evento("ok obrigado"))
        self.assertEqual((r["acao"], z.textos), ("nao_responder", []))


class TestIA(unittest.TestCase):
    def _cliente_falso(self, stop_reason="end_turn", texto='{"acao": "responder"}'):
        chamadas = []

        class Mensagens:
            def create(self, **kwargs):
                chamadas.append(kwargs)
                return types.SimpleNamespace(
                    stop_reason=stop_reason,
                    content=[types.SimpleNamespace(type="thinking"),
                             types.SimpleNamespace(type="text", text=texto)])

        falso = types.SimpleNamespace(beta=types.SimpleNamespace(messages=Mensagens()))
        return falso, chamadas

    def test_pedido_ao_claude(self):
        import anthropic
        falso, chamadas = self._cliente_falso()
        with mock.patch.object(anthropic, "Anthropic", lambda **k: falso), \
                mock.patch.dict(os.environ, {"ATENDIMENTO_MODELO": ""}):
            self.assertEqual(atendimento.consultar_ia({"x": 1}), {"acao": "responder"})
        pedido = chamadas[0]
        self.assertEqual(pedido["model"], "claude-opus-5-5")
        self.assertEqual((pedido["fallbacks"], pedido["betas"]),
                         ("default", ["server-side-fallback-2026-07-01"]))
        self.assertEqual(pedido["output_config"]["effort"], "low")
        self.assertEqual(pedido["output_config"]["format"]["schema"], atendimento.ESQUEMA)
        self.assertEqual(json.loads(pedido["messages"][0]["content"]), {"x": 1})

    def test_recusa_vira_erro(self):
        import anthropic
        falso, _ = self._cliente_falso(stop_reason="refusal", texto="")
        with mock.patch.object(anthropic, "Anthropic", lambda **k: falso):
            with self.assertRaises(atendimento.ErroIA):
                atendimento.consultar_ia({})


class TestRota(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {"CRON_SECRET": "cron", "NO_PROXY": "127.0.0.1",
                                               "no_proxy": "127.0.0.1"})
        cls.env.start()
        import zaptos as rota  # api/webhooks/zaptos.py (o handler)
        cls.rota = rota
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), rota.handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.env.stop()

    def post(self, chave, corpo):
        from autorizacao import token_webhook_whatsapp
        chave = token_webhook_whatsapp() if chave is True else chave
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.srv.server_port}/api/webhooks/zaptos?chave={chave}",
            data=corpo, method="POST", headers={"Content-Type": "application/json"})
        abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with mock.patch("sys.stdout"), abridor.open(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_chave_errada(self):
        self.assertEqual(self.post("cron", b"{}")[0], 401)  # a CRON_SECRET pura não serve

    def test_trata_e_responde_200_mesmo_com_erro(self):
        with mock.patch.object(self.rota.atendimento, "processar",
                               side_effect=RuntimeError("Zaptos fora")):
            status, corpo = self.post(True, json.dumps(evento()).encode())
        self.assertEqual((status, corpo["acao"]), (200, "erro"))

    def test_json_invalido(self):
        self.assertEqual(self.post(True, b"{nao")[0], 400)


if __name__ == "__main__":
    unittest.main()
