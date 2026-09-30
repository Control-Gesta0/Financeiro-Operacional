#!/usr/bin/env python3
"""Resumo de contas a pagar e a receber em aberto no VHSYS (Control ERP Lite).

Uso:
    python3 scripts/resumo_financeiro.py                    # pagar e receber, referência = hoje
    python3 scripts/resumo_financeiro.py pagar              # só contas a pagar
    python3 scripts/resumo_financeiro.py receber 2026-10-01 # só a receber, data informada

Autenticação (headers access-token e secret-access-token), lida de
VHSYS_ACCESS_TOKEN e VHSYS_SECRET_ACCESS_TOKEN. No ambiente cloud do Claude Code
elas vêm de "Credenciais de API", e o valor real só é aplicado pelo proxy.

Documentação: https://developers.vhsys.com.br/api/listar-despesa-16258180e0
              https://developers.vhsys.com.br/api/listar-receita-16174385e0
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

# Cada tipo de conta usa um endpoint e sufixos de campo próprios.
TIPOS = {
    "pagar": {"titulo": "CONTAS A PAGAR", "endpoint": "contas-pagar", "sufixo": "pag",
              "pessoa": "nome_fornecedor"},
    "receber": {"titulo": "CONTAS A RECEBER", "endpoint": "contas-receber", "sufixo": "rec",
                "pessoa": "nome_cliente"},
}


def headers():
    h = {"User-Agent": "FinanceiroOperacional/1.0", "Cache-Control": "no-cache"}
    for header, var in (("access-token", "VHSYS_ACCESS_TOKEN"),
                        ("secret-access-token", "VHSYS_SECRET_ACCESS_TOKEN")):
        if os.environ.get(var):
            h[header] = os.environ[var]
    return h


def listar_em_aberto(endpoint):
    """Busca todas as contas não liquidadas e fora da lixeira, paginando."""
    contas, offset = [], 0
    while True:
        params = {"liquidado": "Nao", "lixeira": "Nao", "limit": PAGE_SIZE, "offset": offset}
        url = f"{BASE_URL}/{endpoint}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers()), timeout=60) as r:
            body = json.load(r)
        if body.get("status") != "success":
            sys.exit(f"Erro da API ({endpoint}): {json.dumps(body, ensure_ascii=False)[:500]}")
        pagina = body.get("data") or []
        contas.extend(c for c in pagina if c.get("lixeira", "Nao") == "Nao")
        total = int(body.get("paging", {}).get("total", 0))
        offset += len(pagina)
        if not pagina or offset >= total:
            return contas


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def resumo(tipo, ref):
    cfg = TIPOS[tipo]
    suf = cfg["sufixo"]

    def saldo(c):
        """Valor ainda em aberto: valor da conta menos o que já foi pago (parciais)."""
        return Decimal(c.get(f"valor_{suf}") or "0") - Decimal(c.get("valor_pago") or "0")

    grupos = defaultdict(list)
    for c in listar_em_aberto(cfg["endpoint"]):
        venc = dt.date.fromisoformat(c[f"vencimento_{suf}"])
        chave = "hoje" if venc == ref else "vencidas" if venc < ref else "a_vencer"
        grupos[chave].append(c)

    print(f"=== {cfg['titulo']} em aberto — referência {ref:%d/%m/%Y} ===\n")
    for chave, titulo in (("hoje", "Vencendo hoje"), ("vencidas", "Vencidas (atrasadas)")):
        itens = sorted(grupos[chave], key=lambda c: c[f"vencimento_{suf}"])
        print(f"{titulo}: {len(itens)} conta(s) — {brl(sum(map(saldo, itens), Decimal(0)))}")
        for c in itens:
            nome = c.get(cfg["pessoa"]) or c.get("nome_conta") or "-"
            print(f"  {c[f'vencimento_{suf}']}  {brl(saldo(c)):>16}  {nome}  [{c.get(f'categoria_{suf}') or '-'}]")
        print()
    a_vencer = grupos["a_vencer"]
    print(f"A vencer (futuras): {len(a_vencer)} conta(s) — {brl(sum(map(saldo, a_vencer), Decimal(0)))}\n")


def main():
    args = sys.argv[1:]
    tipos = [args.pop(0)] if args and args[0] in TIPOS else list(TIPOS)
    ref = dt.date.fromisoformat(args[0]) if args else dt.date.today()
    for tipo in tipos:
        resumo(tipo, ref)


if __name__ == "__main__":
    main()
