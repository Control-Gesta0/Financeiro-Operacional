"""Cobrança pelo WhatsApp (Zaptos) das receitas com boleto no Asaas.

Valem as receitas emitidas pela integração ("Cobrança Asaas pay_...") e, desde
07/10/2026, as antigas que o ERP anterior ligou ao Asaas ("Cobranca em aberto no Asaas
(pay_...)"): estas só recebem o boleto 10 dias antes do vencimento (nada imediato, sem
resumo de parcelas) e os lembretes.

Regras combinadas com o financeiro (05/10/2026):
- etapa "boleto": a cobrança única e a 1ª parcela de uma parcelada vão logo depois da
  emissão (a 1ª leva o resumo: total, cada parcela com valor e vencimento, e o aviso de
  que os próximos boletos vão 10 dias antes de cada vencimento); as demais parcelas vão BOLETO_DIAS_ANTES dias (padrão 10) antes do próprio
  vencimento (ou já, se vencerem antes disso). Cada parcela é uma receita no ERP; as
  da mesma cobrança são reconhecidas pelo cliente + "identificacao" (OS, pedido) ou,
  sem ela, pelo minuto do cadastro. Roda no cron da emissão, só em horário comercial
  (ENVIO_DAS..ENVIO_ATE, horário de Brasília);
- etapa "vencimento": na manhã do dia do vencimento, se ainda estiver em aberto;
- etapa "atraso": LEMBRETE_DIAS_ATRASO dias (padrão 1) depois do vencimento, contados
  a partir do primeiro dia útil quando o vencimento cai no fim de semana;
- vai para o celular do cadastro do cliente no ERP Lite;
- formato: mensagens comuns (o WhatsApp é conectado por QR Code, sem botões):
  1) texto com valor, vencimento, linha digitável e link do boleto; 2) o PDF do boleto
  como documento, que já traz o QR Code Pix da própria cobrança. O Pix copia e cola
  separado saiu em 06/10/2026 a pedido do financeiro (clientes colavam o código no
  campo de chave Pix do banco e recebiam "chave inválida").

Antes de cada envio a cobrança é consultada no Asaas: só segue se estiver PENDING ou
OVERDUE. Cada envio deixa uma marca nas observações da receita, então rodar de novo
nunca manda a mesma etapa duas vezes. Só grava/envia com WHATSAPP_COBRANCA_MODO=ativo.

Canal e-mail (08/10/2026): a mesma régua e o mesmo texto vão também para o e-mail do
cadastro do ERP (vários separados por ";"), com o PDF do boleto anexado, pelo SMTP da
empresa (correio.py). Cada canal tem a sua marca ("WhatsApp: ..." / "E-mail: ..."),
então um não bloqueia o outro. Só envia com EMAIL_COBRANCA_MODO=ativo.
"""
import datetime as dt
import os
import re
from collections import Counter
from decimal import Decimal

import correio
import emissao
import whatsapp

BRT = dt.timezone(dt.timedelta(hours=-3))
ENVIO_DAS, ENVIO_ATE = 8, 20  # janela do envio automático do boleto (horas, Brasília)
ETAPAS = ("boleto", "vencimento", "atraso")
CANAIS = ("whatsapp", "email")
MARCAS_CANAL = {
    "whatsapp": {"boleto": "WhatsApp: boleto enviado",
                 "vencimento": "WhatsApp: lembrete de vencimento enviado",
                 "atraso": "WhatsApp: aviso de atraso enviado"},
    "email": {"boleto": "E-mail: boleto enviado",
              "vencimento": "E-mail: lembrete de vencimento enviado",
              "atraso": "E-mail: aviso de atraso enviado"},
}
MARCAS = MARCAS_CANAL["whatsapp"]
STATUS_A_COBRAR = ("PENDING", "OVERDUE")
COBRANCA_DA_RECEITA = re.compile(re.escape(emissao.MARCA_EMISSAO) + r" (pay_[A-Za-z0-9]+)")


def modo(canal="whatsapp"):
    variavel = "EMAIL_COBRANCA_MODO" if canal == "email" else "WHATSAPP_COBRANCA_MODO"
    return "ativo" if os.environ.get(variavel, "").strip().lower() == "ativo" else "simulacao"


def canais_configurados():
    """WhatsApp sempre; e-mail quando o SMTP estiver configurado na Vercel."""
    return [c for c in CANAIS if c == "whatsapp" or correio.configurado()]


