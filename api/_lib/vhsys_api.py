"""Cliente mínimo da API v2 do VHSYS (contas a receber).

Documentação: https://developers.vhsys.com.br/api/listar-receita-16174385e0
              https://developers.vhsys.com.br/api/liquidar-receita-16174420e0
              https://developers.vhsys.com.br/api/desliquidar-receita-16174410e0
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

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
    """Receitas não liquidadas e fora da lixeira com esse valor e vencimento."""
    corpo = _chamar("GET", "/contas-receber", {
        "valor_receita": valor, "data_vencimento": vencimento,
        "liquidado": "Nao", "lixeira": "Nao", "limit": 50,
    })
    if not corpo or corpo.get("status") != "success":
        return []
    return [r for r in corpo.get("data") or [] if r.get("liquidado_rec", "Nao") == "Nao"]


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
        receitas.extend(r for r in pagina if r.get("liquidado_rec") == "Sim")
        offset += len(pagina)
        if not pagina or offset >= int((corpo.get("paging") or {}).get("total", 0)):
            return receitas
