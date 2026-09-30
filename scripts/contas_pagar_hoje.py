#!/usr/bin/env python3
"""Resumo de contas a pagar em aberto no VHSYS (Control ERP Lite).

Uso:
    python3 scripts/contas_pagar_hoje.py            # referência = hoje
    python3 scripts/contas_pagar_hoje.py 2026-10-01 # referência = data informada

Variáveis de ambiente obrigatórias:
    VHSYS_ACCESS_TOKEN         -> header access-token
    VHSYS_SECRET_ACCESS_TOKEN  -> header secret-access-token

Documentação: https://developers.vhsys.com.br/api/listar-despesa-16258180e0
"""
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from decimal import Decimal

BASE_URL = "https://api.vhsys.com/v2"
PAGE_SIZE = 250  # limite máximo da API


def headers():
    try:
        return {
            "access-token": os.environ["VHSYS_ACCESS_TOKEN"],
            "secret-access-token": os.environ["VHSYS_SECRET_ACCESS_TOKEN"],
            "User-Agent": "FinanceiroOperacional/1.0",
            "Cache-Control": "no-cache",
        }
    except KeyError as e:
        sys.exit(f"Variável de ambiente ausente: {e.args[0]}")


def listar_contas_em_aberto():
    """Busca todas as despesas não liquidadas e fora da lixeira, paginando."""
    contas, offset = [], 0
    while True:
        params = {"liquidado": "Nao", "lixeira": "Nao", "limit": PAGE_SIZE, "offset": offset}
        url = f"{BASE_URL}/contas-pagar?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers()), timeout=60) as r:
            body = json.load(r)
        if body.get("status") != "success":
            sys.exit(f"Erro da API: {json.dumps(body, ensure_ascii=False)[:500]}")
        pagina = body.get("data") or []
        contas.extend(pagina)
        total = int(body.get("paging", {}).get("total", len(contas)))
        offset += len(pagina)
        if not pagina or offset >= total:
            return contas


def saldo(conta):
    """Valor ainda em aberto: valor da conta menos o que já foi pago (parciais)."""
    return Decimal(conta.get("valor_pag") or "0") - Decimal(conta.get("valor_pago") or "0")


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main():
    ref = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    grupos = defaultdict(list)
    for c in listar_contas_em_aberto():
        venc = dt.date.fromisoformat(c["vencimento_pag"])
        chave = "hoje" if venc == ref else "vencidas" if venc < ref else "a_vencer"
        grupos[chave].append(c)

    print(f"Contas a pagar em aberto — referência {ref:%d/%m/%Y}\n")
    for chave, titulo in (("hoje", "Vencendo hoje"), ("vencidas", "Vencidas (atrasadas)")):
        itens = sorted(grupos[chave], key=lambda c: c["vencimento_pag"])
        print(f"{titulo}: {len(itens)} conta(s) — {brl(sum(map(saldo, itens), Decimal(0)))}")
        for c in itens:
            nome = c.get("nome_fornecedor") or c.get("nome_conta") or "-"
            print(f"  {c['vencimento_pag']}  {brl(saldo(c)):>16}  {nome}  [{c.get('categoria_pag') or '-'}]")
        print()
    a_vencer = grupos["a_vencer"]
    print(f"A vencer (futuras): {len(a_vencer)} conta(s) — {brl(sum(map(saldo, a_vencer), Decimal(0)))}")


if __name__ == "__main__":
    main()
