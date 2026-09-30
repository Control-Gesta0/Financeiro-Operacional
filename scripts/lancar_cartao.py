#!/usr/bin/env python3
"""Lança um comprovante de cartão de crédito como conta a pagar no VHSYS.

O cartão define a conta bancária e o vencimento (fatura) da despesa. Sem
--confirmar o script só mostra o que seria lançado; nada é gravado.

Uso:
    python3 scripts/lancar_cartao.py --cartao 6173 --compra 2026-09-30 --valor 651.58 \\
        --categoria-id 9653113 --fornecedor-id 43294242 \\
        --nome "KOMMO CRM - Licenças KOMMO" --pedido 5019195 \\
        --obs "Compra em 30/09/2026 | Cliente - Beauty Pro | ..."          # prévia
    ... --confirmar                                                      # grava

    python3 scripts/lancar_cartao.py --buscar kommo   # acha IDs de categoria/fornecedor

Documentação: https://developers.vhsys.com.br/api/cadastrar-despesa-16258175e0
"""
import argparse
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from decimal import Decimal

from resumo_financeiro import BASE_URL, PAGE_SIZE, brl, headers

# Cartões C6: conta bancária no VHSYS, dia de corte (fechamento) e dia de vencimento.
# Compra antes do dia de corte cai na fatura do mês; no dia de corte ou depois, na do mês seguinte.
CARTOES = {
    "6173": {"id_banco": 1321191, "nome": "Cartão C6 - 6173/4181 (15)", "corte": 9, "vencimento": 15},
    "4181": {"id_banco": 1321191, "nome": "Cartão C6 - 6173/4181 (15)", "corte": 9, "vencimento": 15},
    "8473": {"id_banco": 1321190, "nome": "Cartão C6 - 8473 (10)", "corte": 4, "vencimento": 10},
}
FORMA_PAGAMENTO = "Cartão de Crédito"  # mesmo texto dos lançamentos de cartão já existentes
RECENTES = 1000  # quantas despesas mais recentes olhar na checagem de duplicidade


def api(metodo, endpoint, params=None, corpo=None):
    url = f"{BASE_URL}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    h = headers()
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=dados, headers=h, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Erro HTTP {e.code} em {metodo} {endpoint}: {e.read().decode()[:500]}")
    if body.get("status") != "success":
        sys.exit(f"Erro da API ({endpoint}): {json.dumps(body, ensure_ascii=False)[:500]}")
    return body


def despesas_recentes(limite=RECENTES):
    contas, offset = [], 0
    while offset < limite:
        pagina = api("GET", "contas-pagar", {"limit": PAGE_SIZE, "offset": offset,
                                             "order": "id_conta_pag", "sort": "Desc"}).get("data") or []
        contas.extend(c for c in pagina if c.get("lixeira", "Nao") == "Nao")
        offset += len(pagina)
        if len(pagina) < PAGE_SIZE:
            break
    return contas


def vencimento_fatura(compra, cartao):
    ano, mes = compra.year, compra.month
    if compra.day >= cartao["corte"]:
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return dt.date(ano, mes, cartao["vencimento"])


def buscar(texto):
    """Lista categorias e fornecedores já usados em despesas que contenham o texto."""
    texto = texto.lower()
    categorias, fornecedores = {}, {}
    for c in despesas_recentes(limite=10**6):
        alvo = " ".join(str(c.get(k) or "") for k in ("categoria_pag", "nome_fornecedor", "nome_conta")).lower()
        if texto in alvo:
            categorias[c["id_categoria"]] = c["categoria_pag"]
            fornecedores[c["id_fornecedor"]] = c["nome_fornecedor"]
    print("Categorias:")
    for i, n in sorted(categorias.items(), key=lambda x: x[1] or ""):
        print(f"  {i}  {n}")
    print("Fornecedores:")
    for i, n in sorted(fornecedores.items(), key=lambda x: x[1] or ""):
        print(f"  {i}  {n}")


