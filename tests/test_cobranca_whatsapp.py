"""Testes da cobrança pelo WhatsApp (Zaptos): boleto após a emissão e lembretes.

    python3 -m unittest discover -s tests
"""
import datetime as dt
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
import cobranca_whatsapp as cw  # noqa: E402
import whatsapp  # noqa: E402

HOJE = dt.date(2026, 10, 6)  # terça-feira
DESDE = "2026-10-05"
OBS = "Cobrança Asaas pay_abc (fatura 928695642). Boleto: https://www.asaas.com/b/pdf/abc"


class ErroAsaasFalso(Exception):
    pass


class Vhsys:
    def __init__(self, receitas, clientes):
        self.receitas = {r["id_conta_rec"]: dict(r) for r in receitas}
        self.clientes = clientes

    def receitas_modificadas_desde(self, data):
        return [dict(r) for r in self.receitas.values() if r["liquidado_rec"] == "Nao"]

    def receitas_do_cliente(self, id_cliente):
        self.consultas_cliente = getattr(self, "consultas_cliente", 0) + 1
        return [dict(r) for r in self.receitas.values() if str(r["id_cliente"]) == id_cliente]

    def consultar_receita(self, id_receita):
        return dict(self.receitas[id_receita]) if id_receita in self.receitas else None

    def consultar_cliente(self, id_cliente):
        return self.clientes.get(id_cliente)

    def atualizar_receita(self, id_receita, campos):
        self.receitas[id_receita].update(campos)


class Asaas:
    ErroAsaas = ErroAsaasFalso

    def __init__(self, status="PENDING", sem_pix=False):
        self.status, self.sem_pix = status, sem_pix

    def consultar_cobranca(self, pid):
        return {"id": pid, "status": self.status, "value": 350.0, "invoiceNumber": "928695642",
                "bankSlipUrl": f"https://www.asaas.com/b/pdf/{pid}",
                "invoiceUrl": f"https://www.asaas.com/i/{pid}"}

    def linha_digitavel(self, pid):
        return {"identificationField": "46191110000000000000000000000000000000000000000"}

    def pix_qrcode(self, pid):
        if self.sem_pix:
            raise ErroAsaasFalso("HTTP 400 conta sem chave Pix")
        return {"payload": f"00020126PIX-{pid}"}


def receita(id_=1, venc="2026-10-14", obs=OBS, banco="1320902", liquidado="Nao", cliente=77,
            cad="2026-10-06 09:00:00", ident="0"):
    return {"id_conta_rec": id_, "id_banco": banco, "vencimento_rec": venc, "valor_rec": "350.00",
            "data_cad_rec": cad, "identificacao": ident,
            "observacoes_rec": obs, "liquidado_rec": liquidado, "id_cliente": cliente,
            "nome_cliente": "CSL DISTRIBUIDORA", "nome_conta": "Mensalidade outubro"}


CLIENTE = {"id_cliente": 77, "razao_cliente": "CSL DISTRIBUIDORA LTDA",
           "celular_cliente": "(11) 98888-7777"}


class Zaptos:
    """Substitui o transporte: guarda (tipo, número, conteúdo) de cada mensagem."""

    def __init__(self, falhar=(), sem_whatsapp=(), jid=None):
        self.mensagens, self.falhar = [], set(falhar)
        self.sem_whatsapp, self.jid = set(sem_whatsapp), jid or {}

    def verificar(self, numero):
        return None if numero in self.sem_whatsapp else self.jid.get(numero, numero)

    def texto(self, numero, texto):
        tipo = "pix" if texto.startswith("00020126") else "texto"
        if tipo in self.falhar:
            raise whatsapp.ErroWhatsapp("HTTP 500", 500)
        self.mensagens.append((tipo, numero, texto))

    def documento(self, numero, url, nome, legenda=None):
        if "pdf" in self.falhar:
            raise whatsapp.ErroWhatsapp("HTTP 500 falha ao baixar arquivo", 500)
        self.mensagens.append(("pdf", numero, url, nome))


