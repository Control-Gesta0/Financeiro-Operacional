"""Casos de teste do "Roteiro de Testes - C6 Developers v3.0".

Cada caso descreve uma requisição. Strings no formato ${nome} são trocadas pelo
valor da variável: as de config.json, as calculadas em homologacao.py (datas,
txids) e as salvas de respostas anteriores (campo "salvar").

    salvar = {"variavel": ["caminho.na.resposta", "caminho.alternativo"]}

O primeiro caminho encontrado vence. Índices de lista entram como número
("items.0.id"). Um caso cujo "requer" tenha variável vazia fica PENDENTE.

ATENÇÃO: prefixos, nomes de campos e enums abaixo seguem o roteiro e o padrão
Pix do BACEN. Os que não estão no roteiro estão marcados com CONFERIR e devem
ser validados no portal (https://developers.c6bank.com.br) antes da 1ª rodada.
"""

# Prefixos por API. CONFERIR no portal se algum endpoint responder 404.
AP = "/v1/schedule_payments"
BOLETO = "/v1/bank_slips"
WEBHOOK = "/v1/webhooks"
EXTRATO = "/v1"
PIX = "/v2/pix"
BOLEPIX = "/v2/bank_slips"

# Ordem dos testes no .docx (cada um tem dois campos: status code e response body).
ORDEM_ROTEIRO = [
    "AT_01",
    "AP_01", "AP_02", "AP_03", "AP_04", "AP_05", "AP_06",
    "B_01", "B_02", "B_03", "B_04", "B_05", "B_06", "B_07", "B_08",
    "C_01", "C_02", "C_03", "C_04", "C_0501", "C_0502",
    "E_01", "E_02",
    "P_01_01", "P_01_02", "P_01_03", "P_01_04", "P_01_05",
    "P_02_01", "P_02_02", "P_02_03", "P_02_04",
    "P_03_01", "P_03_02", "P_03_03", "P_03_04",
    "P_04_01", "P_04_02", "P_04_03", "P_04_04",
    "P_05_01", "P_05_02", "P_05_03", "P_05_04",
    "P_06_01", "P_06_02", "P_06_03",
    "TR_01", "TR_02",
    "PA_01_01", "PA_01_02", "PA_01_03", "PA_02_01", "PA_02_02", "PA_02_03", "PA_02_04",
    "PA_03_01", "PA_03_02", "PA_03_03", "PA_03_04", "PA_04_01", "PA_04_02", "PA_04_03", "PA_04_04",
    "BP_01_01", "BP_01_02", "BP_02", "BP_03", "BP_04", "BP_05", "BP_06",
]

# Checkboxes da página "MARQUE O CHECKBOX COM AS APIS QUE VOCÊ TESTARÁ", na ordem do .docx.
CHECKBOXES = ["agendamento", "boleto", "checkout", "extrato", "pix", "recebiveis",
              "pix_automatico", "bolepix"]

# Prefixo do id do teste -> API (para --apis e para marcar os checkboxes).
API_DO_PREFIXO = {"AT": "auth", "AP": "agendamento", "B": "boleto", "C": "checkout",
                  "E": "extrato", "P": "pix", "TR": "recebiveis", "PA": "pix_automatico",
                  "BP": "bolepix"}

# CONFERIR: o roteiro não detalha o payload da API de Boleto v1; aqui segue o
# mesmo desenho do BolePix v2, com o endereço desmembrado. Se o 1º teste voltar
# 400, a mensagem de validação do C6 aponta o campo a ajustar.
PAGADOR_BOLETO = {
    "name": "${pagador.nome}",
    "tax_id": "${pagador.documento}",
    "email": "${pagador.email}",
    "address": {
        "street": "${pagador.logradouro}",
        "number": "${pagador.numero}",
        "complement": "${pagador.complemento}",
        "city": "${pagador.cidade}",
        "state": "${pagador.uf}",
        "zip_code": "${pagador.cep}",
    },
}