def agora():
    return dt.datetime.now(BRT)


def em_horario_comercial(momento=None):
    return ENVIO_DAS <= (momento or agora()).hour < ENVIO_ATE


PRIMEIRA_NOVA_POR = 7  # dias após o cadastro em que a 1ª parcela ainda vai "na hora"


def dias_antes():
    try:
        return max(0, int(os.environ.get("BOLETO_DIAS_ANTES") or 10))
    except ValueError:
        return 10


def dias_atraso():
    try:
        return max(1, int(os.environ.get("LEMBRETE_DIAS_ATRASO") or 1))
    except ValueError:
        return 1


def _data_br(iso):
    try:
        return dt.date.fromisoformat(emissao._data_iso(iso)).strftime("%d/%m/%Y")
    except ValueError:
        return str(iso or "")


def _reais(valor):
    texto = f"{Decimal(str(valor or 0)):,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def primeiro_dia_util(data):
    while data.weekday() >= 5:  # sábado/domingo: o boleto pode ser pago na segunda
        data += dt.timedelta(days=1)
    return data


def telefone(cliente):
    """Celular do cadastro com DDI 55, ou None se não parecer um número válido."""
    numero = whatsapp.normalizar((cliente or {}).get("celular_cliente"))
    return None if not numero or numero.endswith("@g.us") else numero


def nome_cliente(cliente, receita):
    nome = ((cliente or {}).get("fantasia_cliente") or (cliente or {}).get("razao_cliente")
            or receita.get("nome_cliente") or "").strip()
    if not nome.isupper():
        return nome
    # "EDUARDO DO VALE" -> "Eduardo do Vale"; siglas como LTDA/ME/EPP ficam maiúsculas
    minusculas, siglas = {"de", "da", "do", "das", "dos", "e"}, {"LTDA", "ME", "EPP", "EIRELI", "SA", "S/A"}
    palavras = []
    for i, palavra in enumerate(nome.split()):
        if palavra in siglas:
            palavras.append(palavra)
        elif i and palavra.lower() in minusculas:
            palavras.append(palavra.lower())
        else:
            palavras.append(palavra.capitalize())
    return " ".join(palavras)


def id_cobranca(receita):
    """Cobrança do Asaas ligada à receita: a emitida pela integração ou a antiga."""
    obs = receita.get("observacoes_rec") or ""
    nossa = COBRANCA_DA_RECEITA.search(obs)
    if nossa:
        return nossa.group(1)
    antiga = emissao.TEM_COBRANCA.search(obs)
    return antiga.group(0) if antiga else None


def da_integracao(receita):
    """Boleto emitido pela integração (os antigos não têm envio imediato nem resumo)."""
    return bool(COBRANCA_DA_RECEITA.search(receita.get("observacoes_rec") or ""))


def marca_enviada(receita, etapa, canal="whatsapp"):
    """Data (dd/mm/aaaa) em que a etapa já foi enviada por esse canal, ou None."""
    m = re.search(re.escape(MARCAS_CANAL[canal][etapa]) + r" em (\d{2}/\d{2}/\d{4})",
                  receita.get("observacoes_rec") or "")
    return m.group(1) if m else None


def chave_grupo(receita):
    """Receitas com a mesma chave são parcelas da mesma cobrança."""
    identificacao = str(receita.get("identificacao") or "").strip()
    if identificacao not in ("", "0"):
        return (str(receita.get("id_cliente")), identificacao)
    return (str(receita.get("id_cliente")), str(receita.get("data_cad_rec") or "")[:16])


def parcelas_da_cobranca(receita, vhsys, cache):
    """Parcelas da cobrança da receita (ela inclusive), por vencimento, contando as já
    pagas. Uma consulta ao VHSYS por cliente; o cache vale para a rodada inteira."""
    cliente = str(receita.get("id_cliente"))
    if cliente not in cache:
        cache[cliente] = vhsys.receitas_do_cliente(cliente)
    grupo = [r for r in cache[cliente] if chave_grupo(r) == chave_grupo(receita)]
    if not any(str(r.get("id_conta_rec")) == str(receita.get("id_conta_rec")) for r in grupo):
        grupo.append(receita)
    return sorted(grupo, key=lambda r: (emissao._data_iso(r.get("vencimento_rec")),
                                        int(r.get("id_conta_rec") or 0)))


