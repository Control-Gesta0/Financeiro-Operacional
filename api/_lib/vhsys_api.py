"""Cliente mínimo da API v2 do VHSYS (contas a receber).

Documentação: https://developers.vhsys.com.br/api/listar-receita-16174385e0
              https://developers.vhsys.com.br/api/liquidar-receita-16174420e0
              https://developers.vhsys.com.br/api/desliquidar-receita-16174410e0
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

BASE_URL = os.environ.get("VHSYS_BASE_URL", "https://api.vhsys.com/v2")


class ErroVhsys(Exception):
    """Falha de comunicação ou resposta de erro do VHSYS (vale tentar de novo)."""


def _headers():
    h = {"User-Agent": "FinanceiroOperacional/1.0", "Cache-Control": "no-cache",
         "Content-Type": "application/json"}
    for header, var in (("access-token", "VHSYS_ACCESS_TOKEN"),
                        ("secret-access-token", "VHSYS_SECRET_ACCESS_TOKEN")):
        if os.environ.get(var):
            h[header] = os.environ[var]
    return h


def _chamar(metodo, caminho, params=None, body=None):
    url = f"{BASE_URL}{caminho}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    dados = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise ErroVhsys(f"{metodo} {caminho}: HTTP {e.code} {e.read()[:300]!r}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise ErroVhsys(f"{metodo} {caminho}: {e}") from e


def consultar_receita(id_receita):
    """Devolve a receita ou None se não existir."""
    corpo = _chamar("GET", f"/contas-receber/{urllib.parse.quote(str(id_receita))}")
    if not corpo or corpo.get("status") != "success":
        return None
    dados = corpo.get("data")
    return dados[0] if isinstance(dados, list) else dados or None


def buscar_abertas(valor, vencimento):
    """Receitas não liquidadas e fora da lixeira com exatamente esse valor e vencimento.

    Nos filtros do VHSYS um valor sozinho quer dizer "a partir de" (valor_receita=10.00
    traz tudo de R$ 10,00 para cima). Por isso o valor vai como faixa "X,X" e valor e
    vencimento são conferidos aqui de novo. O vencimento aceita o atual ou o original,
    para receitas prorrogadas.
    """
    alvo, receitas, offset = Decimal(str(valor)), [], 0
    while True:
        corpo = _chamar("GET", "/contas-receber", {
            "valor_receita": f"{alvo:.2f},{alvo:.2f}", "liquidado": "Nao", "lixeira": "Nao",
            "limit": 250, "offset": offset,
        })
        if not corpo or corpo.get("status") != "success":
            break
        pagina = corpo.get("data") or []
        receitas.extend(pagina)
        offset += len(pagina)
        if not pagina or offset >= int((corpo.get("paging") or {}).get("total", 0)):
            break
    return [r for r in receitas
            if r.get("liquidado_rec", "Nao") == "Nao"
            and Decimal(str(r.get("valor_rec") or 0)) == alvo
            and vencimento in (r.get("vencimento_rec"), r.get("vencimento_original"))]


def _todas(params, caminho="/contas-receber"):
    receitas, offset = [], 0
    while True:
        corpo = _chamar("GET", caminho, {**params, "limit": 250, "offset": offset})
        if not corpo or corpo.get("status") != "success":
            return receitas
        pagina = corpo.get("data") or []
        if isinstance(pagina, dict):
            pagina = [pagina]
        receitas.extend(pagina)
        offset += len(pagina)
        if not pagina or offset >= int((corpo.get("paging") or {}).get("total", 0)):
            return receitas


def buscar_por_cobranca(id_cobranca, valor=None):
    """Receitas com o ID da cobrança do Asaas nas observações (o ERP Lite grava
    "Cobranca em aberto no Asaas (pay_...)" ao emitir) ou na observação do pagamento
    (gravada pela baixa automática).

    Com valor, procura entre as receitas desse valor exato, abertas ou não. Sem valor,
    varre as em aberto (mais lento, usado quando o valor pago difere do título).
    """
    if valor is not None:
        alvo = Decimal(str(valor))
        params = {"valor_receita": f"{alvo:.2f},{alvo:.2f}", "lixeira": "Nao"}
    else:
        params = {"liquidado": "Nao", "lixeira": "Nao"}
    codigo = re.compile(re.escape(id_cobranca) + r"(?![A-Za-z0-9])")
    # observacoes_rec: gravado pelo ERP Lite ao emitir; obs_pagamento: gravado pela
    # nossa baixa (acha de novo uma receita que só foi localizada por valor+vencimento).
    return [r for r in _todas(params)
            if codigo.search((r.get("observacoes_rec") or "") + " " + (r.get("obs_pagamento") or ""))]


def liquidar(id_receita, campos):
    corpo = _chamar("PUT", f"/contas-receber/{id_receita}", body={"liquidado_rec": "Sim", **campos})
    if not corpo or corpo.get("status") != "success":
        raise ErroVhsys(f"liquidar {id_receita}: {json.dumps(corpo, ensure_ascii=False)[:300]}")
    return corpo


def desliquidar(id_receita):
    corpo = _chamar("PUT", f"/contas-receber/{id_receita}", body={"liquidado_rec": "Nao"})
    if not corpo or corpo.get("status") != "success":
        raise ErroVhsys(f"desliquidar {id_receita}: {json.dumps(corpo, ensure_ascii=False)[:300]}")
    return corpo


def listar_liquidadas(data):
    """Receitas liquidadas com data de pagamento no dia (YYYY-MM-DD)."""
    receitas, offset = [], 0
    while True:
        corpo = _chamar("GET", "/contas-receber", {
            "data_pagamento": data, "liquidado": "Sim", "lixeira": "Nao",
            "limit": 250, "offset": offset,
        })
        if not corpo or corpo.get("status") != "success":
            return receitas
        pagina = corpo.get("data") or []
        # data_pagamento também pode ser "a partir de": confere o dia exato aqui.
        receitas.extend(r for r in pagina
                        if r.get("liquidado_rec") == "Sim" and r.get("data_pagamento") == data)
        offset += len(pagina)
        if not pagina or offset >= int((corpo.get("paging") or {}).get("total", 0)):
            return receitas


# ---------------------------------------------------------------- despesas (taxas)
# As taxas do Asaas viram despesas pagas na categoria de taxas de cobrança. A marca
# abaixo nas observações identifica as lançadas pela integração (e a cobrança).
MARCA_TAXA = "Taxas da cobrança Asaas"


def listar_despesas_taxa(data):
    """Despesas de taxa lançadas pela integração com vencimento no dia (YYYY-MM-DD).
    data_vencimento também é "a partir de" no VHSYS: o dia exato é conferido aqui."""
    return [d for d in _todas({"data_vencimento": data, "lixeira": "Nao"}, "/contas-pagar")
            if d.get("vencimento_pag") == data and MARCA_TAXA in (d.get("observacoes_pag") or "")]


def buscar_despesa_taxa(id_cobranca, data):
    codigo = re.compile(re.escape(id_cobranca) + r"(?![A-Za-z0-9])")
    return [d for d in listar_despesas_taxa(data) if codigo.search(d.get("observacoes_pag") or "")]


def cadastrar_despesa(campos):
    corpo = _chamar("POST", "/contas-pagar", body=campos)
    if not corpo or corpo.get("status") != "success":
        raise ErroVhsys(f"cadastrar despesa: {json.dumps(corpo, ensure_ascii=False)[:300]}")
    dados = corpo.get("data")
    return (dados[0] if isinstance(dados, list) else dados) or {}


def consultar_despesa(id_despesa):
    corpo = _chamar("GET", f"/contas-pagar/{id_despesa}")
    if not corpo or corpo.get("status") != "success":
        return None
    dados = corpo.get("data")
    return dados[0] if isinstance(dados, list) else dados or None


def liquidar_despesa(id_despesa, valor, data):
    corpo = _chamar("PUT", f"/contas-pagar/{id_despesa}",
                    body={"liquidado_pag": "Sim", "valor_pago": valor, "data_pagamento": data})
    if not corpo or corpo.get("status") != "success":
        raise ErroVhsys(f"liquidar despesa {id_despesa}: "
                        f"{json.dumps(corpo, ensure_ascii=False)[:300]}")
    return corpo


# ------------------------------------------------------------------ emissão
def receitas_modificadas_desde(data):
    """Receitas em aberto criadas ou modificadas a partir da data (YYYY-MM-DD)."""
    return _todas({"liquidado": "Nao", "lixeira": "Nao", "data_modificacao": data})


def consultar_cliente(id_cliente):
    corpo = _chamar("GET", f"/clientes/{urllib.parse.quote(str(id_cliente))}")
    if not corpo or corpo.get("status") != "success":
        return None
    dados = corpo.get("data")
    return dados[0] if isinstance(dados, list) else dados or None


def atualizar_receita(id_receita, campos):
    corpo = _chamar("PUT", f"/contas-receber/{id_receita}", body=campos)
    if not corpo or corpo.get("status") != "success":
        raise ErroVhsys(f"atualizar receita {id_receita}: "
                        f"{json.dumps(corpo, ensure_ascii=False)[:300]}")
    return corpo
