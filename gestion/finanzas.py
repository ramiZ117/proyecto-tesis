"""Ganancia real del negocio.

Ventas sin IVA (el IVA es del fisco, no del local)
  − costo de lo vendido (recetas valorizadas al costo del momento de la venta)
  = ganancia bruta
  − mermas (insumos perdidos)
  − gastos fijos del mes
  − gastos extraordinarios (cuota del mes)
  = ganancia neta

Todo en dólares, que es la moneda de los costos. Las propinas y el IGTF no son ingreso del local.
"""

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone

CENTAVO = Decimal("0.01")
CERO = Decimal("0.00")


def _usd(valor) -> Decimal:
    return (valor or CERO).quantize(CENTAVO)


def rango_mes(anio: int, mes: int):
    inicio = date(anio, mes, 1)
    fin = date(anio, mes, monthrange(anio, mes)[1])
    desde = timezone.make_aware(datetime.combine(inicio, time.min))
    hasta = timezone.make_aware(datetime.combine(fin + timedelta(days=1), time.min))
    return inicio, fin, desde, hasta


def ventas_y_costos(desde, hasta) -> dict:
    """Ventas sin IVA, costo de lo vendido y mermas entre dos instantes (USD)."""
    from caja.models import Factura
    from inventario.models import MovimientoStock

    ventas = CERO
    facturas = Factura.objects.filter(estado=Factura.Estado.PAGADA, pagada_en__gte=desde, pagada_en__lt=hasta)
    for f in facturas.only("base_imponible_local", "tasa_cambio"):
        ventas += f.base_imponible_local / f.tasa_cambio
    valor = ExpressionWrapper(F("cantidad") * F("costo_unitario"), output_field=DecimalField(max_digits=18, decimal_places=4))
    movs = MovimientoStock.objects.filter(creado_en__gte=desde, creado_en__lt=hasta)
    costo = movs.filter(tipo=MovimientoStock.Tipo.SALIDA).aggregate(v=Sum(valor))["v"]
    merma = movs.filter(tipo=MovimientoStock.Tipo.MERMA).aggregate(v=Sum(valor))["v"]
    ventas, costo, merma = _usd(ventas), _usd(costo), _usd(merma)
    bruta = ventas - costo
    return {
        "ventas": ventas, "costo_ventas": costo, "ganancia_bruta": bruta, "mermas": merma,
        "margen_bruto": int(round(bruta / ventas * 100)) if ventas else None,
        "facturas": facturas.count(),
    }


def rentabilidad_por_plato(desde, hasta) -> list[dict]:
    """Unidades, ventas sin IVA, costo y ganancia de cada plato vendido en el período."""
    from caja.models import Factura
    from mesas.models import DetallePedido

    facturas = {f.pedido_id: f for f in Factura.objects.filter(
        estado=Factura.Estado.PAGADA, pagada_en__gte=desde, pagada_en__lt=hasta)}
    filas = defaultdict(lambda: {"unidades": 0, "ventas": CERO, "costo": CERO})
    detalles = (DetallePedido.objects.filter(pedido_id__in=facturas.keys())
                .exclude(estado=DetallePedido.Estado.ANULADO)
                .select_related("producto").prefetch_related("movimientos_stock"))
    for d in detalles:
        f = facturas[d.pedido_id]
        proporcion = (f.total_local / f.subtotal_local) if f.subtotal_local else Decimal(1)  # descuentos
        factor_iva = 1 + f.iva_porcentaje / 100
        fila = filas[d.producto]
        fila["unidades"] += d.cantidad
        fila["ventas"] += d.subtotal * proporcion / factor_iva / f.tasa_cambio
        fila["costo"] += sum((m.cantidad * m.costo_unitario for m in d.movimientos_stock.all()
                              if m.tipo == "SAL"), CERO)
    resultado = []
    for producto, fila in filas.items():
        ventas, costo = _usd(fila["ventas"]), _usd(fila["costo"])
        ganancia = ventas - costo
        resultado.append({
            "producto": producto, "unidades": fila["unidades"], "ventas": ventas, "costo": costo,
            "ganancia": ganancia, "margen": int(round(ganancia / ventas * 100)) if ventas else None,
        })
    resultado.sort(key=lambda r: r["ganancia"], reverse=True)
    return resultado


def gastos_del_mes(anio: int, mes: int) -> dict:
    from gestion.models import GastoExtraordinario, GastoFijo

    inicio, fin, _, _ = rango_mes(anio, mes)
    fijos = [g for g in GastoFijo.objects.all() if g.aplica_en(inicio, fin)]
    extraordinarios = []
    primero = date(anio, mes, 1) - timedelta(days=60 * 31)
    for g in GastoExtraordinario.objects.filter(fecha__gte=primero, fecha__lte=fin):
        cuota = g.cuota_en(anio, mes)
        if cuota:
            g.cuota_mes = cuota
            g.numero_cuota = g._indice_mes(anio, mes) + 1
            extraordinarios.append(g)
    return {
        "fijos": fijos, "total_fijos": sum((g.monto_usd for g in fijos), CERO),
        "extraordinarios": extraordinarios, "total_extraordinarios": sum((g.cuota_mes for g in extraordinarios), CERO),
    }


def resultado_mes(anio: int, mes: int) -> dict:
    """Estado de resultados del mes: de las ventas a la ganancia neta."""
    _, _, desde, hasta = rango_mes(anio, mes)
    datos = ventas_y_costos(desde, hasta)
    gastos = gastos_del_mes(anio, mes)
    neta = datos["ganancia_bruta"] - datos["mermas"] - gastos["total_fijos"] - gastos["total_extraordinarios"]
    return {
        **datos, **gastos, "ganancia_neta": neta,
        "margen_neto": int(round(neta / datos["ventas"] * 100)) if datos["ventas"] else None,
    }


def inversion_actual() -> dict:
    """Lo que hay invertido hoy: inventario valorizado al costo + equipos y mobiliario comprados."""
    from gestion.models import GastoExtraordinario
    from inventario.models import Insumo

    inventario = _usd(sum((i.stock_actual * i.costo_unitario for i in Insumo.objects.filter(activo=True)), CERO))
    equipos = _usd(GastoExtraordinario.objects.filter(categoria__in=GastoExtraordinario.INVERSION)
                   .aggregate(v=Sum("monto_usd"))["v"])
    return {"inventario": inventario, "equipos": equipos, "total": inventario + equipos}
