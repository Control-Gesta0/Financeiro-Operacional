"""Regra de baixa automática: evento de cobrança do Asaas -> receita no VHSYS.

Recebido (PAYMENT_RECEIVED) liquida a receita; estorno desfaz a liquidação.
A receita é localizada pelo externalReference da cobrança (ID da receita no
VHSYS) e, sem ele, por uma única receita em aberto com mesmo valor, vencimento
e cliente. Em qualquer dúvida nada é alterado e o caso volta como
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
    """Campos do PUT de liquidação. valor_rec mantém o valor original do título."""
    campos = {
        "valor_rec": _valor(receita.get("valor_rec")),
        "valor_pago": _valor(pagamento.get("value")),
        # paymentDate = crédito na conta Asaas, que é o que aparece no extrato dela.
        "data_pagamento": pagamento.get("paymentDate") or pagamento.get("clientPaymentDate"),
        "obs_pagamento": (f"Baixa automática Asaas {pagamento.get('id')} "
                          f"({pagamento.get('billingType')}). Pago R$ {_valor(pagamento.get('value'))}, "
                          f"líquido R$ {_valor(pagamento.get('netValue'))}."),
    }
    # Taxa do Asaas no próprio título: o VHSYS a apresenta na categoria de taxas de
    # cobrança (30.01.10), sem precisar de uma despesa separada.
    taxa = taxa_cobranca(pagamento)
    if taxa:
        campos["valor_taxa"] = taxa
    if os.environ.get("VHSYS_ID_BANCO_ASAAS"):
        campos["id_banco"] = os.environ["VHSYS_ID_BANCO_ASAAS"]
    return campos


def taxa_cobranca(pagamento):
    """Taxa cobrada pelo Asaas (valor bruto − líquido), ou None se não houver."""
    if pagamento.get("netValue") is None:
        return None
    taxa = Decimal(str(pagamento.get("value") or 0)) - Decimal(str(pagamento["netValue"]))
    return f"{taxa:.2f}" if taxa > 0 else None


def processar_evento(evento, vhsys, asaas):
    """Aplica um evento de webhook. Erros de comunicação sobem como exceção
    (o handler responde 500 e o Asaas reenvia); o resto vira um resultado."""
    tipo = evento.get("event")
    pagamento = evento.get("payment") or {}
    base = {"evento": tipo, "cobranca": pagamento.get("id"), "modo": modo()}

    if tipo in EVENTOS_ALERTA:
        return {**base, "resultado": "alerta", "motivo": "requer conferência manual"}
    if tipo not in EVENTOS_BAIXA | EVENTOS_ESTORNO:
        return {**base, "resultado": "ignorado"}

    if tipo in EVENTOS_ESTORNO and not str(pagamento.get("externalReference") or "").strip().isdigit():
        # Sem o ID da receita não dá para achar com segurança uma conta já liquidada.
        return {**base, "resultado": "nao_conciliado",
                "motivo": "estorno sem externalReference: desfazer a baixa manualmente"}
    receita, como = localizar_receita(pagamento, vhsys, asaas)
    if not receita:
        return {**base, "resultado": "nao_conciliado", "motivo": como}
    base.update(receita=receita.get("id_conta_rec"), localizada_por=como)
    liquidada = receita.get("liquidado_rec") == "Sim"

    if tipo in EVENTOS_BAIXA:
        if liquidada:
            return {**base, "resultado": "ja_liquidada"}
        campos = campos_baixa(pagamento, receita)
        if base["modo"] != "ativo":
            return {**base, "resultado": "liquidada", "campos": campos}
        vhsys.liquidar(receita["id_conta_rec"], campos)
        resultado = {**base, "resultado": "liquidada", "campos": campos}
        if "valor_taxa" in campos:
            # A doc do VHSYS não lista valor_taxa na liquidação: confere se gravou.
            gravada = vhsys.consultar_receita(receita["id_conta_rec"]) or {}
            resultado["taxa_gravada"] = _valor(gravada.get("valor_taxa")) == campos["valor_taxa"]
        return resultado

    if not liquidada:
        return {**base, "resultado": "ja_em_aberto"}
    if base["modo"] == "ativo":
        vhsys.desliquidar(receita["id_conta_rec"])
    return {**base, "resultado": "desliquidada"}
