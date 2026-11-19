"""Tiempo estimado de espera según la cola de la cocina.

Cada plato tiene su tiempo de preparación (Producto.minutos_preparacion). La cocina trabaja varios
platos a la vez (COCINA_PLATOS_SIMULTANEOS). La espera de un pedido es:

    (minutos de cocina que quedan en los pedidos que llegaron antes) ÷ platos simultáneos
    + el plato más lento del propio pedido

Es una estimación para orientar al cliente, no un compromiso.
"""

import math

from django.conf import settings
from django.utils import timezone

from mesas.models import DetallePedido

PENDIENTES = (DetallePedido.Estado.PENDIENTE, DetallePedido.Estado.EN_PREPARACION)


def _minutos_plato(detalle) -> float:
    """Minutos de trabajo de una línea: cada unidad extra suma un 25 % (se cocinan juntas)."""
    base = detalle.producto.minutos_preparacion or 10
    return base * (1 + 0.25 * (detalle.cantidad - 1))


def _restante(detalle, ahora) -> float:
    total = _minutos_plato(detalle)
    if detalle.estado == DetallePedido.Estado.EN_PREPARACION and detalle.preparacion_en:
        transcurrido = (ahora - detalle.preparacion_en).total_seconds() / 60
        return max(total - transcurrido, 1)
    return total


def _paralelo() -> int:
    return max(int(getattr(settings, "COCINA_PLATOS_SIMULTANEOS", 3)), 1)


def _area(detalle) -> str:
    return "SHA" if detalle.producto.categoria.estacion == "SHA" else "COC"


def _en_cola(antes_de=None, excluir_pedido=None, area: str = "COC"):
    """Cola de un área: la cocina y el shawarmero trabajan en paralelo, cada uno con su fila."""
    from cocina.models import filtro_area

    platos = (DetallePedido.objects.filter(enviado=True, estado__in=PENDIENTES).filter(filtro_area(area))
              .select_related("producto__categoria"))
    if antes_de is not None:
        platos = platos.filter(enviado_en__lt=antes_de)
    if excluir_pedido is not None:
        platos = platos.exclude(pedido_id=excluir_pedido)
    return platos


def carga_actual(area: str | None = None) -> int:
    """Minutos que tardaría en arrancar un pedido que entra ahora (lo que ya está en cola).

    Sin área: la más cargada entre la cocina y el shawarmero (estimación prudente para el cliente).
    """
    ahora = timezone.now()
    areas = [area] if area else ["COC", "SHA"]
    return max(math.ceil(sum(_restante(d, ahora) for d in _en_cola(area=a)) / _paralelo()) for a in areas)


def estimar_pedido(pedido) -> int | None:
    """Minutos que faltan para que el pedido esté listo; None si no tiene nada en cocina."""
    ahora = timezone.now()
    todos = [d for d in pedido.detalles.filter(enviado=True, estado__in=PENDIENTES).select_related("producto__categoria")]
    if not todos:
        return None
    resultado = 1
    for area in {_area(d) for d in todos}:  # cocina y shawarmero avanzan a la vez: manda el que más tarde
        propios = [d for d in todos if _area(d) == area]
        fechas = [d.enviado_en for d in propios if d.enviado_en]
        llegada = min(fechas) if fechas else ahora
        delante = sum(_restante(d, ahora) for d in _en_cola(antes_de=llegada, excluir_pedido=pedido.pk, area=area))
        mas_lento = max(_restante(d, ahora) for d in propios)
        resultado = max(resultado, math.ceil(delante / _paralelo() + mas_lento))
    return resultado


def estimar_nuevo(minutos_platos: list[int]) -> int:
    """Para un pedido que todavía no se envía (carrito del cliente o pedido en línea)."""
    if not minutos_platos:
        return 0
    return carga_actual() + max(minutos_platos)
