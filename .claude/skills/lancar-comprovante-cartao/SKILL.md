---
name: lancar-comprovante-cartao
description: Lança comprovantes de compra no cartão de crédito (licenças Kommo, softwares, assinaturas etc.) como contas a pagar no VHSYS, na conta do cartão certo e com o vencimento da fatura calculado pelo dia de corte. Use quando o usuário mandar um comprovante, recibo ou pedido pago no cartão e pedir para lançar, registrar ou cadastrar a despesa.
---

# Lançar comprovante de cartão de crédito no VHSYS

Cada compra no cartão vira uma conta a pagar **em aberto** na conta bancária do cartão.
O vencimento é o da fatura em que a compra cai. A baixa acontece depois, quando a fatura for paga.

Script: `scripts/lancar_cartao.py` (usa as credenciais `VHSYS_ACCESS_TOKEN` e `VHSYS_SECRET_ACCESS_TOKEN`).

## Cartões

| Final | Conta no VHSYS | id_banco | Corte | Vencimento |
|---|---|---|---|---|
| 6173 / 4181 | Cartão C6 - 6173/4181 (15) | 1321191 | dia 09 | dia 15 |
| 8473 | Cartão C6 - 8473 (10) | 1321190 | dia 04 | dia 10 |

Regra da fatura: compra **antes** do dia de corte cai no vencimento do mesmo mês. Compra **no dia de
corte ou depois** cai no vencimento do mês seguinte.
Exemplo: compra em 18/09 no 6173 (corte 09) vence em 15/10.

Nunca use a conta genérica "Cartão C6" (1320973). Ela é da época anterior à separação dos cartões.

## Categorias e fornecedores conhecidos

| Uso | Categoria (plano de contas) | id_categoria | Fornecedor | id_fornecedor |
|---|---|---|---|---|
| Licença Kommo | 30.01.03 - Licenças KOMMO | 9653113 | KOMMO CRM | 43294242 |
| Software em geral | 50.01.02 - Despesas com software | 9704960 | (conforme o caso) | |

Para outro fornecedor ou categoria, ache os IDs no histórico:
`python3 scripts/lancar_cartao.py --buscar "<texto>"`. Se não achar, pergunte ao usuário. Não chute IDs.

## Passo a passo

1. **Leia o comprovante** e extraia: fornecedor, valor cobrado (o total efetivamente pago, com
   desconto de parceiro se houver), data da compra, final do cartão, nº do pedido e dados da
   licença (plano, período, usuários, expiração, ID da conta do cliente).
2. **Confirme com o usuário o que não estiver no comprovante.** O cartão e o cliente final
   normalmente vêm na mensagem. A data da compra define a fatura. Se ela não aparecer no
   comprovante, pergunte e não presuma. A expiração da licença não é a data da compra.
3. **Monte a observação** neste padrão, separado por ` | `, sem acentos para manter o que já existe:

   `Compra em DD/MM/AAAA | Cliente - <Nome do cliente> | ID cliente <id> | Pedido #<nº> | Plano <plano>, <n> meses, <n> usuario(s), expira DD/MM/AAAA | Cartao final <nnnn> | pago via <gateway>`

   Nome da conta (até 45 caracteres), no padrão existente: `KOMMO CRM - Licenças KOMMO`.
4. **Rode a prévia** (sem `--confirmar`):
   ```bash
   python3 scripts/lancar_cartao.py --cartao 6173 --compra 2026-09-30 --valor 651.58 \
     --categoria-id 9653113 --fornecedor-id 43294242 \
     --nome "KOMMO CRM - Licenças KOMMO" --pedido 5019195 \
     --obs "Compra em 30/09/2026 | Cliente - Beauty Pro | ID cliente 32087275 | Pedido #5019195 | Plano Avancado, 12 meses, 1 usuario, expira 09/10/2027 | Cartao final 6173 | pago via Bamboo"
   ```
   O script calcula o vencimento e procura duplicidades: mesmo nº de pedido na observação, ou
   mesmo fornecedor, valor e data de emissão nas 1.000 despesas mais recentes.
5. **Mostre a prévia ao usuário numa tabela curta** (cartão, vencimento, valor, categoria,
   fornecedor, observação) e **peça confirmação antes de gravar**. Lançar no ERP é uma escrita
   real. Se houver vários comprovantes, faça a prévia de todos e peça uma confirmação única.
6. Com o ok, rode o mesmo comando com `--confirmar` e informe o `id_conta_pag` criado.
   Se aparecer aviso de duplicidade, não use `--ignorar-duplicidade` sem o usuário confirmar
   que é outra compra.

## Cuidados

- Valor em reais com ponto decimal (`651.58`). Comprovante em dólar: use o valor em reais da
  fatura (com IOF) e registre o valor em dólar na observação.
- Compra parcelada: um lançamento por parcela, cada um com o vencimento da sua fatura
  (use `--vencimento`) e `(Parcela X de N)` na observação.
- `--vencimento` só serve para exceções (parcelas, fatura com data diferente). No caso normal,
  deixe o script calcular.