PAGADOR_BOLEPIX = {
    "name": "${pagador.nome}",
    "tax_id": "${pagador.documento}",
    "email": "${pagador.email}",
    "address": {
        "address": "${pagador.logradouro}, ${pagador.numero}",
        "neighborhood": "${pagador.bairro}",
        "city": "${pagador.cidade}",
        "state": "${pagador.uf}",
        "zip_code": "${pagador.cep}",
    },
}

PAGAMENTO_BOLEPIX = {
    "bank_slip": {"billing_scheme": "${billing_scheme}"},
    "pix": {"key": "${chave_pix}", "type": "${chave_pix_tipo}"},
}

DEVEDOR_PIX = {"cpf": "${pagador.documento}", "nome": "${pagador.nome}"}

DEVEDOR_PIX_COBV = {
    "cpf": "${pagador.documento}",
    "nome": "${pagador.nome}",
    "logradouro": "${pagador.logradouro}, ${pagador.numero}",
    "cidade": "${pagador.cidade}",
    "uf": "${pagador.uf}",
    "cep": "${pagador.cep}",
}


def _cobv(txid, valor):
    return {
        "calendario": {"dataDeVencimento": "${vencimento}", "validadeAposVencimento": 30},
        "txid": txid,
        "devedor": DEVEDOR_PIX_COBV,
        "valor": {"original": valor},
        "chave": "${chave_pix}",
    }


