#!/usr/bin/env python3
"""Homologação C6 Developers: executa o roteiro de testes no sandbox e preenche o .docx.

Uso:
    python3 scripts/c6/homologacao.py testar                         # todas as APIs do config
    python3 scripts/c6/homologacao.py testar --apis boleto,pix       # só essas APIs
    python3 scripts/c6/homologacao.py testar --so B_04,B_05          # só esses testes
    python3 scripts/c6/homologacao.py status                         # resumo do último resultado
    python3 scripts/c6/homologacao.py preencher                      # gera o roteiro preenchido

O C6 exige mTLS: toda chamada (inclusive /auth) usa o certificado .crt/.key.
Credenciais vêm do ambiente ou de scripts/c6/.env (nunca versionado):
    C6_CLIENT_ID, C6_CLIENT_SECRET, C6_CERT_FILE, C6_KEY_FILE
    C6_BASE_URL (opcional; padrão = sandbox)

Dados não secretos (empresa, pagador de teste, chave Pix, webhook) ficam em
scripts/c6/config.json, criado a partir de config.exemplo.json.

Resultados vão para scripts/c6/evidencias/ (resultados.json + PDFs), que não é
versionado. Rodar de novo um teste sobrescreve só o resultado dele.
"""
import argparse
import datetime as dt
import html
import json
import os
import random
import re
import shutil
import ssl
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from casos import API_DO_PREFIXO, CASOS, CHECKBOXES, ORDEM_ROTEIRO  # noqa: E402

DIR = Path(__file__).resolve().parent
EVIDENCIAS = DIR / "evidencias"
RESULTADOS = EVIDENCIAS / "resultados.json"
ROTEIRO_PADRAO = DIR / "roteiro" / "Roteiro_de_Testes_-_C6_Developers_v3.0.docx"
SANDBOX = "https://baas-api-sandbox.c6bank.info"
AUTH_PATH = "/v1/auth/"
LIMITE_BODY = 4000  # caracteres do response body gravados no .docx


# --------------------------------------------------------------------------- config

def carregar_env():
    """Lê scripts/c6/.env (CHAVE=valor) sem sobrescrever o que já está no ambiente."""
    arq = DIR / ".env"
    if not arq.exists():
        return
    for linha in arq.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            k, v = linha.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def carregar_config():
    arq = DIR / "config.json"
    if not arq.exists():
        sys.exit("Crie scripts/c6/config.json a partir de config.exemplo.json e preencha.")
    return json.loads(arq.read_text(encoding="utf-8"))


def api_do_caso(caso_id):
    return API_DO_PREFIXO[caso_id.split("_")[0]]


