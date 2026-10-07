"""Reprocessa a baixa automática de um dia a partir do extrato do Asaas.

Serve para os recebimentos cujo webhook já passou (por exemplo, os que chegaram
enquanto a baixa estava em simulação). Cada "Cobrança recebida" do dia vira um
evento PAYMENT_RECEIVED com os dados atuais da cobrança e passa pela mesma regra
do webhook: mesma busca da receita, antecipadas ignoradas, sem baixa em dobro.
"""
from collections import Counter

import baixa
import conciliar


def reprocessar_cobranca(pid, vhsys, asaas, aplicar=False):
    """Baixa de uma cobrança só (ex.: boleto antigo pago no Asaas e ainda em aberto no ERP).

    Passa pela mesma regra do webhook; só vale para cobrança já recebida no Asaas."""
    cobranca = asaas.consultar_cobranca(pid)
    if not cobranca or cobranca.get("deleted"):
        resultado = {"cobranca": pid, "resultado": "nao_conciliado",
                     "motivo": "cobrança não encontrada no Asaas"}
    elif cobranca.get("status") != "RECEIVED":
        resultado = {"cobranca": pid, "resultado": "nao_conciliado",
                     "motivo": f"cobrança não está recebida no Asaas ({cobranca.get('status')})"}
    else:
        resultado = baixa.processar_evento({"event": "PAYMENT_RECEIVED", "payment": cobranca},
                                           vhsys, asaas,
                                           modo_forcado=None if aplicar else "simulacao")
    return {"cobranca": pid, "aplicado": aplicar,
            "resumo": {resultado["resultado"]: 1}, "resultados": [resultado]}


def reprocessar(data, vhsys, asaas, aplicar=False):
    extrato, antecipadas = conciliar.separar_antecipadas(asaas.listar_extrato(data))
    resultados = []
    for mov in extrato:
        pid = mov.get("paymentId")
        if mov.get("type") != "PAYMENT_RECEIVED" or not pid:
            continue
        cobranca = asaas.consultar_cobranca(pid)
        if not cobranca:
            resultados.append({"cobranca": pid, "resultado": "nao_conciliado",
                               "motivo": "cobrança não encontrada no Asaas"})
            continue
        resultados.append(baixa.processar_evento(
            {"event": "PAYMENT_RECEIVED", "payment": cobranca}, vhsys, asaas,
            modo_forcado=None if aplicar else "simulacao"))
    return {
        "data": data,
        "aplicado": aplicar,
        "resumo": dict(Counter(r["resultado"] for r in resultados)),
        "antecipadas_ignoradas": len(antecipadas),
        "resultados": resultados,
    }