def posicao_da_parcela(receita, parcelas):
    ids = [str(r.get("id_conta_rec")) for r in parcelas]
    return ids.index(str(receita.get("id_conta_rec"))) + 1


def e_primeira_parcela(receita, vhsys, cache):
    """True se a receita é a de vencimento mais cedo da sua cobrança, contando as parcelas
    já pagas (se o cliente pagou a 1ª na hora, a 2ª não vira "primeira")."""
    return posicao_da_parcela(receita, parcelas_da_cobranca(receita, vhsys, cache)) == 1


def resumo_das_parcelas(parcelas):
    """Descritivo enviado junto com a 1ª parcela: total, cada parcela e a regra de envio."""
    total = sum(Decimal(str(r.get("valor_rec") or 0)) for r in parcelas)
    linhas = [f"Resumo da cobrança: {_reais(total)} em {len(parcelas)} parcelas"]
    for n, r in enumerate(parcelas, 1):
        linhas.append(f"• {n}ª parcela: {_reais(r.get('valor_rec'))}, "
                      f"vencimento em {_data_br(r.get('vencimento_rec'))}")
    linhas.append(f"Os próximos boletos serão enviados por aqui {dias_antes()} dias antes de "
                  "cada vencimento.")
    return "\n".join(linhas)


def boleto_cabe_hoje(receita, hoje, vhsys, cache, canal="whatsapp"):
    if marca_enviada(receita, "boleto", canal):
        return False
    vencimento = emissao._data_iso(receita.get("vencimento_rec"))
    if vencimento < hoje.isoformat():
        return False  # vencida: quem cuida é o aviso de atraso
    if vencimento <= (hoje + dt.timedelta(days=dias_antes())).isoformat():
        return True
    if not da_integracao(receita):
        return False  # boleto antigo do Asaas: só perto do vencimento
    cadastro = emissao._data_iso(receita.get("data_cad_rec"))
    if cadastro < (hoje - dt.timedelta(days=PRIMEIRA_NOVA_POR)).isoformat():
        return False  # antiga: a 1ª parcela já teve a sua vez
    return e_primeira_parcela(receita, vhsys, cache)


def etapa_do_dia(receita, hoje, canal="whatsapp"):
    """Etapa de lembrete que cabe hoje para a receita ("vencimento", "atraso") ou None."""
    try:
        vencimento = dt.date.fromisoformat(emissao._data_iso(receita.get("vencimento_rec")))
    except ValueError:
        return None
    if vencimento == hoje:
        hoje_br = hoje.strftime("%d/%m/%Y")
        if marca_enviada(receita, "vencimento", canal) \
                or marca_enviada(receita, "boleto", canal) == hoje_br:
            return None  # boleto mandado hoje mesmo já serve de lembrete
        return "vencimento"
    inicio = primeiro_dia_util(vencimento) + dt.timedelta(days=dias_atraso())
    # janela de 3 dias: se o cron falhar um dia, o aviso sai no seguinte; mais que isso
    # não manda (evita disparar para atrasos antigos ao ligar o recurso)
    if inicio <= hoje <= inicio + dt.timedelta(days=2) and hoje.weekday() < 5 \
            and not marca_enviada(receita, "atraso", canal):
        return "atraso"
    return None


def textos(etapa, nome, receita, parcela=None):
    """parcela: (n, total) quando a receita é parcela de uma cobrança parcelada."""
    valor, venc = _reais(receita.get("valor_rec")), _data_br(receita.get("vencimento_rec"))
    descricao = (receita.get("nome_conta") or "").strip()
    saudacao = f"Olá, {nome}!" if nome else "Olá!"
    if etapa == "boleto":
        qual = f" de {descricao}" if descricao else ""
        if parcela:
            qual += f" (parcela {parcela[0]} de {parcela[1]})"
        corpo = (f"{saudacao} Segue o boleto{qual} no valor de {valor}, com vencimento em "
                 f"{venc}. Dá para pagar pelo Pix ou pelo código de barras.")
    elif etapa == "vencimento":
        corpo = (f"{saudacao} Passando para lembrar que o boleto de {valor} vence hoje "
                 f"({venc}). Se já pagou, pode desconsiderar.")
    else:
        corpo = (f"{saudacao} Ainda não identificamos o pagamento do boleto de {valor}, "
                 f"que venceu em {venc}. Os dados para pagamento seguem abaixo. Se já pagou, "
                 "pode desconsiderar.")
    return corpo


