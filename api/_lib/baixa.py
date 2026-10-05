"""Regra de baixa automática: evento de cobrança do Asaas -> receita no VHSYS.

Recebido (PAYMENT_RECEIVED) liquida a receita; estorno desfaz a liquidação.
A receita é localizada, nesta ordem: pelo externalReference da cobrança (ID da
receita no VHSYS); pelo ID da cobrança (pay_...) que o ERP Lite grava nas
observações da receita; e, por último, por uma única receita em aberto com
mesmo valor, vencimento e cliente. Em qualquer dúvida nada é alterado e o caso volta como
"nao_conciliado", para conferência manual.

Em modo "simulacao" (o padrão) nada é gravado no VHSYS: o resultado só diz o
que seria feito.
"""
import os
import re
import unicodedata
from decimal import Decimal

EVENTOS_BAIXA = {"PAYMENT_RECEIVED"}
EVENTOS_ESTORNO = {"PAYMENT_REFUNDED", "PAYMENT_RECEIVED_IN_CASH_UNDONE"}
# Precisam de decisão humana (estorno parcial, contestação): só ficam registrados.
EVENTOS_ALERTA = {"PAYMENT_PARTIALLY_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED",
                  "PAYMENT_CHARGEBACK_DISPUTE"}


MARCA_BAIXA = "Baixa automática Asaas"
MARCA_TAXA = "Taxas da cobrança Asaas"  # igual a vhsys_api.MARCA_TAXA
CATEGORIA_TAXAS = ("9653020", "30.01.10 - Taxas de Cobrança Boleto")


def modo():
    return "ativo" if os.environ.get("BAIXA_MODO", "").strip().lower() == "ativo" else "simulacao"


def _valor(v):
    return f"{Decimal(str(v or 0)):.2f}"


def _normalizar(nome):
    sem_acento = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().upper()


def localizar_receita(pagamento, vhsys, asaas):
    """Devolve (receita, como_achou) ou (None, motivo)."""
    ref = str(pagamento.get("externalReference") or "").strip()
    if ref.isdigit():
        receita = vhsys.consultar_receita(ref)
        if receita:
            return receita, "externalReference"
        return None, f"externalReference {ref} não existe no VHSYS"

    valor = _valor(pagamento.get("originalValue") or pagamento.get("value"))
    pid = pagamento.get("id")
    if pid:
        achadas = vhsys.buscar_por_cobranca(pid, valor) or vhsys.buscar_por_cobranca(pid)
        if len(achadas) == 1:
            return achadas[0], "id da cobrança nas observações"
        if achadas:
            return None, f"{len(achadas)} receitas com {pid} nas observações"

    vencimento = pagamento.get("originalDueDate") or pagamento.get("dueDate")
    candidatas = vhsys.buscar_abertas(valor, vencimento)
    cliente = asaas.consultar_cliente(pagamento.get("customer"))
    if cliente and cliente.get("name"):
        nome = _normalizar(cliente["name"])
        candidatas = [r for r in candidatas if _normalizar(r.get("nome_cliente")) == nome]
    if len(candidatas) == 1:
        return candidatas[0], "valor+vencimento+cliente" if cliente else "valor+vencimento"
    return None, (f"{len(candidatas)} receitas em aberto com valor {valor} e vencimento "
                  f"{vencimento}" + (f" para {cliente['name']}" if cliente else ""))


def campos_baixa(pagamento, receita):
    """Campos do PUT de liquidação. valor_rec mantém o valor original do título.
    A taxa não vai aqui: o VHSYS ignora valor_taxa na liquidação (visto em
    30/09/2026); ela vira uma despesa à parte (lancar_taxa)."""
    campos = {
        "valor_rec": _valor(receita.get("valor_rec")),
        "valor_pago": _valor(pagamento.get("value")),
        # paymentDate = crédito na conta Asaas, que é o que aparece no extrato dela.
        "data_pagamento": data_credito(pagamento),
        "obs_pagamento": (f"{MARCA_BAIXA} {pagamento.get('id')} "
                          f"({pagamento.get('billingType')}). Pago R$ {_valor(pagamento.get('value'))}, "
                          f"líquido R$ {_valor(pagamento.get('netValue'))}."),
    }
    if os.environ.get("VHSYS_ID_BANCO_ASAAS"):
        campos["id_banco"] = os.environ["VHSYS_ID_BANCO_ASAAS"]
    return campos