def gerar_txid(n=32):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def gerar_referencia():
    """external_reference_id: 26 caracteres A-Z0-9 (exigência do BolePix v2)."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=26))


def variaveis_do_config(cfg):
    """Valores vindos do config.json; sempre recarregados, mesmo ao reexecutar testes."""
    v = {
        "chave_pix": cfg.get("chave_pix", ""),
        "chave_pix_tipo": cfg.get("chave_pix_tipo", "EVP"),
        "webhook_url": cfg.get("webhook_url", ""),
        "responsavel": cfg.get("empresa", {}).get("responsavel", ""),
        "billing_scheme": cfg.get("billing_scheme", "21"),
    }
    for chave, valor in cfg.get("pagador", {}).items():
        v[f"pagador.{chave}"] = valor
    return v


def variaveis_iniciais(cfg):
    """Variáveis fixas da rodada: config, datas e identificadores únicos."""
    hoje = dt.date.today()
    agora = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    v = {
        **variaveis_do_config(cfg),
        "e2eid": cfg.get("e2eid", ""),
        "hoje": hoje.isoformat(),
        "vencimento": (hoje + dt.timedelta(days=15)).isoformat(),
        "vencimento_alterado": (hoje + dt.timedelta(days=20)).isoformat(),
        "inicio_extrato": (hoje - dt.timedelta(days=29)).isoformat(),
        "inicio_rfc": (agora - dt.timedelta(days=29)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fim_rfc": agora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ref": agora.strftime("%m%d%H%M"),
        "ext_b1": gerar_referencia(),
        "ext_b2": gerar_referencia(),
        "ext_b3": gerar_referencia(),
        "ext_bolepix": gerar_referencia(),
        "txid_cob": gerar_txid(),
        "txid_cobv": gerar_txid(),
        "txid_lote_1": gerar_txid(),
        "txid_lote_2": gerar_txid(),
        "lote_id": str(int(time.time())),
        "devolucao_id": "D" + gerar_txid(20),
    }
    for i, linha in enumerate(cfg.get("linhas_pagamento") or [], 1):
        v[f"linha_{i}"] = linha
    return v


# ------------------------------------------------------------------------ templates

PADRAO_VAR = re.compile(r"\$\{([^}]+)\}")


def aplicar(obj, v):
    """Troca ${nome} pelos valores; string que é só um ${nome} mantém o tipo do valor."""
    if isinstance(obj, dict):
        return {k: aplicar(x, v) for k, x in obj.items()}
    if isinstance(obj, list):
        return [aplicar(x, v) for x in obj]
    if isinstance(obj, str):
        inteiro = PADRAO_VAR.fullmatch(obj)
        if inteiro:
            return v.get(inteiro.group(1), "")
        return PADRAO_VAR.sub(lambda m: str(v.get(m.group(1), "")), obj)
    return obj


def aplicar_path(path, v):
    """Como aplicar(), mas codifica cada valor para caber num trecho da URL
    (chave Pix de e-mail ou telefone tem @ e +)."""
    return PADRAO_VAR.sub(lambda m: urllib.parse.quote(str(v.get(m.group(1), "")), safe=""), path)


def extrair(dados, caminho):
    for parte in caminho.split("."):
        if isinstance(dados, list) and parte.isdigit() and int(parte) < len(dados):
            dados = dados[int(parte)]
        elif isinstance(dados, dict) and parte in dados:
            dados = dados[parte]
        else:
            return None
    return dados


# ---------------------------------------------------------------------------- HTTP

class ClienteC6:
    def __init__(self, software, versao):
        faltando = [k for k in ("C6_CLIENT_ID", "C6_CLIENT_SECRET", "C6_CERT_FILE", "C6_KEY_FILE")
                    if not os.environ.get(k)]
        if faltando:
            sys.exit(f"Defina no ambiente ou em scripts/c6/.env: {', '.join(faltando)}")
        self.base = os.environ.get("C6_BASE_URL", SANDBOX).rstrip("/")
        ctx = ssl.create_default_context()
        ctx.load_cert_chain(os.environ["C6_CERT_FILE"], os.environ["C6_KEY_FILE"])
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        self.token, self.expira = None, 0.0
        # Identificam o ERP nas chamadas; é por eles que o C6 reconhece o software homologado.
        self.parceiro = {"partner-software-name": software, "partner-software-version": versao}

    def _enviar(self, metodo, url, dados=None, headers=None):
        req = urllib.request.Request(url, data=dados, method=metodo, headers=headers or {})
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def autenticar(self):
        form = urllib.parse.urlencode({
            "client_id": os.environ["C6_CLIENT_ID"],
            "client_secret": os.environ["C6_CLIENT_SECRET"],
            "grant_type": "client_credentials",
        }).encode()
        status, tipo, corpo = self._enviar(
            "POST", self.base + AUTH_PATH, form,
            {"Content-Type": "application/x-www-form-urlencoded"})
        if status == 200:
            dados = json.loads(corpo)
            self.token = dados["access_token"]
            self.expira = time.time() + int(dados.get("expires_in", 300)) - 30
        return status, tipo, corpo

    def chamar(self, metodo, path, query=None, body=None):
        if not self.token or time.time() >= self.expira:
            status, _, corpo = self.autenticar()
            if status != 200:
                raise RuntimeError(f"Falha na autenticação ({status}): {corpo[:300]!r}")
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json",
                   **self.parceiro}
        dados = None
        if body is not None:
            dados = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        return self._enviar(metodo, url, dados, headers)


# ------------------------------------------------------------------------ execução

def ler_resultados():
    if RESULTADOS.exists():
        return json.loads(RESULTADOS.read_text(encoding="utf-8"))
    return {"variaveis": {}, "testes": {}}


def gravar_resultados(res):
    EVIDENCIAS.mkdir(exist_ok=True)
    RESULTADOS.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")


def mascarar_token(dados):
    """O token vale 5 minutos, mas não há motivo para gravá-lo inteiro na evidência."""
    if isinstance(dados, dict) and isinstance(dados.get("access_token"), str):
        t = dados["access_token"]
        dados = {**dados, "access_token": t[:12] + "…" + t[-6:] if len(t) > 24 else "…"}
    return dados


def executar_caso(cliente, caso, v):
    path = aplicar_path(caso.get("path", ""), v)
    query = aplicar(caso.get("query"), v)
    body = aplicar(caso.get("body"), v)
    if caso["metodo"] == "AUTH":
        status, tipo, corpo = cliente.autenticar()
        req = {"metodo": "POST", "url": cliente.base + AUTH_PATH,
               "body": "client_id=***&client_secret=***&grant_type=client_credentials"}
    else:
        for _ in range(caso.get("repetir", 1)):
            status, tipo, corpo = cliente.chamar(caso["metodo"], path, query, body)
        req = {"metodo": caso["metodo"], "url": cliente.base + path, "query": query, "body": body}

    if "pdf" in tipo.lower() or corpo[:4] == b"%PDF":
        arq = EVIDENCIAS / f"{caso['id']}.pdf"
        EVIDENCIAS.mkdir(exist_ok=True)
        arq.write_bytes(corpo)
        resposta = f"PDF recebido ({len(corpo)} bytes, Content-Type: {tipo}). Arquivo: {arq.name}"
    else:
        texto = corpo.decode("utf-8", errors="replace")
        try:
            resposta = json.loads(texto) if texto.strip() else ""
        except json.JSONDecodeError:
            resposta = texto
    return status, req, resposta


def cmd_testar(args):
    carregar_env()
    cfg = carregar_config()
    apis = set(args.apis.split(",") if args.apis else cfg.get("apis", [])) | {"auth"}
    so = set(args.so.split(",")) if args.so else None
    desconhecidas = apis - set(API_DO_PREFIXO.values())
    if desconhecidas:
        sys.exit(f"APIs desconhecidas: {', '.join(sorted(desconhecidas))}")

    res = ler_resultados()
    fixas = variaveis_iniciais(cfg)
    # Ao reexecutar só alguns testes, reaproveita ids salvos (boleto_id, group_id...)
    # e os txids/ref da rodada anterior, para consultar o que já foi criado.
    v = {**fixas, **res["variaveis"], **variaveis_do_config(cfg)} if so else dict(fixas)
    protegidas = {k for k, x in fixas.items() if x and (k.startswith("linha_") or k == "e2eid")}

    emp = cfg.get("empresa", {})
    cliente = ClienteC6(emp.get("nome_software", ""), emp.get("versao_software", "1.0.0"))
    print(f"Ambiente: {cliente.base}\n")
    for caso in CASOS:
        cid = caso["id"]
        if api_do_caso(cid) not in apis or (so and cid not in so):
            continue
        faltam = [x for x in caso.get("requer", []) if not v.get(x)]
        if faltam:
            res["testes"][cid] = {"situacao": "PENDENTE", "motivo": f"sem {', '.join(faltam)}",
                                  "titulo": caso["titulo"], "esperado": caso["esperado"]}
            print(f"  {cid:<9} PENDENTE   sem {', '.join(faltam)}")
            continue
        try:
            status, req, resposta = executar_caso(cliente, caso, v)
        except (OSError, RuntimeError, ValueError) as e:
            res["testes"][cid] = {"situacao": "ERRO", "motivo": str(e), "titulo": caso["titulo"],
                                  "esperado": caso["esperado"]}
            print(f"  {cid:<9} ERRO       {e}")
            if caso["metodo"] == "AUTH":
                break
            continue
        if isinstance(resposta, (dict, list)):
            for var, caminhos in caso.get("salvar", {}).items():
                if var in protegidas:
                    continue
                achado = next((x for x in (extrair(resposta, c) for c in caminhos)
                               if x not in (None, "")), None)
                if achado is not None:
                    v[var] = achado
        ok = status == caso["esperado"]
        res["testes"][cid] = {
            "situacao": "OK" if ok else "DIVERGENTE", "titulo": caso["titulo"],
            "esperado": caso["esperado"], "status": status,
            "resposta": mascarar_token(resposta), "requisicao": req,
            "executado_em": dt.datetime.now().isoformat(timespec="seconds"),
        }
        print(f"  {cid:<9} {'OK' if ok else 'DIVERGENTE':<10} {status} (esperado {caso['esperado']})"
              f"  {caso['titulo']}")
        if caso["metodo"] == "AUTH":
            escopos = (resposta or {}).get("scope", "") if isinstance(resposta, dict) else ""
            if escopos:
                print(f"            escopos: {escopos}")
            if status != 200:
                print("\nSem token não há como seguir. Confira certificado, client_id e secret.")
                break
    res["variaveis"] = v
    gravar_resultados(res)
    print(f"\nResultados em {RESULTADOS.relative_to(DIR.parent.parent)}")
    imprimir_status(res, cfg)


def imprimir_status(res, cfg):
    """Resume os testes das APIs do config.json (as que serão marcadas no roteiro)."""
    testes = res["testes"]
    apis = set(cfg.get("apis", [])) | {"auth"}
    previstos = [c for c in ORDEM_ROTEIRO if api_do_caso(c) in apis]
    situacao = {c: testes.get(c, {}).get("situacao", "NÃO EXECUTADO") for c in previstos}
    cont = Counter(situacao.values())
    print("\nResumo: " + ", ".join(f"{k}: {n}" for k, n in sorted(cont.items())))
    for c in previstos:
        if situacao[c] == "OK":
            continue
        t = testes.get(c, {})
        detalhe = t.get("motivo") or (f"{t.get('status')} ≠ {t.get('esperado')}"
                                      if "status" in t else "")
        print(f"  {c:<9} {situacao[c]:<14} {detalhe}")


def cmd_status(_args):
    imprimir_status(ler_resultados(), carregar_config())


# ------------------------------------------------------------------- preenchimento

INICIO_CAMPO = re.compile(r'<w:fldChar w:fldCharType="begin"><w:ffData>(.*?)</w:ffData>', re.S)
SEPARADOR = '<w:fldChar w:fldCharType="separate"/></w:r>'
FIM = '<w:fldChar w:fldCharType="end"/>'


def run_texto(texto, rpr):
    partes = []
    for i, linha in enumerate(texto.split("\n")):
        if i:
            partes.append("<w:br/>")
        partes.append(f'<w:t xml:space="preserve">{html.escape(linha, quote=False)}</w:t>')
    return f"<w:r>{rpr}{''.join(partes)}</w:r>"


def preencher_xml(xml, valores, marcados):
    """Preenche os campos de formulário legados do Word (FORMTEXT/FORMCHECKBOX).

    valores: texto de cada FORMTEXT, em ordem (None = deixa como está);
    marcados: bool de cada FORMCHECKBOX, em ordem.
    Devolve o XML novo e quantos campos de cada tipo foram encontrados.
    """
    partes, pos = [], 0
    n_txt = n_cb = 0
    for m in INICIO_CAMPO.finditer(xml):
        ff = m.group(1)
        if "<w:checkBox>" in ff:
            if n_cb < len(marcados):
                novo_ff = re.sub(r'<w:checked(?: w:val="[01]")?/>', "", ff).replace(
                    "</w:checkBox>", f'<w:checked w:val="{int(marcados[n_cb])}"/></w:checkBox>')
                partes.append(xml[pos:m.start(1)] + novo_ff)
                pos = m.end(1)
            n_cb += 1
            continue
        i = n_txt
        n_txt += 1
        if i >= len(valores) or valores[i] is None:
            continue
        # O resultado do campo fica entre o run do "separate" e o run do "end".
        sep = xml.index(SEPARADOR, m.end()) + len(SEPARADOR)
        trecho = xml[sep:xml.index(FIM, sep)]
        run_fim = sep + max(trecho.rfind("<w:r>"), trecho.rfind("<w:r "))
        rpr = re.search(r"<w:rPr>.*?</w:rPr>", xml[sep:run_fim], re.S)
        partes.append(xml[pos:sep] + run_texto(valores[i], rpr.group(0) if rpr else ""))
        pos = run_fim
    partes.append(xml[pos:])
    return "".join(partes), {"txt": n_txt, "cb": n_cb}


def texto_resposta(resposta):
    if isinstance(resposta, (dict, list)):
        texto = json.dumps(resposta, ensure_ascii=False, indent=2)
    else:
        texto = str(resposta) if resposta != "" else "(sem conteúdo)"
    if len(texto) > LIMITE_BODY:
        texto = texto[:LIMITE_BODY] + f"\n… (truncado; {len(texto)} caracteres no total)"
    return texto


def cmd_preencher(args):
    cfg = carregar_config()
    res = ler_resultados()
    apis = set(cfg.get("apis", []))
    emp = cfg.get("empresa", {})
    valores = [emp.get(k, "") for k in ("cnpj", "nome_empresa", "nome_software", "responsavel",
                                        "email", "telefone")]
    for cid in ORDEM_ROTEIRO:
        t = res["testes"].get(cid)
        if api_do_caso(cid) not in apis | {"auth"} or not t or "status" not in t:
            valores += [None, None]
        else:
            valores += [str(t["status"]), texto_resposta(t["resposta"])]
    marcados = [api in apis for api in CHECKBOXES]

    origem = Path(args.roteiro)
    destino = Path(args.saida)
    with zipfile.ZipFile(origem) as zin:
        xml = zin.read("word/document.xml").decode("utf-8")
        novo, idx = preencher_xml(xml, valores, marcados)
        if idx["txt"] != len(valores) or idx["cb"] != len(marcados):
            sys.exit(f"O .docx tem {idx['txt']} campos de texto e {idx['cb']} checkboxes; "
                     f"esperava {len(valores)} e {len(marcados)}. Versão do roteiro diferente?")
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                dados = novo.encode("utf-8") if item.filename == "word/document.xml" \
                    else zin.read(item.filename)
                zout.writestr(item, dados)
    shutil.move(tmp, destino)
    preenchidos = sum(1 for x in valores[6::2] if x is not None)
    print(f"Roteiro preenchido: {destino} ({preenchidos} testes com resultado)")
    imprimir_status(res, cfg)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("testar", help="executa os testes no sandbox")
    t.add_argument("--apis", help="lista separada por vírgula: " + ",".join(CHECKBOXES))
    t.add_argument("--so", help="ids de teste separados por vírgula (ex.: B_04,B_05)")
    t.set_defaults(func=cmd_testar)
    s = sub.add_parser("status", help="resume o último resultado")
    s.set_defaults(func=cmd_status)
    f = sub.add_parser("preencher", help="gera o roteiro .docx preenchido")
    f.add_argument("--roteiro", default=str(ROTEIRO_PADRAO))
    f.add_argument("--saida", default=str(EVIDENCIAS / "Roteiro_C6_preenchido.docx"))
    f.set_defaults(func=cmd_preencher)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
