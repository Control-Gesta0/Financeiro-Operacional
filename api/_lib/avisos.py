"""Avisos do próprio Asaas aos clientes (e-mail, SMS, WhatsApp, robô de voz).

O financeiro envia as cobranças pela integração (WhatsApp da Zaptos); os avisos do
Asaas duplicariam as mensagens e cobram a taxa de mensageria. Os clientes criados pela
emissão já nascem com notificationDisabled; aqui ficam os antigos.
"""

LOTE = 60  # por chamada, para caber no tempo da função; a página manda recarregar


def desligar(asaas, aplicar=False, lote=LOTE):
    clientes = asaas.listar_clientes()
    ligados = [c for c in clientes if not c.get("notificationDisabled")]
    base = {"clientes": len(clientes), "com_avisos_ligados": len(ligados), "aplicado": aplicar}
    if not aplicar:
        return {**base, "exemplos": sorted(c.get("name") or "" for c in ligados)[:30]}
    desligados, erros = [], []
    for cliente in ligados[:lote]:
        try:
            asaas.atualizar_cliente(cliente["id"], {"notificationDisabled": True})
            desligados.append(cliente["id"])
        except asaas.ErroAsaas as e:  # um cliente com problema não trava os demais
            erros.append({"id": cliente["id"], "nome": cliente.get("name"), "erro": str(e)})
    return {**base, "desligados_agora": len(desligados), "ids_desligados": desligados,
            "erros": erros, "faltam": len(ligados) - len(desligados)}
