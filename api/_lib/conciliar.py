"""Conciliação diária: extrato do Asaas × receitas do VHSYS. Só lê, não altera nada.

Para cada cobrança recebida no extrato do dia, procura a baixa no VHSYS:
  1. receita liquidada no dia com o ID da cobrança no obs_pagamento (baixa da integração);
  2. senão, a mesma busca da baixa automática (externalReference ou valor+vencimento+cliente).
Os demais movimentos (taxas, transferências, estornos) são somados à parte: ainda não
são lançados no VHSYS, então entram no relatório como "a lançar".
"""
import datetime as dt
import re
from collections import defaultdict
from decimal import Decimal

import baixa

BRT = dt.timezone(dt.timedelta(hours=-3))  # Brasil sem horário de verão desde 2019
MAX_ITENS = 15  # pendências listadas por grupo na mensagem


def ontem():
    return (dt.datetime.now(BRT).date() - dt.timedelta(days=1)).isoformat()


def brl(v):
    return "R$ " + f"{Decimal(str(v)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def grupo_do_movimento(tipo):
    if tipo == "PAYMENT_RECEIVED":
        return "recebimento"
    if "FEE" in tipo:
        return "taxa"
    if any(p in tipo for p in ("REVERSAL", "REFUND", "CHARGEBACK")):
        return "estorno"
    if "TRANSFER" in tipo or tipo.startswith("PIX_TRANSACTION_DEBIT"):
        return "transferencia"
    return "outro"


def conferir_recebimento(mov, liquidadas, vhsys, asaas):
    """Classifica um recebimento: baixado, manual, simulado, pendente."""
    pid = mov.get("paymentId") or ""
    marcada = next((r for r in liquidadas if pid and pid in (r.get("obs_pagamento") or "")), None)
    if marcada:
        return {"status": "baixado", "receita": marcada.get("id_conta_rec")}
    cobranca = asaas.consultar_cobranca(pid) if pid else None
    if not cobranca:
        return {"status": "pendente", "motivo": "cobrança não encontrada no Asaas"}
    receita, como = baixa.localizar_receita(cobranca, vhsys, asaas)
    if not receita:
        return {"status": "pendente", "motivo": como}
    if receita.get("liquidado_rec") == "Sim":
        return {"status": "manual", "receita": receita.get("id_conta_rec")}
    estado = "simulado" if baixa.modo() == "simulacao" else "pendente"
    return {"status": estado, "receita": receita.get("id_conta_rec"), "como": como,
            "motivo": None if estado == "simulado" else "receita ainda em aberto no VHSYS"}


def _resumo(movs):
    return {"qtd": len(movs),
            "total": str(sum((abs(Decimal(str(m.get("value") or 0))) for m in movs), Decimal(0)))}


def _fatura(mov):
    m = re.search(r"fatura nr\.?\s*(\d+)", mov.get("description") or "", re.I)
    return m.group(1) if m else None


def separar_antecipadas(extrato):
    """Tira do extrato os pares 'cobrança recebida' + 'baixa da antecipação' da mesma
    fatura e mesmo valor. A fatura já foi baixada quando foi antecipada e os dois
    movimentos se anulam no caixa do dia."""
    restantes, pares = list(extrato), []
    for rec in [m for m in extrato if m.get("type") == "PAYMENT_RECEIVED"]:
        valor = Decimal(str(rec.get("value") or 0))
        par = next((m for m in restantes if m is not rec
                    and "antecipa" in (m.get("description") or "").lower()
                    and Decimal(str(m.get("value") or 0)) == -valor
                    and ((rec.get("paymentId") and m.get("paymentId") == rec.get("paymentId"))
                         or (_fatura(rec) and _fatura(m) == _fatura(rec)))), None)
        if par:
            restantes.remove(rec)
            restantes.remove(par)
            pares.append({"pagamento": rec.get("paymentId"), "valor": rec.get("value"),
                          "descricao": rec.get("description")})
    return restantes, pares