def data_credito(pagamento):
    return pagamento.get("paymentDate") or pagamento.get("clientPaymentDate")


def baixada_pela_integracao(receita, pid):
    return f"{MARCA_BAIXA} {pid} " in (receita.get("obs_pagamento") or "")


LIMITE_NOME_DESPESA = 45  # o VHSYS recusa nome_conta maior (HTTP 403, visto em 05/10/2026)


def nome_despesa_taxa(fatura, cliente):
    """Ex.: "Taxas Asaas 868268326 - J.N DOS REIS LOCACOE". A fatura vem primeiro para
    nunca ser cortada; o nome completo do cliente fica nas observações."""
    nome = f"Taxas Asaas {fatura}" + (f" - {cliente}" if cliente else "")
    return nome[:LIMITE_NOME_DESPESA].rstrip(" -")


def campos_despesa_taxa(pagamento, receita, taxa):
    """Despesa paga com as taxas do Asaas da cobrança, na categoria 30.01.10."""
    pid, data = pagamento.get("id"), data_credito(pagamento)
    fatura = pagamento.get("invoiceNumber") or pid
    cliente = receita.get("nome_cliente") or ""
    campos = {
        "nome_conta": nome_despesa_taxa(fatura, cliente),
        "id_banco": receita.get("id_banco") or os.environ.get("VHSYS_ID_BANCO_ASAAS"),
        "valor_pag": taxa,
        "valor_pago": taxa,
        "vencimento_pag": data,
        "data_emissao": data,
        "data_pagamento": data,
        "liquidado_pag": "Sim",
        "id_categoria": os.environ.get("VHSYS_ID_CATEGORIA_TAXAS", CATEGORIA_TAXAS[0]),
        "categoria_pag": os.environ.get("VHSYS_CATEGORIA_TAXAS", CATEGORIA_TAXAS[1]),
        "observacoes_pag": (f"{MARCA_TAXA} {pid} (receita {receita.get('id_conta_rec')}). "
                            f"Fatura {fatura}" + (f", {cliente}" if cliente else "")
                            + ". Lançada pela baixa automática."),
    }
    if os.environ.get("VHSYS_ID_FORNECEDOR_ASAAS"):
        campos["id_fornecedor"] = os.environ["VHSYS_ID_FORNECEDOR_ASAAS"]
    return campos


def lancar_taxa(pagamento, receita, vhsys, asaas, ativo):
    """Lança (ou só descreve, em simulação) a despesa com as taxas da cobrança.
    Não duplica: se já existe despesa de taxa da cobrança, garante que está paga."""
    taxa, origem = taxa_cobranca(pagamento, asaas)
    if not taxa:
        return None
    campos = campos_despesa_taxa(pagamento, receita, taxa)
    info = {"valor": taxa, "origem": origem}
    if not campos["id_banco"]:
        return {**info, "status": "sem_conta_bancaria"}
    if not ativo:
        return {**info, "status": "simulada", "campos": campos}
    pid, data = pagamento.get("id"), data_credito(pagamento)
    existentes = vhsys.buscar_despesa_taxa(pid, data)
    if existentes:
        despesa, status = existentes[0], "ja_lancada"
    else:
        despesa, status = vhsys.cadastrar_despesa(campos), "lancada"
    id_despesa = despesa.get("id_conta_pag")
    paga = despesa.get("liquidado_pag") == "Sim"
    if not paga and id_despesa:
        # Se o VHSYS não liquidar na criação, liquida em seguida e confere.
        if (vhsys.consultar_despesa(id_despesa) or {}).get("liquidado_pag") != "Sim":
            vhsys.liquidar_despesa(id_despesa, taxa, data)
        paga = (vhsys.consultar_despesa(id_despesa) or {}).get("liquidado_pag") == "Sim"
    return {**info, "status": status, "despesa": id_despesa, "paga": paga}


