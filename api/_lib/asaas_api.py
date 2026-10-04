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