def montar(data, vhsys, asaas):
    extrato_completo = asaas.listar_extrato(data)
    extrato, antecipadas = separar_antecipadas(extrato_completo)
    liquidadas = vhsys.listar_liquidadas(data)
    grupos = defaultdict(list)
    for mov in extrato:
        grupos[grupo_do_movimento(mov.get("type") or "")].append(mov)

    recebimentos = []
    for mov in grupos["recebimento"]:
        recebimentos.append({**conferir_recebimento(mov, liquidadas, vhsys, asaas),
                             "pagamento": mov.get("paymentId"), "valor": mov.get("value"),
                             "descricao": mov.get("description")})

    # Taxas de cobrança: gravadas no valor_taxa da receita (categoria 30.01.10 no VHSYS).
    # Uma cobrança pode ter mais de uma linha de taxa no extrato: compara a SOMA delas
    # com o valor_taxa gravado na receita.
    status_por_pid = {x["pagamento"]: x["status"] for x in recebimentos}
    taxas = {"lancada": [], "simulada": [], "divergente": [], "a_lancar": []}
    movs_taxa = grupos.pop("taxa", [])
    por_pid = defaultdict(list)
    for m in movs_taxa:
        por_pid[m.get("paymentId") or ""].append(m)
    for pid, movs in por_pid.items():
        if not pid:
            taxas["a_lancar"].extend(movs)
            continue
        receita = next((r for r in liquidadas if pid in (r.get("obs_pagamento") or "")), None)
        total = sum((abs(Decimal(str(m.get("value") or 0))) for m in movs), Decimal(0))
        if receita and Decimal(str(receita.get("valor_taxa") or 0)) == total:
            taxas["lancada"].extend(movs)
        elif receita:
            taxas["divergente"].append({"receita": receita.get("id_conta_rec"), "pagamento": pid,
                                        "valor": str(total),
                                        "na_receita": str(receita.get("valor_taxa") or "0")})
        elif status_por_pid.get(pid) == "simulado":
            taxas["simulada"].extend(movs)
        else:
            taxas["a_lancar"].extend(movs)
    pids_do_dia = {x["pagamento"] for x in recebimentos}
    vinculadas = [m for m in taxas["a_lancar"] if m.get("paymentId") in pids_do_dia]
    soltas = [m for m in taxas["a_lancar"] if m.get("paymentId") not in pids_do_dia]
    if vinculadas:
        grupos["taxa_pendente"] = vinculadas
    if soltas:
        grupos["taxa"] = soltas

    pagos_no_dia = {m.get("paymentId") for m in grupos["recebimento"]}
    sem_credito = [r for r in liquidadas
                   if "Baixa automática Asaas" in (r.get("obs_pagamento") or "")
                   and not any(p and p in r["obs_pagamento"] for p in pagos_no_dia)]

    return {
        "data": data,
        "modo": baixa.modo(),
        "recebimentos": recebimentos,
        "baixas_sem_credito": [{"receita": r.get("id_conta_rec"), "cliente": r.get("nome_cliente"),
                                "valor": r.get("valor_pago")} for r in sem_credito],
        "totais": {g: str(sum((Decimal(str(m.get("value") or 0)) for m in movs), Decimal(0)))
                   for g, movs in grupos.items() if movs},
        "qtd": {g: len(movs) for g, movs in grupos.items() if movs},
        "outros": [m.get("description") for m in grupos["outro"]],
        "taxas_lancadas": _resumo(taxas["lancada"]),
        "taxas_simuladas": _resumo(taxas["simulada"]),
        "taxas_divergentes": taxas["divergente"],
        "detalhe_taxas": [{"pagamento": m.get("paymentId"), "tipo": m.get("type"),
                           "valor": m.get("value"), "descricao": m.get("description")}
                          for m in movs_taxa],
        "antecipadas_compensadas": antecipadas,
        "saldo_final": extrato_completo[-1].get("balance") if extrato_completo else None,
    }


