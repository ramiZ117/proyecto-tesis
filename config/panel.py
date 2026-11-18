"""Indicadores del panel del administrador (sistema de información gerencial, cap. 2.2.1).

Todo se calcula a partir de las transacciones ya registradas: no hay datos propios del panel.
"""

from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

from caja.models import CierreCaja, Devolucion, Factura, Pago
from inventario.models import MovimientoStock
from inventario.servicios import estado_insumos
from mesas.models import DetallePedido, Mesa, Pedido

CENTAVO = Decimal("0.01")
PERIODOS = {"hoy": "Hoy", "semana": "Semana", "mes": "Mes"}
DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
COLORES_METODO = {"PMO": "#1B7F45", "DIV": "#E5B830", "EFE": "#2457C5", "TAR": "#B9A6EC", "BIN": "#C9A227", "ZIN": "#6C3FD1"}


def _inicio_dia(fecha):
    return timezone.make_aware(datetime.combine(fecha, time.min))


def rango(periodo: str, ahora=None):
    """(desde, hasta, desde_anterior, hasta_anterior) del período, hasta el instante actual."""
    ahora = ahora or timezone.now()
    hoy = timezone.localdate(ahora)
    if periodo == "semana":
        desde = _inicio_dia(hoy - timedelta(days=hoy.weekday()))
        previo = desde - timedelta(days=7)
        return desde, ahora, previo, previo + (ahora - desde)
    if periodo == "mes":
        desde = _inicio_dia(hoy.replace(day=1))
        mes_ant = (hoy.replace(day=1) - timedelta(days=1)).replace(day=1)
        previo = _inicio_dia(mes_ant)
        return desde, ahora, previo, previo + (ahora - desde)
    desde = _inicio_dia(hoy)
    return desde, ahora, desde - timedelta(days=1), ahora - timedelta(days=1)


def _usd(monto_local: Decimal, tasa: Decimal) -> Decimal:
    return (monto_local / tasa).quantize(CENTAVO, rounding=ROUND_HALF_UP) if tasa else Decimal("0.00")