def dados_pagamento(cobranca, asaas):
    """Linha digitável do boleto (None se o Asaas ainda não tiver)."""
    try:
        linha = (asaas.linha_digitavel(cobranca["id"]) or {}).get("identificationField")
    except getattr(asaas, "ErroAsaas", ValueError):
        linha = None  # boleto ainda sem registro: segue com o link e o PDF
    return {"linha": linha}


def texto_principal(texto, cobranca, pagamento):
    linhas = [texto]
    if pagamento.get("linha"):
        linhas += ["", "Linha digitável:", pagamento["linha"]]
    link = cobranca.get("invoiceUrl") or cobranca.get("bankSlipUrl")
    if link:
        linhas += ["", f"Boleto e fatura: {link}"]
    return "\n".join(linhas)


def enviar(numero, etapa, texto, receita, cobranca, pagamento):
    """Manda o texto e, em seguida, o PDF do boleto (com o QR Code Pix), em todas as etapas.

    Se o texto falhar, nada foi enviado e a etapa fica para o próximo cron. Depois que
    ele saiu, uma falha no PDF só é anotada: repetir mandaria o texto de novo.
    """
    whatsapp.enviar_texto(numero, texto_principal(texto, cobranca, pagamento))
    enviados, falhas = ["texto"], {}
    if cobranca.get("bankSlipUrl"):
        fatura = cobranca.get("invoiceNumber") or receita.get("id_conta_rec")
        try:
            whatsapp.enviar_documento(numero, cobranca["bankSlipUrl"], f"boleto-{fatura}.pdf")
            enviados.append("pdf")
        except whatsapp.ErroWhatsapp as e:
            falhas["pdf"] = str(e)
    return enviados, falhas


def assunto(etapa, receita, parcela=None):
    descricao = (receita.get("nome_conta") or "").strip()
    venc = _data_br(receita.get("vencimento_rec"))
    if etapa == "boleto":
        qual = f" {descricao}" if descricao else ""
        if parcela:
            qual += f" (parcela {parcela[0]} de {parcela[1]})"
        return f"Boleto{qual}: vencimento {venc}"
    if etapa == "vencimento":
        return f"Lembrete: seu boleto vence hoje ({venc})"
    return f"Boleto em aberto: venceu em {venc}"


def enviar_email(destinos, etapa, texto, receita, cobranca, pagamento, parcela=None):
    """O mesmo texto do WhatsApp por e-mail, com o PDF do boleto anexado (se baixar)."""
    anexos, enviados = [], ["email"]
    if cobranca.get("bankSlipUrl"):
        pdf = correio.baixar_pdf(cobranca["bankSlipUrl"])
        if pdf:
            fatura = cobranca.get("invoiceNumber") or receita.get("id_conta_rec")
            anexos.append((f"boleto-{fatura}.pdf", pdf))
            enviados.append("pdf")
    recusados = correio.enviar(destinos, assunto(etapa, receita, parcela),
                               texto_principal(texto, cobranca, pagamento), anexos)
    return enviados, ({"recusados": ", ".join(recusados)} if recusados else {})


