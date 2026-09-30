#!/usr/bin/env python3
"""Cria uma ordem de serviço no VHSYS e lança a conta a receber correspondente.

Uso:
    python3 scripts/criar_ordem_servico.py pedido.json             # simulação: só mostra o que seria enviado
    python3 scripts/criar_ordem_servico.py pedido.json --executar  # grava no VHSYS

Formato do pedido.json:
    {
      "cliente": "BORZI DISTRIBUICAO E LOGISTICA LTDA",   # razão social exata ou id_cliente
      "data": "2026-09-30",                               # opcional, padrão = hoje
      "servicos": [
        {"id_servico": 85329994, "descricao": "KOMMO CRM AVANÇADO 6 MESES",
         "quantidade": 1, "valor_unitario": "782.94"}
      ],
      "parcelas": ["2026-09-30"],                         # vencimentos; opcional, padrão = à vista
      "forma_pagamento": "PIX",
      "conta_bancaria": "C6",                             # nome exato da conta no VHSYS
      "categoria": "10.01.03"                             # código da categoria financeira de receita
    }

Cada parcela vira uma conta a receber no padrão das OS geradas pelo VHSYS
("Ordem de serviço N", documento "N-parcela"). O total é dividido igualmente
entre as parcelas e a diferença de centavos vai para a última.

Documentação: https://developers.vhsys.com.br/api/cadastrar-ordem-de-servi%C3%A7o-16435140e0
              https://developers.vhsys.com.br/api/cadastrar-receita-15847044e0
"""
import datetime as dt
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_DOWN, Decimal

from resumo_financeiro import BASE_URL, PAGE_SIZE, brl, headers

CENTAVO = Decimal("0.01")


def chamar(metodo, caminho, corpo=None, **params):
    url = f"{BASE_URL}/{caminho}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo,
                                 headers={**headers(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        body = json.loads(e.read() or b"{}")
    if body.get("status") != "success":
        sys.exit(f"Erro da API ({metodo} {caminho}): {json.dumps(body, ensure_ascii=False)[:500]}")
    return body["data"]


def listar(endpoint, **params):
    """Busca todos os registros fora da lixeira, paginando."""
    itens, offset = [], 0
    while True:
        url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(
            {**params, "limit": PAGE_SIZE, "offset": offset})
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers()), timeout=60) as r:
            body = json.load(r)
        pagina = body.get("data") or []
        itens.extend(i for i in pagina if i.get("lixeira", "Nao") == "Nao")
        offset += len(pagina)
        if not pagina or offset >= int(body.get("paging", {}).get("total", 0)):
            return itens


def unico(itens, descricao):
    if len(itens) != 1:
        sys.exit(f"Esperava 1 {descricao}, encontrei {len(itens)}: {itens}")
    return itens[0]


def resolver_cliente(ref):
    if isinstance(ref, int):
        return chamar("GET", f"clientes/{ref}")
    candidatos = listar("clientes", razao_cliente=ref)
    return unico([c for c in candidatos if c["razao_cliente"].upper() == ref.upper()], f"cliente '{ref}'")


def dividir(total, n):
    base = (total / n).quantize(CENTAVO, rounding=ROUND_DOWN)
    return [base] * (n - 1) + [total - base * (n - 1)]


def main():
    args = sys.argv[1:]
    executar = "--executar" in args
    args = [a for a in args if a != "--executar"]
    if len(args) != 1:
        sys.exit(__doc__)
    with open(args[0], encoding="utf-8") as f:
        pedido = json.load(f)

    data = pedido.get("data") or dt.date.today().isoformat()
    cliente = resolver_cliente(pedido["cliente"])
    banco = unico([b for b in listar("contas-bancarias")
                   if b["nome_banco_cad"] == pedido["conta_bancaria"]
                   and b["status_banco"] == "Ativo"], f"conta bancária '{pedido['conta_bancaria']}'")
    categoria = unico([c for c in listar("categorias-financeiras")
                       if c["tipo_categoria"] == "Receita"
                       and c["desc_categoria"].startswith(pedido["categoria"] + " ")],
                      f"categoria '{pedido['categoria']}'")

    servicos = [{"id_servico": s["id_servico"], "desc_servico": s["descricao"],
                 "horas_servico": str(s["quantidade"]),
                 "valor_unit_servico": str(Decimal(s["valor_unitario"]).quantize(CENTAVO))}
                for s in pedido["servicos"]]
    total = sum((Decimal(s["valor_unit_servico"]) * Decimal(s["horas_servico"]) for s in servicos),
                Decimal(0)).quantize(CENTAVO)
    vencimentos = pedido.get("parcelas") or [data]
    parcelas = [{"data_parcela": venc, "valor_parcela": str(valor),
                 "forma_pagamento": pedido["forma_pagamento"]}
                for venc, valor in zip(vencimentos, dividir(total, len(vencimentos)))]
    os_corpo = {"id_cliente": cliente["id_cliente"], "nome_cliente": cliente["razao_cliente"],
                "data_pedido": data, "data_entrega": data, "status_pedido": "Em Aberto"}

    print(f"Cliente:   {cliente['razao_cliente']} (id {cliente['id_cliente']})")
    print(f"Conta:     {banco['nome_banco_cad']} (id {banco['id_banco_cad']})")
    print(f"Categoria: {categoria['desc_categoria']} (id {categoria['id_categoria']})")
    for s in servicos:
        print(f"  {s['horas_servico']:>5} x {brl(Decimal(s['valor_unit_servico'])):>14}  {s['desc_servico']}")
    print(f"Total:     {brl(total)}")
    for p in parcelas:
        print(f"  Parcela {p['data_parcela']}  {brl(Decimal(p['valor_parcela']))}  {p['forma_pagamento']}")
    if not executar:
        print("\nSimulação: nada foi gravado. Rode com --executar para criar no VHSYS.")
        return

    os_criada = chamar("POST", "ordens-servico", os_corpo)
    id_ordem, numero = os_criada["id_ordem"], os_criada["id_pedido"]
    print(f"\nOS {numero} criada (id_ordem {id_ordem})")
    chamar("POST", f"ordens-servico/{id_ordem}/servicos", servicos)
    chamar("POST", f"ordens-servico/{id_ordem}/parcelas", parcelas)

    for n, p in enumerate(parcelas, 1):
        receita = chamar("POST", "contas-receber", {
            "nome_conta": f"Ordem de serviço {numero}",
            "id_banco": banco["id_banco_cad"],
            "vencimento_rec": p["data_parcela"],
            "valor_rec": p["valor_parcela"],
            "data_emissao": data,
            "id_cliente": cliente["id_cliente"],
            "nome_cliente": cliente["razao_cliente"],
            "id_categoria": categoria["id_categoria"],
            "categoria_rec": categoria["desc_categoria"],
            "n_documento_rec": f"{numero}-{n}",
            "observacoes_rec": f"Ordem de serviço nro. {numero}",
            "liquidado_rec": "Nao",
            "forma_pagamento": pedido["forma_pagamento"],
            "tipo_conta": "Conta",
        })
        print(f"Conta a receber {receita['id_conta_rec']} criada: {brl(Decimal(p['valor_parcela']))}"
              f" vencendo {p['data_parcela']}")

    conferida = chamar("GET", f"ordens-servico/{id_ordem}")
    print(f"Conferência: OS {numero} com total {brl(Decimal(conferida['valor_total_os']))}")


if __name__ == "__main__":
    main()
