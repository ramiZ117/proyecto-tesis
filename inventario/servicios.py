"""Lógica de inventario compartida por mesas, cocina e inventario."""

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from inventario.models import Insumo, MovimientoStock, Receta


def comprometido_por_insumo() -> dict[int, Decimal]:
    """Insumos ya "reservados" por platillos pedidos que aún no se descontaron."""
    from mesas.models import DetallePedido, Pedido

    por_producto = {
        fila["producto_id"]: fila["total"]
        for fila in DetallePedido.objects.filter(insumos_descontados=False)
        .exclude(estado=DetallePedido.Estado.ANULADO)
        .exclude(pedido__estado__in=[Pedido.Estado.CERRADO, Pedido.Estado.ANULADO])
        .values("producto_id")
        .annotate(total=Sum("cantidad"))
    }
    comprometido: dict[int, Decimal] = defaultdict(Decimal)
    for receta in Receta.objects.filter(producto_id__in=por_producto):
        comprometido[receta.insumo_id] += receta.cantidad * por_producto[receta.producto_id]
    return comprometido


def estado_insumos(insumos=None) -> list:
    """Insumos con `comprometido`, `disponible` (stock - comprometido) y `critico` (disponible <= mínimo).

    El stock real solo baja al cobrar, pero lo ya pedido está reservado: la alerta de nivel crítico
    usa lo disponible para avisar a tiempo, antes de que se agote por pedidos aún sin cobrar.
    """
    comprometido = comprometido_por_insumo()
    lista = list(Insumo.objects.filter(activo=True) if insumos is None else insumos)
    for insumo in lista:
        insumo.comprometido = comprometido.get(insumo.pk, Decimal("0"))
        insumo.disponible = insumo.stock_actual - insumo.comprometido
        insumo.critico = insumo.disponible <= insumo.stock_minimo
    return lista


def nombres_insumos_criticos() -> list[str]:
    return [i.nombre for i in estado_insumos() if i.critico]


def disponibilidad_menu() -> dict[int, int]:
    """Porciones que aún se pueden pedir por producto (descontando lo ya comprometido).

    Los productos sin receta no aparecen en el resultado: se consideran ilimitados.
    """
    comprometido = comprometido_por_insumo()
    stock = {i.pk: i.stock_actual for i in Insumo.objects.all()}
    porciones: dict[int, int] = {}
    for receta in Receta.objects.all():
        libre = stock.get(receta.insumo_id, Decimal("0")) - comprometido.get(receta.insumo_id, Decimal("0"))
        alcanza = int(max(libre, Decimal("0")) // receta.cantidad)
        actual = porciones.get(receta.producto_id)
        porciones[receta.producto_id] = alcanza if actual is None else min(actual, alcanza)
    return porciones


@transaction.atomic
def registrar_entrada(insumo_id: int, cantidad: Decimal, nota: str = "") -> MovimientoStock:
    """Reposición de stock con su movimiento de auditoría."""
    insumo = Insumo.objects.select_for_update().get(pk=insumo_id)
    insumo.stock_actual += cantidad
    insumo.save(update_fields=["stock_actual"])
    return MovimientoStock.objects.create(
        insumo=insumo,
        tipo=MovimientoStock.Tipo.ENTRADA,
        cantidad=cantidad,
        stock_resultante=insumo.stock_actual,
        nota=nota[:200] or "Reposición de inventario",
    )