def cobrar_receita(receita, etapa, vhsys, asaas, aplicar, hoje, reenviar=False, cache=None,
                   canal="whatsapp"):
    id_receita = receita.get("id_conta_rec")
    base = {"receita": id_receita, "cliente": receita.get("nome_cliente"), "etapa": etapa,
            "canal": canal, "valor": receita.get("valor_rec"),
            "vencimento": receita.get("vencimento_rec")}
    pid = id_cobranca(receita)
    if not pid:
        return {**base, "resultado": "nao_enviado", "motivo": "receita sem cobrança Asaas"}
    base["cobranca"] = pid
    if marca_enviada(receita, etapa, canal) and not reenviar:
        return {**base, "resultado": "ja_enviado", "em": marca_enviada(receita, etapa, canal)}
    cobranca = asaas.consultar_cobranca(pid) or {}
    if cobranca.get("deleted"):  # excluída no Asaas (ex.: paga por fora, via Pix no C6)
        return {**base, "resultado": "nao_enviado", "motivo": "cobrança excluída no Asaas"}
    if cobranca.get("status") not in STATUS_A_COBRAR:
        return {**base, "resultado": "nao_enviado",
                "motivo": f"cobrança {cobranca.get('status') or 'não encontrada'} no Asaas"}
    cliente = vhsys.consultar_cliente(receita.get("id_cliente")) or {}
    if canal == "email":
        destinos = correio.enderecos(cliente.get("email_cliente"))
        if not destinos:
            return {**base, "resultado": "sem_email",
                    "motivo": "cliente sem e-mail válido no cadastro do ERP Lite"}
        base["email"] = ", ".join(correio.mascarar(d) for d in destinos)
    else:
        numero = telefone(cliente)
        if not numero:
            return {**base, "resultado": "sem_whatsapp",
                    "motivo": "cliente sem celular válido no cadastro do ERP Lite"}
        base["numero"] = whatsapp.mascarar(numero)
    parcela, resumo = None, ""
    if etapa == "boleto" and da_integracao(receita):
        parcelas = parcelas_da_cobranca(receita, vhsys, {} if cache is None else cache)
        if len(parcelas) > 1:
            parcela = (posicao_da_parcela(receita, parcelas), len(parcelas))
            if parcela[0] == 1:
                resumo = "\n\n" + resumo_das_parcelas(parcelas)
    texto = textos(etapa, nome_cliente(cliente, receita), receita, parcela) + resumo
    if not aplicar:
        return {**base, "resultado": "seria_enviado", "texto": texto}
    pagamento = dados_pagamento(cobranca, asaas)
    if canal == "email":
        enviados, falhas = enviar_email(destinos, etapa, texto, receita, cobranca, pagamento,
                                        parcela)
    else:
        numero = whatsapp.destino_verificado(numero)
        if not numero:
            return {**base, "resultado": "sem_whatsapp",
                    "motivo": "o celular do cadastro não tem WhatsApp"}
        enviados, falhas = enviar(numero, etapa, texto, receita, cobranca, pagamento)
    base.update(resultado="enviado", mensagens=enviados)
    if falhas:
        base["falhas"] = falhas
    # marca a etapa na receita (relida agora: a emissão pode ter mudado as observações)
    atual = (vhsys.consultar_receita(id_receita) or receita).get("observacoes_rec") or ""
    linha = f"{MARCAS_CANAL[canal][etapa]} em {hoje.strftime('%d/%m/%Y')} ({', '.join(enviados)})."
    vhsys.atualizar_receita(id_receita, {"observacoes_rec": f"{atual.strip()}\n{linha}".strip()})
    relida = vhsys.consultar_receita(id_receita) or {}
    base["marca_gravada"] = linha in (relida.get("observacoes_rec") or "")
    return base


def cobrar(vhsys, asaas, etapas, aplicar=False, desde=None, hoje=None, receitas=None,
           canal="whatsapp"):
    """Percorre as receitas em aberto com boleto no Asaas e manda as etapas pedidas.

    etapas: ("boleto",) no cron da emissão; ("vencimento", "atraso") no cron diário.
    desde: mantido por compatibilidade; a lista agora é de todas as receitas em aberto.
    """
    hoje = hoje or agora().date()
    erros = (ValueError, whatsapp.ErroWhatsapp, correio.ErroEmail,
             getattr(asaas, "ErroAsaas", ValueError),
             getattr(vhsys, "ErroVhsys", ValueError))
    resultados, cache = [], {}
    lista = receitas if receitas is not None else vhsys.receitas_em_aberto()
    for receita in lista:
        if receita.get("liquidado_rec") == "Sim" or not id_cobranca(receita):
            continue
        if str(receita.get("id_banco") or "") != emissao.conta_asaas():
            continue
        etapa = "boleto" if "boleto" in etapas else etapa_do_dia(receita, hoje, canal)
        if etapa not in etapas:
            continue
        try:
            if etapa == "boleto" and not boleto_cabe_hoje(receita, hoje, vhsys, cache, canal):
                continue
            resultados.append(cobrar_receita(receita, etapa, vhsys, asaas, aplicar, hoje,
                                             cache=cache, canal=canal))
        except erros as e:  # um cliente com problema não trava os demais
            resultados.append({"receita": receita.get("id_conta_rec"),
                               "cliente": receita.get("nome_cliente"), "etapa": etapa,
                               "canal": canal, "resultado": "erro", "motivo": str(e)})
    return {"dia": hoje.isoformat(), "canal": canal, "etapas": list(etapas), "aplicado": aplicar,
            "resumo": dict(Counter(r["resultado"] for r in resultados)),
            "resultados": resultados}
