---
name: ordem-de-servico
description: Cria ordens de serviço no VHSYS a partir de propostas e orçamentos (licenças e suporte Kommo, implantação, consultoria etc.) e define como fica o contas a receber de OS e pedidos de venda. O financeiro sai sempre do "Lançar Contas" do próprio documento, nunca de uma receita avulsa. Use quando o usuário pedir para criar uma OS, lançar uma venda, ou lançar, cadastrar ou gerar contas a receber de uma OS ou de um pedido.
---

# Ordem de serviço e contas a receber no VHSYS

## Regra principal

Venda com OS ou pedido gera o contas a receber **pelo próprio documento**: menu ▾ da OS (ou do
pedido) > **Lançar Contas**. Nunca lance essas receitas como avulsas via `POST /contas-receber`.

A receita lançada pela OS fica vinculada a ela (`identificacao = OS_<id_ordem>`, OS com
`contas_pedido = 1` e ícone **$** na listagem). A avulsa não tem esse vínculo. A OS continua
aparecendo sem financeiro, e quem clicar em "Lançar Contas" duplica a cobrança.

A API pública do VHSYS não tem endpoint para "Lançar Contas". O fluxo é este:
o script cria a OS com as parcelas, o usuário clica em Lançar Contas e o script confere o resultado.

Script: `scripts/criar_ordem_servico.py` (usa as credenciais `VHSYS_ACCESS_TOKEN` e `VHSYS_SECRET_ACCESS_TOKEN`).

## Passo a passo

1. **Leia a proposta** e extraia: cliente, itens (PN, descrição, quantidade, valor unitário),
   total, condição (à vista ou parcelado), forma de pagamento, conta bancária e categoria.
   Ignore colunas informativas como "valor proporcional mês".
2. **Mapeie cada item para um serviço cadastrado** (tabela abaixo). Quando a proposta tiver
   período ou detalhe que o nome do cadastro não tem, mantenha o `id_servico` e complete a
   descrição. Exemplo: SUP6 vira `SUPORTE PREMIUM KOMMO 6 MESES (ATENDIMENTO PREFERENCIAL)`.
3. **Monte o `pedido.json`** no scratchpad (formato no docstring do script):
   ```json
   {
     "cliente": 43294181,
     "data": "2026-09-30",
     "servicos": [
       {"id_servico": 85329994, "descricao": "KOMMO CRM AVANÇADO 6 MESES", "quantidade": 1, "valor_unitario": "782.94"},
       {"id_servico": 85317855, "descricao": "SUPORTE PREMIUM KOMMO 6 MESES (ATENDIMENTO PREFERENCIAL)", "quantidade": 1, "valor_unitario": "1200.00"}
     ],
     "forma_pagamento": "PIX",
     "conta_bancaria": "C6",
     "categoria": "10.01.03"
   }
   ```
   Parcelado: `"parcelas": ["2026-10-10", "2026-11-10"]`. O total é dividido igualmente e a
   diferença de centavos vai para a última parcela.
4. **Rode a simulação**: `python3 scripts/criar_ordem_servico.py pedido.json`. Ela resolve
   cliente, conta e categoria, soma o total e bloqueia OS repetida (mesmo cliente, data e total).
   Confira se o total bate com a proposta.
5. **Grave**: `python3 scripts/criar_ordem_servico.py pedido.json --executar`. Isso cria a OS, os
   serviços e as parcelas (data, valor e forma de pagamento). **Não cria conta a receber.**
   Se o usuário não informou cliente, valores, pagamento, conta e categoria, mostre a prévia e
   confirme antes de gravar.
6. **Peça ao usuário para lançar o financeiro pela OS**: Ordens de serviço > OS N > ▾ >
   **Lançar Contas**, na conta e na categoria pedidas.
7. **Confira**: `python3 scripts/criar_ordem_servico.py --conferir N`. O comando verifica se o
   financeiro saiu da OS, se a soma das receitas bate com o total da OS, e mostra conta,
   categoria e forma de cada parcela. Ele também acusa receita avulsa que cita a OS sem estar
   vinculada. Compare a conta e a categoria com o que o usuário pediu. A baixa (liquidação)
   só acontece quando o pagamento entrar.

## Receita avulsa já lançada para uma OS

Se o `--conferir` acusar uma receita avulsa, ela duplica a cobrança da OS. Com o ok do usuário,
exclua a avulsa (`DELETE /contas-receber/{id}`, que vai para a lixeira) e lance pela OS. Se a
avulsa já estiver liquidada, avise antes de excluir, porque a baixa terá de ser refeita na
receita da OS.

## Pedidos de venda

Vale a mesma regra: o financeiro do pedido (Vendas > Pedidos) sai do **Lançar Contas** do
pedido. O script não cria pedidos. Se um dia criar pedido via API, cadastre pedido, produtos e
parcelas e pare antes do financeiro.

## Serviços cadastrados

Não há endpoint de catálogo de serviços. Os IDs abaixo vêm de OS anteriores. Para outro serviço,
procure em `GET /ordens-servico/{id_ordem}/servicos` de uma OS parecida. Se não achar, pergunte
ao usuário. Não chute IDs.

| PN | Serviço no VHSYS | id_servico |
|---|---|---|
| KA6 | KOMMO CRM AVANÇADO 6 MESES | 85329994 |
| | KOMMO CRM AVANÇADO 12 MESES | 85316743 |
| | KOMMO CRM UPGRADE AVANÇADO | 85335384 |
| SUP6 | SUPORTE PREMIUM KOMMO (descrição com o período) | 85317855 |
| | TAXAS FINANCEIRAS | 85329997 |
| | IMPLANTAÇÃO DE SISTEMAS | 31758275 |
| | CONSULTORIA | 18054094 |
| | CRM VERTISELL PRO | 85037179 |
| | CRM VERTISELL START | 85037174 |
| | Control ERP Lite Essencial | 85037145 |

## Contas e categorias usuais

O script resolve pelo nome da conta e pelo código da categoria. Os IDs ficam aqui só para referência.

| Uso | Valor no pedido.json | Nome no VHSYS | id |
|---|---|---|---|
| Conta PJ para Pix e transferência | `"conta_bancaria": "C6"` | C6 | 1060867 |
| Venda Kommo (licença e suporte) | `"categoria": "10.01.03"` | 10.01.03 - Venda de sistemas KOMMO | 9704937 |
| Implantação Kommo | `"categoria": "10.02.02"` | 10.02.02 - Implantação Sistemas KOMMO | 9649018 |