# Lista em ORDEM DE EXECUÇÃO (Boleto roda antes do Agendamento para reaproveitar
# as linhas digitáveis; Pix recebidos lista antes de consultar um específico).
CASOS = [
    # ---------------------------------------------------------------- Autenticação
    {"id": "AT_01", "titulo": "Geração do token de sessão", "metodo": "AUTH", "esperado": 200},

    # ---------------------------------------------------------------------- Boleto
    {"id": "B_01", "titulo": "Emissão de boleto simples", "metodo": "POST", "path": BOLETO + "/",
     "esperado": 201,
     "body": {"external_reference_id": "${ext_b1}", "amount": 10.50, "due_date": "${vencimento}",
              "payer": PAGADOR_BOLETO},
     "salvar": {"boleto_id": ["id"],
                "linha_1": ["digitable_line", "bar_code", "barcode"]}},
    {"id": "B_02", "titulo": "Emissão de boleto com juros e multa", "metodo": "POST",
     "path": BOLETO + "/", "esperado": 201,
     # Campos de multa/juros no formato "fees" do BolePix v2 (FIXED_VALUE, VALUE_PER_DAY).
     # CONFERIR se a API v1 aceita o mesmo bloco.
     "body": {"external_reference_id": "${ext_b2}", "amount": 20.00, "due_date": "${vencimento}",
              "payer": PAGADOR_BOLETO,
              "fees": {"fine_value": 2.00, "fine_deadline": 1, "fine_type": "FIXED_VALUE",
                       "interest_value": 0.10, "interest_deadline": 1,
                       "interest_type": "VALUE_PER_DAY"}},
     "salvar": {"linha_2": ["digitable_line", "bar_code", "barcode"]}},
    {"id": "B_03", "titulo": "Emissão de boleto com desconto", "metodo": "POST",
     "path": BOLETO + "/", "esperado": 201,
     # Desconto no formato "fees" do BolePix v2; first_discount_deadline = dias antes do
     # vencimento. CONFERIR o enum de discount_type no portal.
     "body": {"external_reference_id": "${ext_b3}", "amount": 30.00, "due_date": "${vencimento}",
              "payer": PAGADOR_BOLETO,
              "fees": {"discount_type": "FIXED_VALUE", "first_discount_value": 3.00,
                       "first_discount_deadline": 5}},
     "salvar": {"linha_3": ["digitable_line", "bar_code", "barcode"]}},
    {"id": "B_04", "titulo": "Alteração de dados do boleto", "metodo": "PUT",
     "path": BOLETO + "/${boleto_id}", "requer": ["boleto_id"], "esperado": 200,
     "body": {"amount": 11.50, "due_date": "${vencimento_alterado}"}},
    {"id": "B_05", "titulo": "Consulta de boleto", "metodo": "GET",
     "path": BOLETO + "/${boleto_id}", "requer": ["boleto_id"], "esperado": 200},
    {"id": "B_06", "titulo": "Geração do PDF do boleto", "metodo": "GET",
     "path": BOLETO + "/${boleto_id}/pdf", "requer": ["boleto_id"], "esperado": 200},
    {"id": "B_07", "titulo": "Cadastro do webhook de boleto", "metodo": "POST",
     "path": WEBHOOK + "/", "esperado": 200,
     "body": {"url": "${webhook_url}", "service": "BANK_SLIP"}},
    {"id": "B_08", "titulo": "Baixa/cancelamento de boleto", "metodo": "PUT",
     "path": BOLETO + "/${boleto_id}/cancel", "requer": ["boleto_id"], "esperado": 200},

    # ---------------------------------------------------- Agendamento de pagamentos
    # Três boletos no grupo: AP_04 remove um pela lista, AP_05 remove outro pelo id
    # e AP_06 envia o grupo com o que sobrou. As linhas vêm de B_01..B_03 ou do
    # config.json (linhas_pagamento), se preenchidas lá.
    {"id": "AP_01", "titulo": "Enviar grupo de pagamentos para decode", "metodo": "POST",
     "path": AP + "/decode", "requer": ["linha_1", "linha_2", "linha_3"], "esperado": 201,
     # CONFERIR: formato do body (lista de itens com amount + content).
     "body": [{"amount": 10.50, "content": "${linha_1}"},
              {"amount": 20.00, "content": "${linha_2}"},
              {"amount": 30.00, "content": "${linha_3}"}],
     "salvar": {"group_id": ["group_id", "id", "data.group_id"]}},
    {"id": "AP_02", "titulo": "Consultar DDA (boletos pendentes)", "metodo": "GET",
     "path": AP + "/query", "esperado": 200},
    {"id": "AP_03", "titulo": "Obter itens do grupo de pagamentos", "metodo": "GET",
     "path": AP + "/${group_id}/items", "requer": ["group_id"], "esperado": 200,
     "salvar": {"item_1": ["0.id", "items.0.id", "data.0.id"],
                "item_2": ["1.id", "items.1.id", "data.1.id"]}},
    {"id": "AP_04", "titulo": "Remover lista de pagamentos do grupo", "metodo": "DELETE",
     "path": AP + "/${group_id}/items", "requer": ["group_id", "item_1"], "esperado": 204,
     "body": [{"id": "${item_1}"}]},
    {"id": "AP_05", "titulo": "Remover pagamento específico do grupo", "metodo": "DELETE",
     "path": AP + "/${group_id}/items/${item_2}", "requer": ["group_id", "item_2"],
     "esperado": 204},
    {"id": "AP_06", "titulo": "Enviar grupo para aprovação", "metodo": "POST",
     "path": AP + "/submit", "requer": ["group_id"], "esperado": 204,
     "body": {"group_id": "${group_id}", "uploader_name": "${responsavel}"}},

    # --------------------------------------------------------------------- Extrato
    {"id": "E_01", "titulo": "Consulta de saldo", "metodo": "GET", "path": EXTRATO + "/balance",
     "esperado": 200},
    {"id": "E_02", "titulo": "Consulta de extrato", "metodo": "GET",
     "path": EXTRATO + "/statement", "esperado": 200,
     "query": {"start_date": "${inicio_extrato}", "end_date": "${hoje}"}},

    # ------------------------------------------------------------------------- Pix
    {"id": "P_01_01", "titulo": "Criar cobrança imediata com txid", "metodo": "PUT",
     "path": PIX + "/cob/${txid_cob}", "esperado": 201,
     "body": {"calendario": {"expiracao": 3600}, "devedor": DEVEDOR_PIX,
              "valor": {"original": "10.00"}, "chave": "${chave_pix}",
              "solicitacaoPagador": "Teste de homologacao ERP"}},
    {"id": "P_01_02", "titulo": "Criar cobrança imediata sem txid", "metodo": "POST",
     "path": PIX + "/cob", "esperado": 201,
     "body": {"calendario": {"expiracao": 3600}, "devedor": DEVEDOR_PIX,
              "valor": {"original": "11.00"}, "chave": "${chave_pix}"},
     "salvar": {"loc_da_cob": ["loc.id"]}},
    {"id": "P_01_03", "titulo": "Revisar cobrança imediata", "metodo": "PATCH",
     "path": PIX + "/cob/${txid_cob}", "esperado": 201,
     "body": {"valor": {"original": "12.00"}, "solicitacaoPagador": "Valor revisado"}},
    {"id": "P_01_04", "titulo": "Consultar cobrança imediata", "metodo": "GET",
     "path": PIX + "/cob/${txid_cob}", "esperado": 200},
    {"id": "P_01_05", "titulo": "Consultar lista de cobranças imediatas", "metodo": "GET",
     "path": PIX + "/cob", "esperado": 200,
     "query": {"inicio": "${inicio_rfc}", "fim": "${fim_rfc}"}},

    {"id": "P_02_01", "titulo": "Criar cobrança com vencimento", "metodo": "PUT",
     "path": PIX + "/cobv/${txid_cobv}", "esperado": 201,
     "body": {k: v for k, v in _cobv("${txid_cobv}", "50.00").items() if k != "txid"}},
    {"id": "P_02_02", "titulo": "Revisar cobrança com vencimento", "metodo": "PATCH",
     "path": PIX + "/cobv/${txid_cobv}", "esperado": 200,
     "body": {"valor": {"original": "55.00"}}},
    {"id": "P_02_03", "titulo": "Consultar cobrança com vencimento", "metodo": "GET",
     "path": PIX + "/cobv/${txid_cobv}", "esperado": 200},
    {"id": "P_02_04", "titulo": "Consultar lista de cobranças com vencimento", "metodo": "GET",
     "path": PIX + "/cobv", "esperado": 200,
     "query": {"inicio": "${inicio_rfc}", "fim": "${fim_rfc}"}},

    {"id": "P_03_01", "titulo": "Criar lote de cobranças com vencimento", "metodo": "PUT",
     "path": PIX + "/lotecobv/${lote_id}", "esperado": 201,
     "body": {"descricao": "Lote homologacao ERP ${ref}",
              "cobsv": [_cobv("${txid_lote_1}", "60.00"), _cobv("${txid_lote_2}", "70.00")]}},
    {"id": "P_03_02", "titulo": "Revisar cobranças dentro do lote", "metodo": "PATCH",
     "path": PIX + "/lotecobv/${lote_id}", "esperado": 200,
     "body": {"cobsv": [{"txid": "${txid_lote_1}", "valor": {"original": "65.00"}}]}},
    {"id": "P_03_03", "titulo": "Consultar lote específico", "metodo": "GET",
     "path": PIX + "/lotecobv/${lote_id}", "esperado": 200},
    {"id": "P_03_04", "titulo": "Consultar lista de lotes", "metodo": "GET",
     "path": PIX + "/lotecobv", "esperado": 200,
     "query": {"inicio": "${inicio_rfc}", "fim": "${fim_rfc}"}},

    {"id": "P_04_01", "titulo": "Criar location do payload", "metodo": "POST",
     "path": PIX + "/loc", "esperado": 201, "body": {"tipoCob": "cob"},
     "salvar": {"loc_nova": ["id"]}},
    {"id": "P_04_02", "titulo": "Consultar locations cadastradas", "metodo": "GET",
     "path": PIX + "/loc", "esperado": 200,
     "query": {"inicio": "${inicio_rfc}", "fim": "${fim_rfc}"}},
    {"id": "P_04_03", "titulo": "Recuperar location específica", "metodo": "GET",
     "path": PIX + "/loc/${loc_nova}", "requer": ["loc_nova"], "esperado": 200},
    # Desvincula a location criada junto com a cobrança de P_01_02.
    {"id": "P_04_04", "titulo": "Desvincular cobrança de uma location", "metodo": "DELETE",
     "path": PIX + "/loc/${loc_da_cob}/txid", "requer": ["loc_da_cob"], "esperado": 200},

    # Lista primeiro para descobrir um e2eid; se não houver Pix recebido no período,
    # informe "e2eid" no config.json (faça um Pix para uma cobrança criada acima).
    {"id": "P_05_02", "titulo": "Consultar lista de Pix recebidos", "metodo": "GET",
     "path": PIX + "/pix", "esperado": 200,
     "query": {"inicio": "${inicio_rfc}", "fim": "${fim_rfc}"},
     "salvar": {"e2eid": ["pix.0.endToEndId"]}},
    {"id": "P_05_01", "titulo": "Consultar Pix recebido específico", "metodo": "GET",
     "path": PIX + "/pix/${e2eid}", "requer": ["e2eid"], "esperado": 200},
    {"id": "P_05_03", "titulo": "Solicitar devolução", "metodo": "PUT",
     "path": PIX + "/pix/${e2eid}/devolucao/${devolucao_id}", "requer": ["e2eid"],
     "esperado": 201, "body": {"valor": "0.01"}},
    {"id": "P_05_04", "titulo": "Consultar devolução", "metodo": "GET",
     "path": PIX + "/pix/${e2eid}/devolucao/${devolucao_id}", "requer": ["e2eid"],
     "esperado": 200},

    {"id": "P_06_01", "titulo": "Cadastrar webhook Pix", "metodo": "PUT",
     "path": PIX + "/webhook/${chave_pix}", "esperado": 200,
     "body": {"webhookUrl": "${webhook_url}"}},
    {"id": "P_06_02", "titulo": "Consultar webhook Pix", "metodo": "GET",
     "path": PIX + "/webhook/${chave_pix}", "esperado": 200},
    {"id": "P_06_03", "titulo": "Excluir webhook Pix", "metodo": "DELETE",
     "path": PIX + "/webhook/${chave_pix}", "esperado": 204},

    # --------------------------------------------------------------------- BolePix
    # Formato conferido na biblioteca femitz/c6bank-php: external_reference_id com 26
    # caracteres A-Z0-9, endereço com address/neighborhood e billing_scheme "21" no
    # sandbox ("15" em produção).
    {"id": "BP_01_01", "titulo": "Criar cobrança BolePix", "metodo": "POST", "path": BOLEPIX,
     "esperado": 201,
     "body": {"amount": 40.00, "due_date": "${vencimento}", "description": "Teste homologacao ERP",
              "payer": PAGADOR_BOLEPIX, "payment_method": PAGAMENTO_BOLEPIX}},
    {"id": "BP_01_02", "titulo": "Criar cobrança BolePix com external_reference_id duplicado",
     "metodo": "POST", "path": BOLEPIX, "esperado": 201, "repetir": 2,
     "body": {"external_reference_id": "${ext_bolepix}", "amount": 45.00,
              "due_date": "${vencimento}", "description": "Teste duplicidade",
              "payer": PAGADOR_BOLEPIX, "payment_method": PAGAMENTO_BOLEPIX}},
    {"id": "BP_02", "titulo": "Atualizar cobrança BolePix", "metodo": "PATCH",
     "path": BOLEPIX + "/${ext_bolepix}", "esperado": 200,
     "body": {"amount": 47.00, "due_date": "${vencimento_alterado}",
              "description": "Valor atualizado"}},
    {"id": "BP_03", "titulo": "Consultar cobrança BolePix", "metodo": "GET",
     "path": BOLEPIX + "/${ext_bolepix}", "esperado": 200},
    {"id": "BP_04", "titulo": "Consultar PDF da cobrança BolePix", "metodo": "GET",
     "path": BOLEPIX + "/${ext_bolepix}/pdf", "esperado": 200},
    {"id": "BP_05", "titulo": "Cancelar cobrança BolePix (baixa)", "metodo": "PUT",
     "path": BOLEPIX + "/${ext_bolepix}/cancel", "esperado": 204},
    # CONFERIR: nomes dos parâmetros do intervalo de vencimento.
    {"id": "BP_06", "titulo": "Listar cobranças BolePix", "metodo": "GET",
     "path": BOLEPIX + "/list", "esperado": 200,
     "query": {"due_date_start": "${hoje}", "due_date_end": "${vencimento}"}},
]