def rodar(vhsys, asaas, etapas=("boleto",), aplicar=True, hoje=HOJE, falhar=(), **zaptos):
    z = Zaptos(falhar, **zaptos)
    with mock.patch.object(whatsapp, "enviar_texto", z.texto), \
            mock.patch.object(whatsapp, "enviar_documento", z.documento), \
            mock.patch.object(whatsapp, "destino_verificado", z.verificar), \
            mock.patch.dict(os.environ, {"VHSYS_ID_BANCO_ASAAS": "", "LEMBRETE_DIAS_ATRASO": ""}):
        r = cw.cobrar(vhsys, asaas, etapas, aplicar=aplicar, desde=DESDE, hoje=hoje)
    return r, z.mensagens


class TestTelefone(unittest.TestCase):
    def test_formatos(self):
        casos = {"(11) 98888-7777": "5511988887777", "5511988887777": "5511988887777",
                 "011 3333-4444": "551133334444", "98888-7777": None, "": None,
                 "+55 (11) 98888-7777": "5511988887777", "123@g.us": None}
        for entrada, esperado in casos.items():
            self.assertEqual(cw.telefone({"celular_cliente": entrada}), esperado, entrada)

    def test_destino_da_conciliacao(self):
        self.assertEqual(whatsapp.normalizar(" +55 11 98888-7777 "), "5511988887777")
        self.assertEqual(whatsapp.normalizar("120363000000@g.us"), "120363000000@g.us")
        self.assertIsNone(whatsapp.normalizar("98888-7777"))
        self.assertEqual(whatsapp.mascarar("5511988887777"), "5511*****7777")


class TestNome(unittest.TestCase):
    def test_nome_em_maiusculas_fica_legivel(self):
        casos = {"EDUARDO DO VALE": "Eduardo do Vale",
                 "PEDRAZZOLI & PEDRAZZOLI LTDA": "Pedrazzoli & Pedrazzoli LTDA",
                 "MARIA DAS DORES E SILVA": "Maria das Dores e Silva",
                 "Já Escrito Normal": "Já Escrito Normal"}
        for entrada, esperado in casos.items():
            self.assertEqual(cw.nome_cliente({"razao_cliente": entrada}, {}), esperado)


