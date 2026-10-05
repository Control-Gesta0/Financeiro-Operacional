"""Cobrança pelo WhatsApp (Zaptos) das receitas emitidas no Asaas pela integração.

Regras combinadas com o financeiro (05/10/2026):
- etapa "boleto": logo depois da emissão, no mesmo cron da emissão, só em horário
  comercial (ENVIO_DAS..ENVIO_ATE, horário de Brasília);
- etapa "vencimento": na manhã do dia do vencimento, se ainda estiver em aberto;
- etapa "atraso": LEMBRETE_DIAS_ATRASO dias (padrão 1) depois do vencimento, contados
  a partir do primeiro dia útil quando o vencimento cai no fim de semana;
- vai para o celular do cadastro do cliente no ERP Lite;
- formato: mensagens comuns (o WhatsApp é conectado por QR Code, sem botões):
  1) texto com valor, vencimento, linha digitável e link do boleto; 2) o PDF do boleto
  como documento (só na etapa "boleto"); 3) o Pix copia e cola sozinho, para o cliente
  copiar com um toque. O Pix é sempre o da própria cobrança, nunca a chave Pix fixa:
  senão o pagamento não fica ligado à cobrança e a baixa automática não acha a receita.

Antes de cada envio a cobrança é consultada no Asaas: só segue se estiver PENDING ou
OVERDUE. Cada envio deixa uma marca nas observações da receita, então rodar de novo
nunca manda a mesma etapa duas vezes. Só grava/envia com WHATSAPP_COBRANCA_MODO=ativo.
"""
import datetime as dt
import os
import re
from collections import Counter
from decimal import Decimal

import emissao
import whatsapp

BRT = dt.timezone(dt.timedelta(hours=-3))
ENVIO_DAS, ENVIO_ATE = 8, 20  # janela do envio automático do boleto (horas, Brasília)
ETAPAS = ("boleto", "vencimento", "atraso")
MARCAS = {"boleto": "WhatsApp: boleto enviado",
          "vencimento": "WhatsApp: lembrete de vencimento enviado",
          "atraso": "WhatsApp: aviso de atraso enviado"}
STATUS_A_COBRAR = ("PENDING", "OVERDUE")
COBRANCA_DA_RECEITA = re.compile(re.escape(emissao.MARCA_EMISSAO) + r" (pay_[A-Za-z0-9]+)")


def modo():
    return "ativo" if os.environ.get("WHATSAPP_COBRANCA_MODO", "").strip().lower() == "ativo" \
        else "simulacao"


def agora():
    return dt.datetime.now(BRT)


def em_horario_comercial(momento=None):
    return ENVIO_DAS <= (momento or agora()).hour < ENVIO_ATE


def dias_atraso():
    try:
        return max(1, int(os.environ.get("LEMBRETE_DIAS_ATRASO") or 1))
    except ValueError:
        return 1


def _data_br(iso):
    try:
        return dt.date.fromisoformat(emissao._data_iso(iso)).strftime("%d/%m/%Y")
    except ValueError:
        return str(iso or "")


def _reais(valor):
    texto = f"{Decimal(str(valor or 0)):,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def primeiro_dia_util(data):
    while data.weekday() >= 5:  # sábado/domingo: o boleto pode ser pago na segunda
        data += dt.timedelta(days=1)
    return data


def telefone(cliente):
    """Celular do cadastro com DDI 55, ou None se não parecer um número válido."""
    numero = re.sub(r"\D", "", str((cliente or {}).get("celular_cliente") or "")).lstrip("0")
    if len(numero) in (10, 11):
        numero = "55" + numero
    return numero if numero.startswith("55") and len(numero) in (12, 13) else None


def nome_cliente(cliente, receita):
    nome = ((cliente or {}).get("fantasia_cliente") or (cliente or {}).get("razao_cliente")
            or receita.get("nome_cliente") or "").strip()
    return nome.title() if nome.isupper() else nome


def id_cobranca(receita):
    m = COBRANCA_DA_RECEITA.search(receita.get("observacoes_rec") or "")
    return m.group(1) if m else None


def marca_enviada(receita, etapa):
    """Data (dd/mm/aaaa) em que a etapa já foi enviada, ou None."""
    m = re.search(re.escape(MARCAS[etapa]) + r" em (\d{2}/\d{2}/\d{4})",
                  receita.get("observacoes_rec") or "")
    return m.group(1) if m else None


