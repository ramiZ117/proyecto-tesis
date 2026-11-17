"""Lógica de inventario compartida por mesas, cocina, caja e inventario."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone

from config.formato import num
from inventario.models import Insumo, MovimientoStock, Producto, Receta

CENTAVO = Decimal("0.01")


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


def _porcentaje(valor: Decimal, total: Decimal) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, int(round(valor / total * 100))))


def estado_insumos(insumos=None) -> list:
    """Insumos con su situación real de almacén.

    Cada insumo trae:
    - `comprometido`: lo reservado por comandas abiertas; `disponible` = stock - comprometido.
    - `critico`: disponible <= stock mínimo (RF-04: alerta visual).
    - `reponer`: disponible <= punto de reposición (entra a la lista de compra), sin ser crítico.
    - `nivel`: "critico", "reponer" u "ok"; y porcentajes para dibujar la barra (stock, mínimo y reposición
      respecto del máximo).
    """
    comprometido = comprometido_por_insumo()
    if insumos is None:
        insumos = Insumo.objects.filter(activo=True).select_related("proveedor")
    lista = list(insumos)
    for insumo in lista:
        insumo.comprometido = comprometido.get(insumo.pk, Decimal("0"))
        insumo.disponible = insumo.stock_actual - insumo.comprometido
        insumo.critico = insumo.disponible <= insumo.stock_minimo
        insumo.reponer = not insumo.critico and insumo.disponible <= insumo.punto_reposicion
        insumo.nivel = "critico" if insumo.critico else "reponer" if insumo.reponer else "ok"
        tope = max(insumo.stock_maximo, insumo.stock_actual, insumo.punto_reposicion, Decimal("0.001"))
        insumo.pct_stock = _porcentaje(insumo.stock_actual, tope)
        insumo.pct_minimo = _porcentaje(insumo.stock_minimo, tope)
        insumo.pct_reposicion = _porcentaje(insumo.punto_reposicion, tope)
        insumo.valor_usd = (insumo.stock_actual * insumo.costo_unitario).quantize(CENTAVO)
        insumo.sugerido_compra = max(insumo.stock_maximo - insumo.stock_actual, Decimal("0"))
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


def lista_compra(insumos=None) -> list[dict]:
    """Insumos en nivel crítico o de reposición, con lo que falta para llegar al stock máximo."""
    filas = []
    for i in insumos if insumos is not None else estado_insumos():
        if i.nivel == "ok":
            continue
        objetivo = i.stock_maximo if i.stock_maximo > i.stock_actual else i.punto_reposicion * 2
        cantidad = max(objetivo - i.stock_actual, Decimal("0"))
        if cantidad <= 0:
            continue
        filas.append(
            {
                "insumo": i,
                "cantidad": cantidad,
                "costo_usd": (cantidad * i.costo_unitario).quantize(CENTAVO),
                "proveedor": i.proveedor.nombre if i.proveedor else "Sin proveedor",
            }
        )
    filas.sort(key=lambda f: (f["proveedor"], f["insumo"].nombre))
    return filas


def costos_de_platos(productos=None) -> list[dict]:
    """Costo de receta, precio y margen bruto de cada plato disponible."""
    if productos is None:
        productos = Producto.objects.filter(disponible=True).prefetch_related("recetas__insumo")
    filas = []
    for p in productos:
        recetas = list(p.recetas.all())
        if not recetas:
            continue
        costo = sum((r.cantidad * r.insumo.costo_unitario for r in recetas), Decimal("0")).quantize(CENTAVO)
        margen = int(round((p.precio_usd - costo) / p.precio_usd * 100)) if p.precio_usd > 0 else 0
        filas.append({"producto": p, "costo_usd": costo, "precio_usd": p.precio_usd, "margen": margen})
    filas.sort(key=lambda f: f["margen"])
    return filas


def valor_merma_del_mes() -> Decimal:
    hoy = timezone.localdate()
    inicio = timezone.make_aware(datetime(hoy.year, hoy.month, 1))
    valor = (
        MovimientoStock.objects.filter(tipo=MovimientoStock.Tipo.MERMA, creado_en__gte=inicio)
        .aggregate(
            total=Sum(ExpressionWrapper(F("cantidad") * F("costo_unitario"), output_field=DecimalField(max_digits=18, decimal_places=4)))
        )["total"]
    )
    return (valor or Decimal("0")).quantize(CENTAVO)


# ---- movimientos manuales ---------------------------------------------------------------
def _validar_cantidad(cantidad: Decimal, permitir_cero: bool = False) -> None:
    if cantidad is None or not cantidad.is_finite() or cantidad < 0 or (cantidad == 0 and not permitir_cero):
        raise ValidationError("Indica una cantidad mayor a cero.")
    if cantidad >= Decimal("1000000"):
        raise ValidationError("La cantidad es demasiado grande.")


@transaction.atomic
def registrar_entrada(insumo_id: int, cantidad: Decimal, nota: str = "", usuario=None, costo_unitario=None) -> MovimientoStock:
    """Reposición de stock (compra) con su movimiento de auditoría. Puede actualizar el costo del insumo."""
    _validar_cantidad(cantidad)
    insumo = Insumo.objects.select_for_update().get(pk=insumo_id)
    insumo.stock_actual += cantidad
    campos = ["stock_actual"]
    if costo_unitario is not None and costo_unitario >= 0:
        insumo.costo_unitario = costo_unitario
        campos.append("costo_unitario")
    insumo.save(update_fields=campos)
    return MovimientoStock.objects.create(
        insumo=insumo,
        tipo=MovimientoStock.Tipo.ENTRADA,
        cantidad=cantidad,
        stock_resultante=insumo.stock_actual,
        costo_unitario=insumo.costo_unitario,
        usuario=usuario,
        nota=nota[:200] or (f"Compra a {insumo.proveedor}" if insumo.proveedor else "Reposición de inventario"),
    )


@transaction.atomic
def registrar_merma(insumo_id: int, cantidad: Decimal, motivo: str, nota: str = "", usuario=None) -> MovimientoStock:
    """Pérdida de insumo (vencido, dañado, error de preparación…): baja el stock y queda valorizada."""
    _validar_cantidad(cantidad)
    if motivo not in MovimientoStock.Motivo.values or motivo in (MovimientoStock.Motivo.CONTEO,):
        raise ValidationError("Elige el motivo de la merma.")
    insumo = Insumo.objects.select_for_update().get(pk=insumo_id)
    if cantidad > insumo.stock_actual:
        raise ValidationError(
            f"La merma ({num(cantidad)} {insumo.abreviatura}) supera el stock de {insumo.nombre} "
            f"({num(insumo.stock_actual)} {insumo.abreviatura})."
        )
    insumo.stock_actual -= cantidad
    insumo.save(update_fields=["stock_actual"])
    return MovimientoStock.objects.create(
        insumo=insumo,
        tipo=MovimientoStock.Tipo.MERMA,
        motivo=motivo,
        cantidad=cantidad,
        stock_resultante=insumo.stock_actual,
        costo_unitario=insumo.costo_unitario,
        usuario=usuario,
        nota=(nota or f"Merma: {MovimientoStock.Motivo(motivo).label.lower()}")[:200],
    )


@transaction.atomic
def registrar_ajuste(insumo_id: int, contado: Decimal, nota: str = "", usuario=None) -> MovimientoStock:
    """Conteo físico: el stock pasa a ser lo contado y queda el antes y el después."""
    _validar_cantidad(contado, permitir_cero=True)
    insumo = Insumo.objects.select_for_update().get(pk=insumo_id)
    anterior = insumo.stock_actual
    if contado == anterior:
        raise ValidationError(f"El conteo coincide con el stock registrado de {insumo.nombre}: no hay nada que ajustar.")
    insumo.stock_actual = contado
    insumo.save(update_fields=["stock_actual"])
    autor = getattr(usuario, "username", "sistema")
    return MovimientoStock.objects.create(
        insumo=insumo,
        tipo=MovimientoStock.Tipo.AJUSTE,
        motivo=MovimientoStock.Motivo.CONTEO,
        cantidad=abs(contado - anterior),
        stock_resultante=contado,
        costo_unitario=insumo.costo_unitario,
        usuario=usuario,
        nota=(nota or f"Conteo de {autor}: {anterior.normalize():f} → {contado.normalize():f}")[:200],
    )