class TestBoleto(unittest.TestCase):
    def test_previa_nao_envia_nem_marca(self):
        v = Vhsys([receita()], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(), aplicar=False)
        self.assertEqual((r["resumo"], msgs), ({"seria_enviado": 1}, []))
        self.assertIn("vencimento em 14/10/2026", r["resultados"][0]["texto"])
        self.assertEqual(v.receitas[1]["observacoes_rec"], OBS)

    def test_envia_texto_e_pdf_sem_pix_separado_e_marca(self):
        v = Vhsys([receita()], {77: CLIENTE})
        r, msgs = rodar(v, Asaas())
        self.assertEqual([m[0] for m in msgs], ["texto", "pdf"])  # o PDF já tem o QR Pix
        self.assertEqual({m[1] for m in msgs}, {"5511988887777"})
        texto = msgs[0][2]
        self.assertIn("Olá, Csl Distribuidora LTDA!", texto)
        self.assertIn("R$ 350,00", texto)
        self.assertIn("Linha digitável:\n4619111", texto)
        self.assertIn("https://www.asaas.com/i/pay_abc", texto)
        self.assertNotIn("Pix copia e cola", texto)
        self.assertEqual(msgs[1][3], "boleto-928695642.pdf")
        res = r["resultados"][0]
        self.assertEqual((res["resultado"], res["marca_gravada"]), ("enviado", True))
        self.assertIn("WhatsApp: boleto enviado em 06/10/2026 (texto, pdf).",
                      v.receitas[1]["observacoes_rec"])
        self.assertTrue(v.receitas[1]["observacoes_rec"].startswith(OBS))

    def test_rodar_de_novo_nao_reenvia(self):
        v = Vhsys([receita()], {77: CLIENTE})
        rodar(v, Asaas())
        r, msgs = rodar(v, Asaas())
        self.assertEqual((r["resultados"], msgs), ([], []))

    def test_conta_sem_pix_manda_o_mesmo(self):
        v = Vhsys([receita()], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(sem_pix=True))
        self.assertEqual([m[0] for m in msgs], ["texto", "pdf"])

    def test_falha_no_pdf_nao_repete_o_texto(self):
        v = Vhsys([receita()], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(), falhar={"pdf"})
        res = r["resultados"][0]
        self.assertEqual((res["resultado"], res["mensagens"]), ("enviado", ["texto"]))
        self.assertIn("pdf", res["falhas"])
        self.assertIn("boleto enviado", v.receitas[1]["observacoes_rec"])

    def test_falha_no_texto_fica_para_o_proximo_cron(self):
        v = Vhsys([receita(1), receita(2)], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(), falhar={"texto"})
        self.assertEqual(r["resumo"], {"erro": 2})
        self.assertNotIn("WhatsApp", v.receitas[1]["observacoes_rec"])

    def test_cobranca_paga_ou_cancelada_nao_envia(self):
        for status in ("RECEIVED", "CONFIRMED", "REFUNDED"):
            v = Vhsys([receita()], {77: CLIENTE})
            r, msgs = rodar(v, Asaas(status=status))
            self.assertEqual((r["resumo"], msgs), ({"nao_enviado": 1}, []), status)

    def test_celular_sem_whatsapp_nao_envia_nem_marca(self):
        v = Vhsys([receita()], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(), sem_whatsapp={"5511988887777"})
        self.assertEqual((r["resumo"], msgs), ({"sem_whatsapp": 1}, []))
        self.assertNotIn("WhatsApp:", v.receitas[1]["observacoes_rec"])

    def test_envia_para_o_numero_que_o_whatsapp_conhece(self):
        # conta antiga sem o nono dígito: o /chat/check devolve 551188887777
        v = Vhsys([receita()], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(), jid={"5511988887777": "551188887777"})
        self.assertEqual({m[1] for m in msgs}, {"551188887777"})

    def test_cliente_sem_celular(self):
        v = Vhsys([receita()], {77: {**CLIENTE, "celular_cliente": ""}})
        r, msgs = rodar(v, Asaas())
        self.assertEqual((r["resumo"], msgs), ({"sem_whatsapp": 1}, []))

    def test_parcelas_vao_10_dias_antes_de_cada_vencimento(self):
        # cobrança em 3 parcelas emitida em 06/10: cada parcela é uma receita no ERP
        v = Vhsys([receita(1, venc="2026-10-16"), receita(2, venc="2026-11-16"),
                   receita(3, venc="2026-12-16")], {77: CLIENTE})
        r, msgs = rodar(v, Asaas())
        self.assertEqual([x["receita"] for x in r["resultados"]], [1])  # 10 dias: vai já
        r, _ = rodar(v, Asaas(), hoje=dt.date(2026, 11, 5))
        self.assertEqual(r["resultados"], [])  # 2ª parcela ainda a 11 dias
        r, _ = rodar(v, Asaas(), hoje=dt.date(2026, 11, 6))
        self.assertEqual([x["receita"] for x in r["resultados"]], [2])
        self.assertIn("boleto enviado em 06/11/2026", v.receitas[2]["observacoes_rec"])
        self.assertNotIn("WhatsApp", v.receitas[3]["observacoes_rec"])

    def test_antecedencia_configuravel(self):
        v = Vhsys([receita(1, venc="2026-10-08"), receita(2, venc="2026-10-16")], {77: CLIENTE})
        with mock.patch.dict(os.environ, {"BOLETO_DIAS_ANTES": "5"}):
            r, _ = rodar(v, Asaas(), aplicar=False)
        self.assertEqual([x["receita"] for x in r["resultados"]], [1])  # 2ª a 10 dias: espera

    def test_cobranca_unica_vai_na_hora_mesmo_vencendo_longe(self):
        v = Vhsys([receita(venc="2026-12-20")], {77: CLIENTE})
        r, msgs = rodar(v, Asaas())
        self.assertEqual(r["resumo"], {"enviado": 1})

    def test_primeira_parcela_vai_na_hora_e_as_demais_perto_do_vencimento(self):
        # OS parcelada em 3x criada hoje: mesma identificação, vencimentos longe
        parcelas = [receita(i, venc=v, ident="OS_158") for i, v in
                    ((11, "2026-10-30"), (12, "2026-11-30"), (13, "2026-12-30"))]
        v = Vhsys(parcelas, {77: CLIENTE})
        r, _ = rodar(v, Asaas())
        self.assertEqual([x["receita"] for x in r["resultados"]], [11])
        r, _ = rodar(v, Asaas())
        self.assertEqual(r["resultados"], [])  # cron seguinte: nada de novo
        self.assertEqual(v.consultas_cliente, 2)  # uma consulta por cliente por rodada
        r, _ = rodar(v, Asaas(), hoje=dt.date(2026, 11, 20))
        self.assertEqual([x["receita"] for x in r["resultados"]], [12])

    def test_primeira_parcela_leva_o_resumo_da_cobranca(self):
        parcelas = [receita(11, venc="2026-10-30", ident="OS_158"),
                    receita(12, venc="2026-11-30", ident="OS_158"),
                    receita(13, venc="2026-12-30", ident="OS_158")]
        parcelas[2]["valor_rec"] = "300.00"
        v = Vhsys(parcelas, {77: CLIENTE})
        r, msgs = rodar(v, Asaas())
        texto = msgs[0][2]
        self.assertIn("Segue o boleto de Mensalidade outubro (parcela 1 de 3) no valor de "
                      "R$ 350,00", texto)
        self.assertIn("Resumo da cobrança: R$ 1.000,00 em 3 parcelas\n"
                      "• 1ª parcela: R$ 350,00, vencimento em 30/10/2026\n"
                      "• 2ª parcela: R$ 350,00, vencimento em 30/11/2026\n"
                      "• 3ª parcela: R$ 300,00, vencimento em 30/12/2026\n"
                      "Os próximos boletos serão enviados por aqui 10 dias antes de cada "
                      "vencimento.", texto)
        self.assertLess(texto.index("Resumo"), texto.index("Linha digitável"))
        r, msgs = rodar(v, Asaas(), hoje=dt.date(2026, 11, 20))
        self.assertIn("(parcela 2 de 3)", msgs[0][2])
        self.assertNotIn("Resumo", msgs[0][2])

    def test_cobranca_unica_sem_resumo_nem_parcela(self):
        v = Vhsys([receita()], {77: CLIENTE})
        r, msgs = rodar(v, Asaas())
        self.assertNotIn("Resumo", msgs[0][2])
        self.assertNotIn("parcela", msgs[0][2])

    def test_entrada_paga_na_hora_nao_antecipa_a_segunda(self):
        parcelas = [receita(21, venc="2026-10-20"), receita(22, venc="2026-11-20"),
                    receita(23, venc="2026-12-20")]
        v = Vhsys(parcelas, {77: CLIENTE})
        rodar(v, Asaas())  # 1ª vai
        v.receitas[21]["liquidado_rec"] = "Sim"  # cliente pagou a entrada no mesmo dia
        r, _ = rodar(v, Asaas())
        self.assertEqual(r["resultados"], [])

    def test_sem_identificacao_agrupa_pelo_minuto_do_cadastro(self):
        v = Vhsys([receita(31, venc="2026-11-20", cad="2026-10-06 10:15:02"),
                   receita(32, venc="2026-12-20", cad="2026-10-06 10:15:40"),
                   receita(33, venc="2026-12-05", cad="2026-10-06 14:00:00")], {77: CLIENTE})
        r, _ = rodar(v, Asaas(), aplicar=False)
        self.assertEqual(sorted(x["receita"] for x in r["resultados"]), [31, 33])

    def test_parcela_antiga_espera_a_janela(self):
        v = Vhsys([receita(41, venc="2026-11-30", cad="2026-09-20 09:00:00")], {77: CLIENTE})
        r, _ = rodar(v, Asaas(), aplicar=False)
        self.assertEqual(r["resultados"], [])

    def test_fora_do_escopo_nao_aparece(self):
        v = Vhsys([receita(1, obs="sem cobrança"), receita(2, banco="1318757"),
                   receita(3, liquidado="Sim"), receita(4, venc="2026-10-05")], {77: CLIENTE})
        r, msgs = rodar(v, Asaas())
        self.assertEqual((r["resultados"], msgs), ([], []))