def etapa_do_dia(receita, hoje):
    """Etapa de lembrete que cabe hoje para a receita ("vencimento", "atraso") ou None."""
    try:
        vencimento = dt.date.fromisoformat(emissao._data_iso(receita.get("vencimento_rec")))
    except ValueError:
        return None
    if vencimento == hoje:
        hoje_br = hoje.strftime("%d/%m/%Y")
        if marca_enviada(receita, "vencimento") or marca_enviada(receita, "boleto") == hoje_br:
            return None  # boleto mandado hoje mesmo já serve de lembrete
        return "vencimento"
    inicio = primeiro_dia_util(vencimento) + dt.timedelta(days=dias_atraso())
    # janela de 3 dias: se o cron falhar um dia, o aviso sai no seguinte; mais que isso
    # não manda (evita disparar para atrasos antigos ao ligar o recurso)
    if inicio <= hoje <= inicio + dt.timedelta(days=2) and hoje.weekday() < 5 \
            and not marca_enviada(receita, "atraso"):
        return "atraso"
    return None


def textos(etapa, nome, receita):
    valor, venc = _reais(receita.get("valor_rec")), _data_br(receita.get("vencimento_rec"))
    descricao = (receita.get("nome_conta") or "").strip()
    saudacao = f"Olá, {nome}!" if nome else "Olá!"
    if etapa == "boleto":
        corpo = (f"{saudacao} Segue o boleto{f' de {descricao}' if descricao else ''} no valor "
                 f"de {valor}, com vencimento em {venc}. Dá para pagar pelo Pix ou pelo "
                 "código de barras.")
    elif etapa == "vencimento":
        corpo = (f"{saudacao} Passando para lembrar que o boleto de {valor} vence hoje "
                 f"({venc}). Se já pagou, pode desconsiderar.")
    else:
        corpo = (f"{saudacao} Ainda não identificamos o pagamento do boleto de {valor}, "
                 f"que venceu em {venc}. Os dados para pagamento seguem abaixo. Se já pagou, "
                 "pode desconsiderar.")
    return corpo


def dados_pagamento(cobranca, asaas):
    """Linha digitável e Pix copia e cola da cobrança (o que existir)."""
    dados = {}
    for chave, consulta, campo in (("linha", "linha_digitavel", "identificationField"),
                                   ("pix", "pix_qrcode", "payload")):
        try:
            dados[chave] = (getattr(asaas, consulta)(cobranca["id"]) or {}).get(campo)
        except getattr(asaas, "ErroAsaas", ValueError):
            dados[chave] = None  # sem chave Pix na conta, ou boleto ainda sem registro
    return dados


def texto_principal(texto, cobranca, pagamento):
    linhas = [texto]
    if pagamento.get("linha"):
        linhas += ["", "Linha digitável:", pagamento["linha"]]
    link = cobranca.get("invoiceUrl") or cobranca.get("bankSlipUrl")
    if link:
        linhas += ["", f"Boleto e fatura: {link}"]
    if pagamento.get("pix"):
        linhas += ["", "O Pix copia e cola vai na próxima mensagem."]
    return "\n".join(linhas)


def enviar(numero, etapa, texto, receita, cobranca, pagamento):
    """Manda o texto e, em seguida, o PDF do boleto e o Pix copia e cola.

    Se o texto falhar, nada foi enviado e a etapa fica para o próximo cron. Depois que
    ele saiu, uma falha no PDF ou no Pix só é anotada: repetir mandaria o texto de novo.
    """
    whatsapp.enviar_texto(numero, texto_principal(texto, cobranca, pagamento))
    enviados, falhas = ["texto"], {}
    extras = []
    if etapa == "boleto" and cobranca.get("bankSlipUrl"):
        fatura = cobranca.get("invoiceNumber") or receita.get("id_conta_rec")
        extras.append(("pdf", lambda: whatsapp.enviar_documento(
            numero, cobranca["bankSlipUrl"], f"boleto-{fatura}.pdf")))
    if pagamento.get("pix"):
        extras.append(("pix", lambda: whatsapp.enviar_texto(numero, pagamento["pix"])))
    for nome, envio in extras:
        try:
            envio()
            enviados.append(nome)
        except whatsapp.ErroWhatsapp as e:
            falhas[nome] = str(e)
    return enviados, falhas


