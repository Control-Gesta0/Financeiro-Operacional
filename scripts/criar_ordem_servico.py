#!/usr/bin/env python3
"""Cria uma ordem de serviço no VHSYS e confere o contas a receber lançado por ela.

Uso:
    python3 scripts/criar_ordem_servico.py pedido.json             # simulação: só mostra o que seria enviado
    python3 scripts/criar_ordem_servico.py pedido.json --executar  # cria a OS com serviços e parcelas
    python3 scripts/criar_ordem_servico.py --conferir 74           # confere o financeiro da OS nº 74

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

O script NÃO cria conta a receber. O financeiro de OS e pedidos é lançado pelo
próprio documento (menu da OS > Lançar Contas), que vincula a receita à OS
(identificacao "OS_<id_ordem>" e contas_pedido = 1). A API pública não tem esse
endpoint; receita avulsa via POST /contas-receber fica sem vínculo e duplica a
cobrança quando alguém clica em Lançar Contas.

O total é dividido igualmente entre as parcelas e a diferença de centavos vai
para a última.

Documentação: https://developers.vhsys.com.br/api/cadastrar-ordem-de-servi%C3%A7o-16435140e0
              https://developers.vhsys.com.br/api/cadastrar-parcelas-16437580e0
"""
import datetime as dt
import json
import re
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


def criar(caminho_pedido, executar):
    with open(caminho_pedido, encoding="utf-8") as f:
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
    for s in servicos:
        print(f"  {s['horas_servico']:>5} x {brl(Decimal(s['valor_unit_servico'])):>14}  {s['desc_servico']}")
    print(f"Total:     {brl(total)}")
    for p in parcelas:
        print(f"  Parcela {p['data_parcela']}  {brl(Decimal(p['valor_parcela']))}  {p['forma_pagamento']}")
    print(f"Lançar Contas com conta {banco['nome_banco_cad']} (id {banco['id_banco_cad']})"
          f" e categoria {categoria['desc_categoria']} (id {categoria['id_categoria']})")

    repetidas = [o["id_pedido"] for o in listar("ordens-servico")
                 if o["id_cliente"] == cliente["id_cliente"] and o["data_pedido"] == data
                 and Decimal(o["valor_total_os"] or "0") == total]
    if repetidas:
        sys.exit(f"\nJá existe OS deste cliente com a mesma data e total: {repetidas}. Nada foi gravado.")
    if not executar:
        print("\nSimulação: nada foi gravado. Rode com --executar para criar a OS no VHSYS.")
        return

    os_criada = chamar("POST", "ordens-servico", os_corpo)
    id_ordem, numero = os_criada["id_ordem"], os_criada["id_pedido"]
    chamar("POST", f"ordens-servico/{id_ordem}/servicos", servicos)
    chamar("POST", f"ordens-servico/{id_ordem}/parcelas", parcelas)
    conferida = chamar("GET", f"ordens-servico/{id_ordem}")
    print(f"\nOS {numero} criada (id_ordem {id_ordem}) com total {brl(Decimal(conferida['valor_total_os']))}")
    print(f"Próximo passo no VHSYS: OS {numero} > menu > Lançar Contas, conta {banco['nome_banco_cad']},"
          f" categoria {categoria['desc_categoria']}.")
    print(f"Depois confira: python3 scripts/criar_ordem_servico.py --conferir {numero}")


def conferir(numero):
    """Confere se o financeiro da OS foi lançado por ela e se não há receita avulsa duplicada."""
    ordem = unico([o for o in listar("ordens-servico") if o["id_pedido"] == numero], f"OS {numero}")
    id_ordem, total = ordem["id_ordem"], Decimal(ordem["valor_total_os"] or "0")
    bancos = {b["id_banco_cad"]: b["nome_banco_cad"] for b in listar("contas-bancarias")}
    receitas = [c for c in listar("contas-receber", id_cliente=ordem["id_cliente"])
                if c["id_cliente"] == ordem["id_cliente"]]
    vinculadas = [c for c in receitas if c.get("identificacao") == f"OS_{id_ordem}"]
    menciona_os = re.compile(rf"ordem de servi[cç]o (nro\. )?{numero}\b", re.IGNORECASE)
    avulsas = [c for c in receitas if c not in vinculadas
               and menciona_os.search(f"{c.get('nome_conta') or ''} {c.get('observacoes_rec') or ''}")]

    print(f"OS {numero} (id_ordem {id_ordem}) — {ordem['nome_cliente']} — total {brl(total)}")
    print(f"Financeiro lançado pela OS: {'sim' if ordem['contas_pedido'] == 1 else 'NÃO'}")
    for c in vinculadas:
        print(f"  {c['id_conta_rec']}  {c['vencimento_rec']}  {brl(Decimal(c['valor_rec'])):>14}"
              f"  {bancos.get(c['id_banco'], c['id_banco'])}  {c['categoria_rec']}"
              f"  {c.get('forma_pagamento') or '-'}  liquidado={c['liquidado_rec']}")
    soma = sum((Decimal(c["valor_rec"]) for c in vinculadas), Decimal(0))
    problemas = []
    if ordem["contas_pedido"] != 1 or not vinculadas:
        problemas.append("financeiro ainda não foi lançado pela OS (menu > Lançar Contas)")
    elif soma != total:
        problemas.append(f"receitas da OS somam {brl(soma)}, diferente do total {brl(total)}")
    for c in avulsas:
        problemas.append(f"receita avulsa {c['id_conta_rec']} ({brl(Decimal(c['valor_rec']))},"
                         f" '{c['nome_conta']}', liquidado={c['liquidado_rec']}) cita a OS sem estar"
                         " vinculada: duplicidade, excluir após confirmar com o usuário")
    for p in problemas:
        print(f"ATENÇÃO: {p}")
    if problemas:
        sys.exit(1)
    print("OK: financeiro lançado pela OS, sem receita avulsa.")


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--conferir":
        return conferir(int(args[1]))
    executar = "--executar" in args
    args = [a for a in args if a != "--executar"]
    if len(args) != 1:
        sys.exit(__doc__)
    criar(args[0], executar)


if __name__ == "__main__":
    main()