def texto(r):
    dia = dt.date.fromisoformat(r["data"]).strftime("%d/%m/%Y")
    linhas = [f"*Conciliação Asaas · {dia}*"]
    if r["saldo_final"] is None:
        linhas.append("\nSem movimento no Asaas neste dia.")
        return "\n".join(linhas)

    rec = r["recebimentos"]
    por_status = defaultdict(list)
    for x in rec:
        por_status[x["status"]].append(x)
    if rec:
        linhas.append(f"\nRecebimentos: {len(rec)} · {brl(r['totais'].get('recebimento', 0))}")
    else:
        linhas.append("\nNenhum recebimento a conciliar neste dia.")
    if por_status["baixado"]:
        linhas.append(f"✅ {len(por_status['baixado'])} baixados no VHSYS pela integração")
    if por_status["manual"]:
        linhas.append(f"✅ {len(por_status['manual'])} já estavam baixados no VHSYS")
    if por_status["simulado"]:
        linhas.append(f"🟡 {len(por_status['simulado'])} seriam baixados (modo simulação):")
        for x in por_status["simulado"][:MAX_ITENS]:
            linhas.append(f"• {brl(x['valor'])} → receita {x['receita']} ({x['como']})")
    if por_status["pendente"]:
        linhas.append(f"🔴 {len(por_status['pendente'])} sem baixa, conferir:")
        for x in por_status["pendente"][:MAX_ITENS]:
            linhas.append(f"• {brl(x['valor'])} · {x['pagamento']} · {x['motivo']}")
    for status in ("simulado", "pendente"):
        if len(por_status[status]) > MAX_ITENS:
            linhas.append(f"  e mais {len(por_status[status]) - MAX_ITENS}")

    if r["baixas_sem_credito"]:
        linhas.append(f"\n⚠️ {len(r['baixas_sem_credito'])} baixas da integração sem crédito "
                      "no extrato deste dia:")
        for x in r["baixas_sem_credito"][:MAX_ITENS]:
            linhas.append(f"• receita {x['receita']} · {x['cliente']} · {brl(x['valor'] or 0)}")

    if r["taxas_lancadas"]["qtd"]:
        linhas.append(f"\nTaxas de cobrança lançadas nas receitas (30.01.10): "
                      f"{r['taxas_lancadas']['qtd']} · {brl(r['taxas_lancadas']['total'])}")
    if r["taxas_simuladas"]["qtd"]:
        linhas.append(f"🟡 {r['taxas_simuladas']['qtd']} taxas · {brl(r['taxas_simuladas']['total'])} "
                      "seriam lançadas nas receitas (simulação)")
    if r["taxas_divergentes"]:
        linhas.append(f"🔴 {len(r['taxas_divergentes'])} receitas com taxa diferente do extrato:")
        for x in r["taxas_divergentes"][:MAX_ITENS]:
            linhas.append(f"• receita {x['receita']} · extrato {brl(x['valor'])} · "
                          f"na receita {brl(x['na_receita'])}")

    rotulos = (("taxa_pendente", "Taxas das cobranças sem baixa"),
               ("taxa", "Taxas sem cobrança vinculada"), ("transferencia", "Transferências e Pix enviados"),
               ("estorno", "Estornos e chargebacks"), ("outro", "Outros movimentos"))
    a_lancar = [(rot, g) for g, rot in rotulos if r["qtd"].get(g)]
    if a_lancar:
        linhas.append("\nAinda não lançados no VHSYS:")
        for rot, g in a_lancar:
            linhas.append(f"• {rot}: {r['qtd'][g]} · {brl(r['totais'][g])}")
        if r["outros"]:
            linhas.append("  (" + "; ".join(str(d) for d in r["outros"][:5]) + ")")

    linhas.append(f"\nSaldo Asaas no fim do dia: {brl(r['saldo_final'])}")
    return "\n".join(linhas)