class TestLembretes(unittest.TestCase):
    def test_dia_do_vencimento(self):
        v = Vhsys([receita(venc="2026-10-06")], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(), etapas=("vencimento", "atraso"))
        self.assertEqual(r["resultados"][0]["etapa"], "vencimento")
        self.assertIn("vence hoje (06/10/2026)", msgs[0][2])
        self.assertEqual([m[0] for m in msgs], ["texto", "pdf"])  # lembrete também leva o PDF
        self.assertIn("lembrete de vencimento enviado em 06/10/2026",
                      v.receitas[1]["observacoes_rec"])

    def test_boleto_mandado_hoje_dispensa_lembrete(self):
        obs = OBS + "\nWhatsApp: boleto enviado em 06/10/2026 (texto, pdf)."
        v = Vhsys([receita(venc="2026-10-06", obs=obs)], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(), etapas=("vencimento", "atraso"))
        self.assertEqual(msgs, [])

    def test_atraso_um_dia_depois(self):
        v = Vhsys([receita(venc="2026-10-05")], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(status="OVERDUE"), etapas=("vencimento", "atraso"))
        self.assertEqual(r["resultados"][0]["etapa"], "atraso")
        self.assertIn("que venceu em 05/10/2026", msgs[0][2])
        r, msgs = rodar(v, Asaas(status="OVERDUE"), etapas=("vencimento", "atraso"),
                        hoje=dt.date(2026, 10, 7))
        self.assertEqual(msgs, [])  # já avisado

    def test_vencimento_no_sabado_conta_da_segunda(self):
        v = Vhsys([receita(venc="2026-10-10")], {77: CLIENTE})
        for dia, esperado in ((11, []), (12, []), (13, ["atraso"])):
            r, _ = rodar(v, Asaas(status="OVERDUE"), etapas=("vencimento", "atraso"),
                         aplicar=False, hoje=dt.date(2026, 10, dia))
            self.assertEqual([x["etapa"] for x in r["resultados"]], esperado, dia)

    def test_atraso_antigo_nao_dispara(self):
        v = Vhsys([receita(venc="2026-09-01")], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(status="OVERDUE"), etapas=("vencimento", "atraso"))
        self.assertEqual(r["resultados"], [])

    def test_pago_no_vencimento_e_ja_baixado_no_asaas_nao_recebe_aviso(self):
        v = Vhsys([receita(venc="2026-10-05")], {77: CLIENTE})
        r, msgs = rodar(v, Asaas(status="RECEIVED"), etapas=("vencimento", "atraso"))
        self.assertEqual((r["resumo"], msgs), ({"nao_enviado": 1}, []))