def duplicadas(corpo, pedido):
    achadas = []
    for c in despesas_recentes():
        mesmo_pedido = pedido and pedido in (c.get("observacoes_pag") or "")
        mesmo_valor = (str(c.get("id_fornecedor")) == str(corpo["id_fornecedor"])
                       and Decimal(c.get("valor_pag") or "0") == Decimal(corpo["valor_pag"])
                       and c.get("data_emissao") == corpo["data_emissao"])
        if mesmo_pedido or mesmo_valor:
            achadas.append(c)
    return achadas


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--buscar", help="texto para achar IDs de categoria/fornecedor no histórico")
    p.add_argument("--cartao", choices=sorted(CARTOES), help="final do cartão usado")
    p.add_argument("--compra", type=dt.date.fromisoformat, help="data da compra (AAAA-MM-DD)")
    p.add_argument("--valor", type=Decimal, help="valor cobrado no cartão, ex.: 651.58")
    p.add_argument("--categoria-id", type=int)
    p.add_argument("--fornecedor-id", type=int)
    p.add_argument("--nome", help="nome da conta (até 45 caracteres)")
    p.add_argument("--obs", default="", help="observações")
    p.add_argument("--pedido", default="", help="nº do pedido/documento, usado na checagem de duplicidade")
    p.add_argument("--vencimento", type=dt.date.fromisoformat, help="força o vencimento em vez de calcular")
    p.add_argument("--confirmar", action="store_true", help="grava no VHSYS (sem isso é só prévia)")
    p.add_argument("--ignorar-duplicidade", action="store_true")
    a = p.parse_args()

    if a.buscar:
        return buscar(a.buscar)
    faltando = [n for n in ("cartao", "compra", "valor", "categoria_id", "fornecedor_id", "nome")
                if getattr(a, n) is None]
    if faltando:
        p.error("faltam: " + ", ".join("--" + n.replace("_", "-") for n in faltando))
    if len(a.nome) > 45:
        p.error(f"--nome tem {len(a.nome)} caracteres; o VHSYS aceita até 45")

    cartao = CARTOES[a.cartao]
    venc = a.vencimento or vencimento_fatura(a.compra, cartao)
    corpo = {
        "nome_conta": a.nome,
        "id_banco": cartao["id_banco"],
        "id_categoria": a.categoria_id,
        "id_fornecedor": a.fornecedor_id,
        "valor_pag": f"{a.valor:.2f}",
        "data_emissao": a.compra.isoformat(),
        "vencimento_pag": venc.isoformat(),
        "n_documento_pag": a.pedido,
        "observacoes_pag": a.obs,
        "forma_pagamento": FORMA_PAGAMENTO,
        "liquidado_pag": "Nao",
    }

    print(f"Cartão:      {cartao['nome']} (corte dia {cartao['corte']}, vence dia {cartao['vencimento']})")
    print(f"Compra:      {a.compra:%d/%m/%Y}  ->  fatura com vencimento {venc:%d/%m/%Y}")
    print(f"Valor:       {brl(a.valor)}")
    print(json.dumps(corpo, ensure_ascii=False, indent=2))

    dups = duplicadas(corpo, a.pedido)
    if dups:
        print("\nATENÇÃO: possíveis lançamentos duplicados:")
        for c in dups:
            print(f"  id {c['id_conta_pag']}  venc {c['vencimento_pag']}  {brl(Decimal(c['valor_pag']))}  "
                  f"{c['nome_fornecedor']}  | {c.get('observacoes_pag') or ''}")
        if a.confirmar and not a.ignorar_duplicidade:
            sys.exit("Nada gravado. Confira os itens acima ou use --ignorar-duplicidade.")

    if not a.confirmar:
        print("\nPrévia apenas. Rode de novo com --confirmar para gravar.")
        return
    criada = api("POST", "contas-pagar", corpo=corpo)["data"]
    print(f"\nLançado: id_conta_pag {criada.get('id_conta_pag')} — {criada.get('situacao')}")


if __name__ == "__main__":
    main()
