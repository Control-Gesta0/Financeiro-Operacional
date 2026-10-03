# Homologação C6 Developers: Control ERP Lite

Ferramentas para passar pelo "Roteiro de Testes - C6 Developers v3.0" no sandbox
do C6 e devolver o roteiro preenchido para `homologacaoapi@c6bank.com`.

APIs no escopo (definidas em `config.json` → `apis`):

| Checkbox do roteiro       | Testes                 | Uso no ERP                         |
|---------------------------|------------------------|------------------------------------|
| Autenticação (obrigatória)| AT_01                  | token OAuth2 + mTLS                |
| Agendamento de Pagamentos | AP_01 a AP_06          | contas a pagar, DDA                |
| Boleto                    | B_01 a B_08            | contas a receber                   |
| Extrato                   | E_01, E_02             | saldo e conciliação                |
| Pix                       | P_01 a P_06 (24 casos) | cobranças Pix e webhooks           |
| BolePix                   | BP_01 a BP_06          | boleto com QR Code Pix             |

Checkout, Transações/Recebíveis (C6 Pay) e Pix Automático ficam desmarcados.

## Arquivos

- `homologacao.py`: executa os testes (`testar`), resume (`status`) e gera o .docx preenchido (`preencher`).
- `casos.py`: os 48 casos de teste (endpoint, payload, status esperado). Ajustes de payload são feitos aqui.
- `config.exemplo.json`: modelo do `config.json` (empresa, chave Pix, pagador de teste, webhook).
- `env.exemplo`: modelo do `.env` (client id/secret e caminho do certificado).
- `roteiro/`: o roteiro original, sem preenchimento.
- `evidencias/`: resultados, PDFs e o roteiro preenchido (gerado; não vai para o git).

Só usa a biblioteca padrão do Python 3.9+; não precisa instalar nada.

## Passo a passo

### 1. Pré-requisitos

- **Certificado do sandbox (.crt e .key).** O C6 exige mTLS em todas as chamadas,
  inclusive no `/auth`. Sem ele a autenticação nem completa o handshake TLS.
  O e-mail com as credenciais traz só ClientId, ClientSecret e chave Pix. Veja se
  veio algum anexo ou link à parte; se não veio, peça a `homologacaoapi@c6bank.com`
  (modelo de e-mail no final).
- **URL de webhook para teste.** Os testes B_07 e P_06 cadastram webhooks. Uma URL
  de https://webhook.site serve.
- **Rodar fora de proxy que intercepte TLS.** mTLS não passa por proxy que abre o
  TLS. Rode da sua máquina ou de um servidor com saída direta para
  `baas-api-sandbox.c6bank.info`. O sandbox funciona de segunda a sexta, das 7h às 23h.

### 2. Configurar

```bash
cd scripts/c6
cp config.exemplo.json config.json   # preencha empresa, chave_pix, webhook_url, pagador
cp env.exemplo .env                  # preencha client id/secret e caminhos do .crt/.key
```

`chave_pix_tipo` é o tipo da chave recebida no e-mail (`EVP` para chave aleatória;
`CPF`, `CNPJ`, `EMAIL` ou `PHONE` nos demais casos). `nome_software` e
`versao_software` vão nos headers `partner-software-name` e
`partner-software-version` de todas as chamadas: é assim que o C6 identifica o
ERP homologado.

### 3. Primeiro teste: só autenticação

```bash
python3 scripts/c6/homologacao.py testar --apis auth
```

O resultado mostra os **escopos** liberados. Compare com os que o roteiro exige:

- Agendamento: `schedulepayments.read`, `schedulepayments.write`
- Boleto: `bankslip.read`, `bankslip.write`
- Extrato: `statement.read`
- Pix: `pix.*`, `cob.*`, `cobv.*`, `lotecobv.*`, `payloadlocation.*` (read e write)
- BolePix: `bankslip_pix.read`, `bankslip_pix.write`

Se faltar algum, peça a liberação ao C6 antes de seguir; caso contrário, a API
correspondente vai dar 403.

### 4. Rodar o roteiro

```bash
python3 scripts/c6/homologacao.py testar
```

Cada teste sai como **OK** (status igual ao esperado), **DIVERGENTE** (outro
status) ou **PENDENTE** (depende de um dado que ainda não existe). A ordem de
execução encadeia os dados: os boletos de B_01 a B_03 viram o grupo de pagamentos
do AP_01, a location criada com a cobrança de P_01_02 é desvinculada em P_04_04 etc.

Para os DIVERGENTES, abra `evidencias/resultados.json`, leia a mensagem de
validação do C6, ajuste o payload em `casos.py` e repita só aquele teste. Os
ids criados na rodada anterior são reaproveitados:

```bash
python3 scripts/c6/homologacao.py testar --so B_02,B_03
python3 scripts/c6/homologacao.py status
```

**O que provavelmente vai precisar de ajuste na 1ª rodada.** Os pontos
marcados com `CONFERIR` em `casos.py` não estão detalhados no roteiro:

- Boleto v1 (B_01 a B_03): o formato do pagador e de multa/juros/desconto segue o
  do BolePix v2, que foi conferido em uma biblioteca de integração pública
  (femitz/c6bank-php). A API v1 pode usar outros nomes.
- AP_01: se o body do decode é uma lista de `{amount, content}`.
- BP_06: nomes dos parâmetros do intervalo de datas.

**Casos que dependem de ação manual:**

- P_05 (Pix recebidos): precisa de um Pix recebido no período. Se a lista vier
  vazia, pague uma das cobranças Pix criadas (QR Code / copia e cola) e informe
  o `endToEndId` em `config.json` → `e2eid`. Depois rode `--so P_05_01,P_05_03,P_05_04`.
- AP_01: se o sandbox não aceitar as linhas digitáveis dos boletos recém-emitidos,
  informe 3 linhas válidas em `config.json` → `linhas_pagamento`.

### 5. Gerar o roteiro preenchido e enviar

```bash
python3 scripts/c6/homologacao.py preencher
```

Gera `evidencias/Roteiro_C6_preenchido.docx` com os dados da empresa, os
checkboxes das APIs do `config.json` e, para cada teste executado, o status code e
o response body (o `access_token` vai mascarado; PDFs aparecem como "PDF recebido").
Revise no Word e envie para `homologacaoapi@c6bank.com`.

Depois da homologação, a liberação em produção exige conta PJ C6 aberta no mesmo
CNPJ cadastrado no Portal do Desenvolvedor (conta MEI não vale). Em produção a URL
base é `https://baas-api.c6bank.info` e o `billing_scheme` do BolePix passa a ser `15`.

## Modelo de e-mail: solicitar o certificado do sandbox

> **Para:** homologacaoapi@c6bank.com
> **Assunto:** Certificado do sandbox: Control ERP Lite
>
> Olá,
>
> Recebemos as credenciais do sandbox (ClientId, ClientSecret e chave Pix) para a
> integração do Control ERP Lite. O roteiro de testes v3.0 pede o certificado
> (.crt e .key) para autenticar no /auth via mTLS, mas ele não veio no e-mail.
> Podem nos enviar o certificado do sandbox, ou nos dizer onde baixá-lo no portal?
>
> Vamos homologar Autenticação, Agendamento de Pagamentos, Boleto, Extrato, Pix e
> BolePix. Podem confirmar também se os escopos dessas APIs estão liberados para
> o nosso client_id?
>
> CNPJ: ___ · Responsável: ___ · Telefone: ___
>
> Obrigado!