def cobrar_receita(receita, etapa, vhsys, asaas, aplicar, hoje, reenviar=False):
    id_receita = receita.get("id_conta_rec")
    base = {"receita": id_receita, "cliente": receita.get("nome_cliente"), "etapa": etapa,
            "valor": receita.get("valor_rec"), "vencimento": receita.get("vencimento_rec")}
    pid = id_cobranca(receita)
    if not pid:
        return {**base, "resultado": "nao_enviado", "motivo": "receita sem cobrança Asaas"}
    base["cobranca"] = pid
    if marca_enviada(receita, etapa) and not reenviar:
        return {**base, "resultado": "ja_enviado", "em": marca_enviada(receita, etapa)}
    cobranca = asaas.consultar_cobranca(pid) or {}
    if cobranca.get("status") not in STATUS_A_COBRAR:
        return {**base, "resultado": "nao_enviado",
                "motivo": f"cobrança {cobranca.get('status') or 'não encontrada'} no Asaas"}
    cliente = vhsys.consultar_cliente(receita.get("id_cliente")) or {}
    numero = telefone(cliente)
    if not numero:
        return {**base, "resultado": "sem_whatsapp",
                "motivo": "cliente sem celular válido no cadastro do ERP Lite"}
    base["numero"] = numero[:-4] + "****"
    texto = textos(etapa, nome_cliente(cliente, receita), receita)
    if not aplicar:
        return {**base, "resultado": "seria_enviado", "texto": texto}
    pagamento = dados_pagamento(cobranca, asaas)
    enviados, falhas = enviar(numero, etapa, texto, receita, cobranca, pagamento)
    base.update(resultado="enviado", mensagens=enviados)
    if falhas:
        base["falhas"] = falhas
    # marca a etapa na receita (relida agora: a emissão pode ter mudado as observações)
    atual = (vhsys.consultar_receita(id_receita) or receita).get("observacoes_rec") or ""
    linha = f"{MARCAS[etapa]} em {hoje.strftime('%d/%m/%Y')} ({', '.join(enviados)})."
    vhsys.atualizar_receita(id_receita, {"observacoes_rec": f"{atual.strip()}\n{linha}".strip()})
    relida = vhsys.consultar_receita(id_receita) or {}
    base["marca_gravada"] = linha in (relida.get("observacoes_rec") or "")
    return base


def cobrar(vhsys, asaas, etapas, aplicar=False, desde=None, hoje=None, receitas=None):
    """Percorre as receitas em aberto emitidas pela integração e manda as etapas pedidas.

    etapas: ("boleto",) no cron da emissão; ("vencimento", "atraso") no cron diário.
    """
    desde = desde or os.environ.get("EMISSAO_A_PARTIR_DE", "")
    if not desde:
        raise ValueError("defina EMISSAO_A_PARTIR_DE (AAAA-MM-DD) na Vercel")
    hoje = hoje or agora().date()
    erros = (ValueError, whatsapp.ErroWhatsapp, getattr(asaas, "ErroAsaas", ValueError),
             getattr(vhsys, "ErroVhsys", ValueError))
    resultados = []
    lista = receitas if receitas is not None else vhsys.receitas_modificadas_desde(desde)
    for receita in lista:
        if receita.get("liquidado_rec") == "Sim" or not id_cobranca(receita):
            continue
        if str(receita.get("id_banco") or "") != emissao.conta_asaas():
            continue
        if "boleto" in etapas:
            venceu = emissao._data_iso(receita.get("vencimento_rec")) < hoje.isoformat()
            etapa = None if marca_enviada(receita, "boleto") or venceu else "boleto"
        else:
            etapa = etapa_do_dia(receita, hoje)
            etapa = etapa if etapa in etapas else None
        if not etapa:
            continue
        try:
            resultados.append(cobrar_receita(receita, etapa, vhsys, asaas, aplicar, hoje))
        except erros as e:  # um cliente com problema não trava os demais
            resultados.append({"receita": receita.get("id_conta_rec"),
                               "cliente": receita.get("nome_cliente"), "etapa": etapa,
                               "resultado": "erro", "motivo": str(e)})
    return {"dia": hoje.isoformat(), "etapas": list(etapas), "aplicado": aplicar,
            "resumo": dict(Counter(r["resultado"] for r in resultados)),
            "resultados": resultados}
