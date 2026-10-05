"""Cliente mínimo da API v3 do Asaas (só leitura)."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("ASAAS_BASE_URL", "https://api.asaas.com/v3")


class ErroAsaas(Exception):
    pass


def consultar_cliente(id_cliente):
    """Devolve o cliente (name, cpfCnpj...) ou None se não houver chave/cliente."""
    if not id_cliente or not os.environ.get("ASAAS_API_KEY"):
        return None
    req = urllib.request.Request(
        f"{BASE_URL}/customers/{urllib.parse.quote(id_cliente)}",
        headers={"access_token": os.environ["ASAAS_API_KEY"],
                 "User-Agent": "FinanceiroOperacional/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise ErroAsaas(f"cliente {id_cliente}: HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise ErroAsaas(f"cliente {id_cliente}: {e}") from e


def _get(caminho, params=None):
    if not os.environ.get("ASAAS_API_KEY"):
        raise ErroAsaas("ASAAS_API_KEY não configurada")
    url = f"{BASE_URL}{caminho}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(url, headers={"access_token": os.environ["ASAAS_API_KEY"],
                                               "User-Agent": "FinanceiroOperacional/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise ErroAsaas(f"GET {caminho}: HTTP {e.code} {e.read()[:200]!r}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise ErroAsaas(f"GET {caminho}: {e}") from e


def listar_extrato(data):
    """Movimentações da conta no dia (YYYY-MM-DD), da mais antiga para a mais nova."""
    itens, offset = [], 0
    while True:
        corpo = _get("/financialTransactions", {"startDate": data, "finishDate": data,
                                                "order": "asc", "offset": offset, "limit": 100})
        pagina = (corpo or {}).get("data") or []
        itens.extend(pagina)
        if not (corpo or {}).get("hasMore") or not pagina:
            return itens
        offset += len(pagina)


def consultar_cobranca(id_cobranca):
    return _get(f"/payments/{urllib.parse.quote(id_cobranca)}")
