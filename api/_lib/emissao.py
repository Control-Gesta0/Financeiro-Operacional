"""Emissão automática de cobranças no Asaas a partir das receitas do ERP Lite (VHSYS).

Regras combinadas com o financeiro (05/10/2026):
- vira cobrança a receita em aberto na conta bancária Asaas, cadastrada a partir de
  EMISSAO_A_PARTIR_DE, ainda sem cobrança e com vencimento de hoje em diante;
- cobrança do tipo BOLETO (o Asaas inclui o QR Code Pix no boleto);
- criada logo depois da receita (o cron roda a cada 15 minutos);
- sem avisos do Asaas ao cliente (notificationDisabled no cliente): o financeiro envia.

A cobrança leva o ID da receita no externalReference: antes de criar, o Asaas é
consultado por essa referência, então rodar de novo nunca duplica. Depois o ID da
cobrança e o link do boleto são gravados nas observações da receita, o que também
deixa a baixa automática achar a receita na primeira tentativa.
"""
import datetime as dt
import os
import re
from collections import Counter
from decimal import Decimal

BRT = dt.timezone(dt.timedelta(hours=-3))
CONTA_ASAAS_PADRAO = "1320902"  # conta bancária "Asaas" no VHSYS
MARCA_EMISSAO = "Cobrança Asaas"
TEM_COBRANCA = re.compile(r"pay_[A-Za-z0-9]+")


def modo():
    return "ativo" if os.environ.get("EMISSAO_MODO", "").strip().lower() == "ativo" else "simulacao"


def hoje():
    return dt.datetime.now(BRT).date().isoformat()


def conta_asaas():
    return os.environ.get("VHSYS_ID_BANCO_ASAAS") or CONTA_ASAAS_PADRAO


def _digitos(texto):
    return re.sub(r"\D", "", str(texto or ""))


