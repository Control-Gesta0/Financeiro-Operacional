"""Conciliação diária: extrato do Asaas × receitas do VHSYS. Só lê, não altera nada.

Para cada cobrança recebida no extrato do dia, procura a baixa no VHSYS:
  1. receita liquidada no dia com o ID da cobrança no obs_pagamento (baixa da integração);
  2. senão, a mesma busca da baixa automática (externalReference ou valor+vencimento+cliente).
Os demais movimentos (taxas, transferências, estornos) são somados à parte: ainda não
são lançados no VHSYS, então entram no relatório como "a lançar".
"""
import datetime as dt
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


def montar(data, vhsys, asaas):
    extrato = asaas.listar_extrato(data)
    liquidadas = vhsys.listar_liquidadas(data)
    grupos = defaultdict(list)
    for mov in extrato:
        grupos[grupo_do_movimento(mov.get("type") or "")].append(mov)

    recebimentos = []
    for mov in grupos["recebimento"]:
        recebimentos.append({**conferir_recebimento(mov, liquidadas, vhsys, asaas),
                             "pagamento": mov.get("paymentId"), "valor": mov.get("value"),
                             "descricao": mov.get("description")})

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
                   for g, movs in grupos.items()},
        "qtd": {g: len(movs) for g, movs in grupos.items()},
        "outros": [m.get("description") for m in grupos["outro"]],
        "saldo_final": extrato[-1].get("balance") if extrato else None,
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
    linhas.append(f"\nRecebimentos: {len(rec)} · {brl(r['totais'].get('recebimento', 0))}")
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

    rotulos = (("taxa", "Taxas"), ("transferencia", "Transferências e Pix enviados"),
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