class Falso(http.server.BaseHTTPRequestHandler):
    recebidos, user_agents, status = [], [], 200
    resposta = {"id": "r1", "messageid": "3EB0", "status": "sent"}

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        corpo = json.loads(self.rfile.read(n))
        Falso.recebidos.append((self.path, self.headers["token"], corpo))
        Falso.user_agents.append(self.headers["User-Agent"])
        self.send_response(Falso.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if Falso.status >= 400:
            resposta = {"error": "invalid"}
        elif self.path.endswith("/chat/check"):
            resposta = [{"query": q, "isInWhatsapp": q != "5511900000000",
                         "jid": "551188887777@s.whatsapp.net"} for q in corpo["numbers"]]
        else:
            resposta = Falso.resposta
        self.wfile.write(json.dumps(resposta).encode())

    def log_message(self, *a):
        pass


class TestTransporte(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), Falso)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Falso.recebidos, Falso.user_agents, Falso.status = [], [], 200
        Falso.resposta = {"id": "r1", "messageid": "3EB0", "status": "sent"}
        self.env = mock.patch.dict(os.environ, {
            "ZAPTOS_URL": f"{self.base}/whatsapp/", "ZAPTOS_TOKEN": "tok-zaptos",
            "UAZAPI_URL": "http://nao-usar", "UAZAPI_TOKEN": "antigo",
            "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_zaptos_tem_preferencia_e_documento_vai_por_media(self):
        whatsapp.enviar_texto("5511988887777", "oi")
        whatsapp.enviar_documento("5511988887777", "https://x/b.pdf", "boleto-1.pdf")
        (c1, t1, b1), (c2, _, b2) = Falso.recebidos
        self.assertEqual((c1, t1, b1), ("/whatsapp/send/text", "tok-zaptos",
                                        {"number": "5511988887777", "text": "oi"}))
        self.assertEqual((c2, b2["type"], b2["file"], b2["docName"]),
                         ("/whatsapp/send/media", "document", "https://x/b.pdf", "boleto-1.pdf"))

    def test_user_agent_proprio_para_passar_pelo_cloudflare(self):
        # caso real (06/10/2026): Python-urllib/3.x levava HTTP 403 "error code: 1010"
        whatsapp.enviar_texto("5511988887777", "oi")
        self.assertEqual(Falso.user_agents, [whatsapp.USER_AGENT])
        self.assertNotIn("Python-urllib", whatsapp.USER_AGENT)

    def test_nomes_antigos_ainda_valem(self):
        os.environ.pop("ZAPTOS_URL")
        os.environ.pop("ZAPTOS_TOKEN")
        os.environ["UAZAPI_URL"] = f"{self.base}/uaz"
        whatsapp.enviar_texto("5511988887777", "oi")
        self.assertEqual(Falso.recebidos[0][:2], ("/uaz/send/text", "antigo"))

    def test_verifica_o_numero_e_usa_o_jid_do_whatsapp(self):
        self.assertEqual(whatsapp.destino_verificado("5511988887777"), "551188887777")
        self.assertIsNone(whatsapp.destino_verificado("5511900000000"))
        self.assertEqual(Falso.recebidos[0][:1] + (Falso.recebidos[0][2],),
                         ("/whatsapp/chat/check", {"numbers": ["5511988887777"]}))
        self.assertEqual(whatsapp.destino_verificado("123@g.us"), "123@g.us")
        self.assertEqual(len(Falso.recebidos), 2)  # grupo não passa pelo check

    def test_resposta_200_com_falha_vira_erro(self):
        Falso.resposta = {"status": "failed"}
        with self.assertRaises(whatsapp.ErroWhatsapp):
            whatsapp.enviar_texto("5511988887777", "oi")
        Falso.resposta = {"error": "number not on whatsapp"}
        with self.assertRaises(whatsapp.ErroWhatsapp):
            whatsapp.enviar_texto("5511988887777", "oi")

    def test_resumo_do_envio(self):
        r = whatsapp.enviar_texto("5511988887777", "oi")
        self.assertEqual(whatsapp.resumo_envio(r), {"status": "sent", "mensagem": "3EB0"})

    def test_erro_guarda_o_status(self):
        Falso.status = 400
        with self.assertRaises(whatsapp.ErroWhatsapp) as ctx:
            whatsapp.enviar_texto("5511988887777", "oi")
        self.assertEqual(ctx.exception.status, 400)


class TestRota(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {"CRON_SECRET": "cron", "WHATSAPP_COBRANCA_MODO": "",
                                               "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        cls.env.start()
        import cobrar as rota  # api/cobrar.py (o handler)
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), rota.handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.env.stop()

    def get(self, query):
        abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with abridor.open(f"http://127.0.0.1:{self.srv.server_port}/api/cobrar{query}",
                              timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_sem_chave(self):
        self.assertEqual(self.get("")[0], 401)

    def test_aplicar_em_simulacao_e_recusado(self):
        status, corpo = self.get("?chave=cron&aplicar=1")
        self.assertEqual(status, 409)
        self.assertIn("WHATSAPP_COBRANCA_MODO=ativo", corpo["erro"])

    def test_parametros_invalidos(self):
        self.assertEqual(self.get("?chave=cron&data=06/10/2026")[0], 400)
        self.assertEqual(self.get("?chave=cron&receita=abc")[0], 400)
        self.assertEqual(self.get("?chave=cron&receita=1&etapa=outra")[0], 400)


if __name__ == "__main__":
    unittest.main()