def _data_iso(texto):
    """Aceita "2026-10-05 09:00:00" e "05/10/2026 09:00"; devolve "2026-10-05" ou ""."""
    texto = str(texto or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if m:
        return "-".join(m.groups())
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", texto)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def motivo_para_nao_emitir(receita, desde, dia):
    """None se a receita deve virar cobrança; senão o motivo (para o relatório)."""
    if str(receita.get("id_banco") or "") != conta_asaas():
        return "outra conta bancária"
    if _data_iso(receita.get("data_cad_rec")) < desde:
        return "cadastrada antes do início da emissão"
    if receita.get("liquidado_rec") == "Sim":
        return "já liquidada"
    if TEM_COBRANCA.search(receita.get("observacoes_rec") or ""):
        return "já tem cobrança no Asaas"
    if Decimal(str(receita.get("valor_rec") or 0)) <= 0:
        return "valor zerado"
    if _data_iso(receita.get("vencimento_rec")) < dia:
        return "vencida: o Asaas não aceita boleto com vencimento no passado"
    return None


def dados_cliente_asaas(cliente):
    dados = {
        "name": cliente.get("razao_cliente") or cliente.get("fantasia_cliente"),
        "cpfCnpj": _digitos(cliente.get("cnpj_cliente")),
        "email": cliente.get("email_cliente") or None,
        "mobilePhone": _digitos(cliente.get("celular_cliente")) or None,
        "phone": _digitos(cliente.get("fone_cliente")) or None,
        "postalCode": _digitos(cliente.get("cep_cliente")) or None,
        "address": cliente.get("endereco_cliente") or None,
        "addressNumber": cliente.get("numero_cliente") or None,
        "complement": cliente.get("complemento_cliente") or None,
        "province": cliente.get("bairro_cliente") or None,
        "externalReference": str(cliente.get("id_cliente") or ""),
        "notificationDisabled": True,
    }
    return {k: v for k, v in dados.items() if v not in (None, "")}


def obter_cliente_asaas(receita, vhsys, asaas, aplicar):
    """Devolve (id do cliente no Asaas ou None em simulação, o que foi feito)."""
    cliente = vhsys.consultar_cliente(receita.get("id_cliente")) or {}
    documento = _digitos(cliente.get("cnpj_cliente"))
    if not documento:
        raise ValueError(f"cliente {receita.get('id_cliente')} sem CPF/CNPJ no VHSYS")
    existente = asaas.buscar_cliente_por_documento(documento)
    if existente:
        if existente.get("notificationDisabled"):
            return existente["id"], "existente"
        if aplicar:
            asaas.atualizar_cliente(existente["id"], {"notificationDisabled": True})
        return existente["id"], "existente, avisos do Asaas desligados"
    if not aplicar:
        return None, "seria cadastrado"
    return asaas.criar_cliente(dados_cliente_asaas(cliente))["id"], "cadastrado"


def dados_cobranca(receita, id_cliente):
    return {
        "customer": id_cliente,
        "billingType": "BOLETO",
        "value": float(Decimal(str(receita.get("valor_rec")))),
        "dueDate": receita.get("vencimento_rec"),
        "description": (receita.get("nome_conta") or "")[:500],
        "externalReference": str(receita.get("id_conta_rec")),
    }


def observacao_com_cobranca(receita, cobranca):
    linha = (f"{MARCA_EMISSAO} {cobranca.get('id')} (fatura {cobranca.get('invoiceNumber')}). "
             f"Boleto: {cobranca.get('bankSlipUrl') or cobranca.get('invoiceUrl')}")
    atual = (receita.get("observacoes_rec") or "").strip()
    return f"{atual}\n{linha}" if atual else linha


def campos_boleto(cobranca, asaas):
    """Campos de boleto da receita (os que a API do VHSYS mostra): forma de pagamento,
    link do boleto e Pix copia e cola. A gravação é conferida campo a campo."""
    campos = {"forma_pagamento": "Boleto",
              "link_boleto": cobranca.get("bankSlipUrl") or cobranca.get("invoiceUrl")}
    try:
        pix = asaas.pix_qrcode(cobranca["id"]) or {}
        if pix.get("payload"):
            campos["brcode"] = pix["payload"]
    except getattr(asaas, "ErroAsaas", ValueError):
        pass  # conta sem chave Pix: segue só com o boleto
    return {k: v for k, v in campos.items() if v}


def gravar_campos_boleto(id_receita, cobranca, vhsys, asaas):
    """Tenta gravar os campos de boleto e devolve o que o VHSYS de fato guardou."""
    campos = campos_boleto(cobranca, asaas)
    try:
        vhsys.atualizar_receita(id_receita, campos)
    except getattr(vhsys, "ErroVhsys", ValueError) as e:
        return {"enviados": campos, "erro": str(e)}
    relida = vhsys.consultar_receita(id_receita) or {}
    return {"enviados": campos,
            "gravados": {k: (str(relida.get(k) or "") == str(v)) for k, v in campos.items()}}


def sincronizar_receita(id_receita, vhsys, asaas, aplicar):
    """Para uma receita já emitida: busca a cobrança pelo ID da receita e grava os
    campos de boleto (útil para as emitidas antes desse recurso)."""
    cobranca = asaas.buscar_cobranca_por_referencia(str(id_receita))
    if not cobranca:
        return {"receita": id_receita, "resultado": "sem_cobranca",
                "motivo": "nenhuma cobrança no Asaas com essa receita como referência"}
    base = {"receita": id_receita, "cobranca": cobranca.get("id")}
    if not aplicar:
        return {**base, "resultado": "seria_sincronizada",
                "campos": campos_boleto(cobranca, asaas)}
    return {**base, "resultado": "sincronizada",
            "campos_boleto": gravar_campos_boleto(id_receita, cobranca, vhsys, asaas)}


def emitir_receita(receita, vhsys, asaas, aplicar):
    id_receita = receita.get("id_conta_rec")
    base = {"receita": id_receita, "cliente": receita.get("nome_cliente"),
            "valor": receita.get("valor_rec"), "vencimento": receita.get("vencimento_rec")}
    cobranca = asaas.buscar_cobranca_por_referencia(str(id_receita))
    if cobranca:
        base.update(resultado="ja_emitida", cobranca=cobranca.get("id"))
    else:
        id_cliente, cliente_status = obter_cliente_asaas(receita, vhsys, asaas, aplicar)
        base["cliente_asaas"] = cliente_status
        if not aplicar:
            return {**base, "resultado": "seria_emitida",
                    "cobranca": dados_cobranca(receita, id_cliente or "(novo)")}
        cobranca = asaas.criar_cobranca(dados_cobranca(receita, id_cliente))
        base.update(resultado="emitida", cobranca=cobranca.get("id"))
    base["boleto"] = cobranca.get("bankSlipUrl") or cobranca.get("invoiceUrl")
    if aplicar:
        vhsys.atualizar_receita(id_receita, {"observacoes_rec": observacao_com_cobranca(receita, cobranca)})
        relida = vhsys.consultar_receita(id_receita) or {}
        base["link_gravado"] = cobranca.get("id", "") in (relida.get("observacoes_rec") or "")
        base["campos_boleto"] = gravar_campos_boleto(id_receita, cobranca, vhsys, asaas)
    return base


FORA_DO_ESCOPO = ("outra conta bancária", "cadastrada antes do início da emissão",
                  "já liquidada", "já tem cobrança no Asaas")


def emitir(vhsys, asaas, aplicar=False, desde=None, dia=None, diagnostico=False):
    desde = desde or os.environ.get("EMISSAO_A_PARTIR_DE", "")
    if not desde:
        raise ValueError("defina EMISSAO_A_PARTIR_DE (AAAA-MM-DD) na Vercel")
    dia = dia or hoje()
    resultados, ignoradas = [], []
    for receita in vhsys.receitas_modificadas_desde(desde):
        motivo = motivo_para_nao_emitir(receita, desde, dia)
        if motivo in FORA_DO_ESCOPO:
            if diagnostico:  # mostra por que ficou de fora, com os campos que decidem
                ignoradas.append({
                    "receita": receita.get("id_conta_rec"), "cliente": receita.get("nome_cliente"),
                    "motivo": motivo, "id_banco": receita.get("id_banco"),
                    "data_cad_rec": receita.get("data_cad_rec"),
                    "vencimento_rec": receita.get("vencimento_rec"),
                    "observacoes_rec": (receita.get("observacoes_rec") or "")[:120]})
            continue  # fora do escopo: não polui o relatório
        if motivo:
            resultados.append({"receita": receita.get("id_conta_rec"),
                               "cliente": receita.get("nome_cliente"),
                               "resultado": "nao_emitida", "motivo": motivo})
            continue
        erros = (ValueError, getattr(asaas, "ErroAsaas", ValueError),
                 getattr(vhsys, "ErroVhsys", ValueError))
        try:
            resultados.append(emitir_receita(receita, vhsys, asaas, aplicar))
        except erros as e:  # cadastro incompleto ou recusa do Asaas: segue com as demais
            resultados.append({"receita": receita.get("id_conta_rec"),
                               "cliente": receita.get("nome_cliente"),
                               "resultado": "nao_emitida", "motivo": str(e)})
    retorno = {"desde": desde, "aplicado": aplicar,
               "resumo": dict(Counter(r["resultado"] for r in resultados)),
               "resultados": resultados}
    if diagnostico:
        retorno["ignoradas"] = ignoradas
        retorno["conta_asaas"] = conta_asaas()
    return retorno