def taxa_cobranca(pagamento, asaas=None):
    """Taxas do Asaas da cobrança e de onde vieram: ("2.84", "extrato") ou
    ("1.85", "valor_liquido"), ou (None, None) se não houver taxa.

    O webhook só traz a taxa principal (bruto − líquido). A de mensageria é lançada à
    parte no extrato, por isso a soma vem do extrato do dia do crédito quando ele já
    tiver as linhas daquela cobrança. Todas vão juntas numa despesa (30.01.10).
    """
    pid, data = pagamento.get("id"), pagamento.get("paymentDate")
    if asaas is not None and pid and data and asaas.configurado():
        linhas = [m for m in asaas.listar_extrato(data)
                  if m.get("paymentId") == pid and "FEE" in (m.get("type") or "")]
        if linhas:
            total = sum((abs(Decimal(str(m.get("value") or 0))) for m in linhas), Decimal(0))
            return (f"{total:.2f}", "extrato") if total > 0 else (None, None)
    if pagamento.get("netValue") is None:
        return None, None
    taxa = Decimal(str(pagamento.get("value") or 0)) - Decimal(str(pagamento["netValue"]))
    return (f"{taxa:.2f}", "valor_liquido") if taxa > 0 else (None, None)


def processar_evento(evento, vhsys, asaas, modo_forcado=None):
    """Aplica um evento de webhook. Erros de comunicação sobem como exceção
    (o handler responde 500 e o Asaas reenvia); o resto vira um resultado.
    modo_forcado="simulacao" permite uma prévia mesmo com a baixa ligada."""
    tipo = evento.get("event")
    pagamento = evento.get("payment") or {}
    base = {"evento": tipo, "cobranca": pagamento.get("id"), "modo": modo_forcado or modo()}

    if tipo in EVENTOS_ALERTA:
        return {**base, "resultado": "alerta", "motivo": "requer conferência manual"}
    if tipo not in EVENTOS_BAIXA | EVENTOS_ESTORNO:
        return {**base, "resultado": "ignorado"}

    if tipo in EVENTOS_ESTORNO and not str(pagamento.get("externalReference") or "").strip().isdigit():
        # Sem o ID da receita não dá para achar com segurança uma conta já liquidada.
        return {**base, "resultado": "nao_conciliado",
                "motivo": "estorno sem externalReference: desfazer a baixa manualmente"}
    if tipo in EVENTOS_BAIXA and pagamento.get("anticipated"):
        # Antecipada: o título já foi baixado quando o dinheiro entrou na antecipação.
        return {**base, "resultado": "ignorado", "motivo": "cobrança antecipada"}
    receita, como = localizar_receita(pagamento, vhsys, asaas)
    if not receita:
        return {**base, "resultado": "nao_conciliado", "motivo": como}
    base.update(receita=receita.get("id_conta_rec"), localizada_por=como)
    liquidada = receita.get("liquidado_rec") == "Sim"

    if tipo in EVENTOS_BAIXA:
        ativo = base["modo"] == "ativo"
        if liquidada:
            # Baixada antes pela integração (ex.: reprocessamento): só completa a taxa.
            if baixada_pela_integracao(receita, pagamento.get("id")):
                return {**base, "resultado": "ja_liquidada",
                        "taxa": lancar_taxa(pagamento, receita, vhsys, asaas, ativo)}
            return {**base, "resultado": "ja_liquidada"}
        campos = campos_baixa(pagamento, receita)
        if ativo:
            vhsys.liquidar(receita["id_conta_rec"], campos)
        return {**base, "resultado": "liquidada", "campos": campos,
                "taxa": lancar_taxa(pagamento, receita, vhsys, asaas, ativo)}

    if not liquidada:
        return {**base, "resultado": "ja_em_aberto"}
    if base["modo"] == "ativo":
        vhsys.desliquidar(receita["id_conta_rec"])
    return {**base, "resultado": "desliquidada"}
