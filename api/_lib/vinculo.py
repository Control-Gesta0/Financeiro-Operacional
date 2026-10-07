"""Vínculo das receitas migradas (conta Asaas, sem código do Asaas) com as cobranças que
já existem no Asaas, para não emitir boleto em dobro.

Para cada receita em aberto na conta Asaas sem "pay_..." nas observações, procura no
Asaas uma cobrança do mesmo cliente (CPF/CNPJ) com o mesmo valor e o mesmo vencimento
(atual ou original) que ainda não esteja ligada a outra receita. Classifica:
- vinculavel: uma única cobrança em aberto (PENDING/OVERDUE) bate;
- paga_no_asaas: a cobrança que bate já foi paga no Asaas, mas a receita está em aberto;
- ambigua: mais de uma cobrança bate;
- sem_cobranca: nenhuma cobrança bate (candidata a emitir, se o financeiro decidir);
- sem_documento / sem_cliente_no_asaas.
Com aplicar=True grava, só nas "vinculavel", a linha "Cobranca em aberto no Asaas
(pay_...)" (o formato do ERP anterior), e a receita passa a valer como boleto antigo
para a baixa, o atendimento e os lembretes.
"""
import datetime as dt
import re
from collections import Counter
from decimal import Decimal

import emissao

EM_ABERTO = ("PENDING", "OVERDUE")
PAGAS = ("RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH")


def _digitos(texto):
    return re.sub(r"\D", "", str(texto or ""))


def candidatas(abertas):
    return [r for r in abertas if str(r.get("id_banco") or "") == emissao.conta_asaas()
            and not emissao.TEM_COBRANCA.search(r.get("observacoes_rec") or "")]


def _item(receita, **extra):
    return {"receita": receita.get("id_conta_rec"), "cliente": receita.get("nome_cliente"),
            "valor": receita.get("valor_rec"), "vencimento": receita.get("vencimento_rec"), **extra}


def analisar(vhsys, asaas, aplicar=False, hoje=None):
    hoje = hoje or dt.date.today()
    abertas = vhsys.receitas_em_aberto()
    ja_ligadas = {m.group(0) for r in abertas
                  for m in emissao.TEM_COBRANCA.finditer(r.get("observacoes_rec") or "")}
    resultados = []
    por_cliente = {}
    for r in candidatas(abertas):
        por_cliente.setdefault(r.get("id_cliente"), []).append(r)
    for id_cliente, receitas in por_cliente.items():
        cliente = vhsys.consultar_cliente(id_cliente) or {}
        doc = _digitos(cliente.get("cnpj_cliente"))
        if not doc:
            resultados += [_item(r, situacao="sem_documento") for r in receitas]
            continue
        cus = asaas.buscar_cliente_por_documento(doc)
        if not cus:
            resultados += [_item(r, situacao="sem_cliente_no_asaas") for r in receitas]
            continue
        cobrancas = [c for c in asaas.cobrancas_do_cliente(cus["id"]) if c["id"] not in ja_ligadas]
        usadas = set()
        for r in sorted(receitas, key=lambda r: emissao._data_iso(r.get("vencimento_rec"))):
            valor = Decimal(str(r.get("valor_rec") or 0))
            datas = {emissao._data_iso(r.get("vencimento_rec")),
                     emissao._data_iso(r.get("vencimento_original"))} - {""}
            batem = [c for c in cobrancas if c["id"] not in usadas
                     and Decimal(str(c.get("value") or 0)) == valor and c.get("dueDate") in datas]
            if len(batem) > 1:
                resultados.append(_item(r, situacao="ambigua", cobrancas=[c["id"] for c in batem]))
                continue
            if not batem:
                resultados.append(_item(r, situacao="sem_cobranca"))
                continue
            cobranca = batem[0]
            usadas.add(cobranca["id"])
            situacao = ("vinculavel" if cobranca.get("status") in EM_ABERTO
                        else "paga_no_asaas" if cobranca.get("status") in PAGAS else "outra")
            item = _item(r, situacao=situacao, cobranca=cobranca["id"], status=cobranca.get("status"),
                         fatura=cobranca.get("invoiceNumber"))
            if aplicar and situacao == "vinculavel":
                obs = (r.get("observacoes_rec") or "").strip()
                linha = (f"Cobranca em aberto no Asaas ({cobranca['id']}). Vínculo pela integração "
                         f"em {hoje.strftime('%d/%m/%Y')} (fatura {cobranca.get('invoiceNumber')}).")
                vhsys.atualizar_receita(r["id_conta_rec"], {"observacoes_rec": f"{obs}\n{linha}".strip()})
                relida = vhsys.consultar_receita(r["id_conta_rec"]) or {}
                item["vinculo_gravado"] = cobranca["id"] in (relida.get("observacoes_rec") or "")
            resultados.append(item)
    resumo = Counter(x["situacao"] for x in resultados)
    valores = Counter()
    for x in resultados:
        valores[x["situacao"]] += Decimal(str(x["valor"] or 0))
    return {"aplicado": aplicar, "receitas": len(resultados), "resumo": dict(resumo),
            "valores": {k: str(v) for k, v in valores.items()},
            "resultados": sorted(resultados, key=lambda x: (x["situacao"], str(x["cliente"]),
                                                           str(x["vencimento"])))}