def _barras(periodo: str, desde, hasta) -> list[dict]:
    """Ventas netas en USD agrupadas por hora (hoy), día (semana) o semana del mes (mes)."""
    if periodo == "hoy":
        claves = list(range(24))  # se recorta al horario con ventas más abajo
        etiqueta = lambda c: f"{c}h"  # noqa: E731
        clave_de = lambda dt: dt.hour  # noqa: E731
    elif periodo == "semana":
        claves = list(range(7))
        etiqueta = lambda c: DIAS[c]  # noqa: E731
        clave_de = lambda dt: dt.weekday()  # noqa: E731
    else:
        claves = list(range(1, 6))
        etiqueta = lambda c: f"Sem {c}"  # noqa: E731
        clave_de = lambda dt: min((dt.day - 1) // 7 + 1, 5)  # noqa: E731
    totales = {c: Decimal("0") for c in claves}
    for pago in Pago.objects.select_related("factura").filter(registrado_en__gt=desde, registrado_en__lte=hasta):
        c = clave_de(timezone.localtime(pago.registrado_en))
        if c in totales:
            totales[c] += _usd(pago.monto_local, pago.factura.tasa_cambio)
    for dev in Devolucion.objects.select_related("factura").filter(registrado_en__gt=desde, registrado_en__lte=hasta):
        c = clave_de(timezone.localtime(dev.registrado_en))
        if c in totales:
            totales[c] -= _usd(dev.monto_local, dev.factura.tasa_cambio)
    if periodo == "hoy":
        # Horario habitual de 10:00 a 23:00, ampliado si hubo ventas antes o después.
        con_ventas = [h for h, v in totales.items() if v]
        desde_h = min([10] + con_ventas)
        hasta_h = max([23] + con_ventas)
        claves = list(range(desde_h, hasta_h + 1))
    maximo = max([v for v in totales.values()] + [Decimal("0")])
    return [
        {
            "etiqueta": etiqueta(c),
            "valor": totales[c].quantize(CENTAVO),
            "alto": int(totales[c] / maximo * 100) if maximo > 0 and totales[c] > 0 else 0,
            "pico": maximo > 0 and totales[c] == maximo,
        }
        for c in claves
    ]


def _metodos(lineas: list[dict]) -> tuple[list[dict], str]:
    por_metodo: dict[str, dict] = {}
    for linea in lineas:
        fila = por_metodo.setdefault(linea["metodo"], {"metodo": linea["metodo"], "nombre": linea["metodo_label"], "monto": Decimal("0")})
        fila["monto"] += Decimal(str(linea["aplicado_local"])) - Decimal(str(linea["devuelto_local"]))
    filas = [f for f in por_metodo.values() if f["monto"] > 0]
    total = sum((f["monto"] for f in filas), Decimal("0"))
    filas.sort(key=lambda f: f["monto"], reverse=True)
    acumulado = 0
    tramos = []
    for f in filas:
        f["pct"] = int(round(f["monto"] / total * 100)) if total else 0
        f["color"] = COLORES_METODO.get(f["metodo"], "#9AA3A0")
        fin = 100 if f is filas[-1] else acumulado + f["pct"]
        tramos.append(f"{f['color']} {acumulado}% {fin}%")
        acumulado = fin
    gradiente = f"conic-gradient({', '.join(tramos)})" if tramos else "conic-gradient(#E8E6DF 0 100%)"
    return filas, gradiente


def _top_platos(desde, hasta, limite=5) -> list[dict]:
    filas = list(
        DetallePedido.objects.exclude(estado=DetallePedido.Estado.ANULADO)
        .filter(pedido__facturas__estado=Factura.Estado.PAGADA, pedido__facturas__pagada_en__gt=desde,
                pedido__facturas__pagada_en__lte=hasta)
        .values("producto__nombre")
        .annotate(total=Sum("cantidad"))
        .order_by("-total", "producto__nombre")[:limite]
    )
    maximo = filas[0]["total"] if filas else 0
    return [
        {"nombre": f["producto__nombre"], "cantidad": f["total"], "ancho": int(f["total"] / maximo * 100) if maximo else 0}
        for f in filas
    ]


def _minutos_cocina(desde, hasta) -> int | None:
    tiempos = [
        (d.listo_en - d.enviado_en).total_seconds()
        for d in DetallePedido.objects.filter(listo_en__gt=desde, listo_en__lte=hasta, enviado_en__isnull=False).only("listo_en", "enviado_en")
    ]
    if not tiempos:
        return None
    return int(round(sum(tiempos) / len(tiempos) / 60))


def _actividad(limite=6) -> list[dict]:
    eventos = []
    for f in Factura.objects.filter(estado=Factura.Estado.PAGADA).select_related("cajero").order_by("-pagada_en")[:limite]:
        eventos.append({"cuando": f.pagada_en, "texto": f"Factura {f.numero} cobrada", "sub": f.cajero.username, "tono": "ambar"})
    for d in DetallePedido.objects.filter(listo_en__isnull=False).select_related("producto", "pedido__mesa").order_by("-listo_en")[:limite]:
        eventos.append({"cuando": d.listo_en, "texto": f"{d.cantidad} × {d.producto.nombre} listo para servir",
                        "sub": f"Cocina · {d.pedido.etiqueta}", "tono": "verde"})
    for m in MovimientoStock.objects.exclude(tipo=MovimientoStock.Tipo.SALIDA).select_related("insumo", "usuario").order_by("-creado_en")[:limite]:
        autor = m.usuario.username if m.usuario else "sistema"
        eventos.append({"cuando": m.creado_en, "texto": f"{m.get_tipo_display()}: {m.cantidad.normalize():f} {m.insumo.abreviatura} de {m.insumo.nombre}",
                        "sub": autor, "tono": "rojo" if m.tipo == MovimientoStock.Tipo.MERMA else "gris"})
    for p in Pedido.objects.filter(estado=Pedido.Estado.ABIERTO).exclude(enviado_cocina_en__isnull=False).select_related("mesa", "mesero").order_by("-creado_en")[:limite]:
        eventos.append({"cuando": p.creado_en, "texto": f"{p.etiqueta}: comanda abierta", "sub": p.mesero.username, "tono": "azul"})
    eventos.sort(key=lambda e: e["cuando"], reverse=True)
    return eventos[:limite]


def indicadores(periodo: str = "hoy") -> dict:
    periodo = periodo if periodo in PERIODOS else "hoy"
    desde, hasta, desde_ant, hasta_ant = rango(periodo)
    datos = CierreCaja.calcular(None, desde, hasta)
    previos = CierreCaja.calcular(None, desde_ant, hasta_ant)
    delta = None
    if previos["total_usd"] > 0:
        delta = int(round((datos["total_usd"] - previos["total_usd"]) / previos["total_usd"] * 100))
    metodos, gradiente = _metodos(datos["lineas"])
    mesas = list(Mesa.objects.filter(activa=True).order_by("numero"))
    alertas = [i for i in estado_insumos() if i.nivel != "ok"]
    alertas.sort(key=lambda i: (i.nivel != "critico", i.nombre))
    return {
        "periodo": periodo,
        "periodos": list(PERIODOS.items()),
        "titulo_barras": {"hoy": "Ventas por hora", "semana": "Ventas por día", "mes": "Ventas por semana"}[periodo],
        "comparacion": {"hoy": "ayer", "semana": "la semana pasada", "mes": "el mes pasado"}[periodo],
        "ganancia": _ganancia(desde, hasta),
        "ventas_local": datos["total_local"],
        "iva_local": datos["iva_local"],
        "base_local": datos["base_local"],
        "igtf_local": datos["igtf_local"],
        "en_linea": _en_linea_hoy(),
        "ventas_usd": datos["total_usd"],
        "delta": delta,
        "facturas": datos["cantidad_facturas"],
        "ticket_usd": (datos["total_usd"] / datos["cantidad_facturas"]).quantize(CENTAVO) if datos["cantidad_facturas"] else None,
        "mesas": mesas,
        "mesas_en_servicio": sum(1 for m in mesas if m.estado in (Mesa.Estado.OCUPADA, Mesa.Estado.POR_FACTURAR)),
        "para_llevar": Pedido.objects.filter(tipo=Pedido.Tipo.LLEVAR).exclude(estado__in=[Pedido.Estado.CERRADO, Pedido.Estado.ANULADO]).count(),
        "minutos_cocina": _minutos_cocina(desde, hasta),
        "barras": _barras(periodo, desde, hasta),
        "metodos": metodos,
        "gradiente": gradiente,
        "top": _top_platos(desde, hasta),
        "alertas": alertas[:4],
        "actividad": _actividad(),
    }



def _en_linea_hoy() -> int:
    from mesas.models import PedidoEnLinea

    return PedidoEnLinea.objects.filter(creado_en__date=timezone.localdate()).exclude(
        estado=PedidoEnLinea.Estado.RECHAZADO).count()



def _ganancia(desde, hasta) -> dict:
    from gestion.finanzas import ventas_y_costos

    return ventas_y_costos(desde, hasta)
