"""Factura y nota de cierre en formato de ticket (impresora térmica de 80 mm, 40 columnas).

Se arma como texto de ancho fijo para que se vea igual en pantalla, impresa y en el correo:
encabezado con el RIF y la dirección del local, datos del cliente, cada plato con su cantidad y
precio, base imponible e IVA, forma de pago y total.
"""

import textwrap
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from config.formato import num

ANCHO = 40
LINEA = "-" * ANCHO

NOMBRE_METODO = {
    "EFE": "EFECTIVO BS",
    "PMO": "PAGO MOVIL",
    "DIV": "DIVISA",
    "TAR": "TARJETA",
    "BIN": "BINANCE",
    "ZIN": "ZINLI",
}


def bs(valor) -> str:
    return f"Bs {num(Decimal(valor), 2)}"


def usd(valor) -> str:
    return f"$ {num(Decimal(valor), 2)}"


def centro(texto: str) -> list[str]:
    return [linea.center(ANCHO).rstrip() for linea in textwrap.wrap(texto, ANCHO) or [""]]


def izq(texto: str, sangria: str = "") -> list[str]:
    return textwrap.wrap(texto, ANCHO, subsequent_indent=sangria) or [""]


def par(izquierda: str, derecha: str) -> str:
    """Texto a la izquierda y monto a la derecha; si no caben, el texto se recorta."""
    espacio = ANCHO - len(derecha) - 1
    if len(izquierda) > espacio:
        izquierda = izquierda[: max(espacio - 1, 1)] + "…"
    return izquierda + " " * (ANCHO - len(izquierda) - len(derecha)) + derecha


def encabezado_local() -> list[str]:
    lineas = []
    if settings.RESTAURANTE_RIF:
        lineas += centro(f"RIF {settings.RESTAURANTE_RIF}")
    lineas += centro(settings.RESTAURANTE_RAZON_SOCIAL)
    for parte in settings.RESTAURANTE_DIRECCION_FISCAL.split("|"):
        if parte.strip():
            lineas += centro(parte.strip().upper())
    if settings.RESTAURANTE_TELEFONO:
        lineas += centro(f"TELF: {settings.RESTAURANTE_TELEFONO}")
    return lineas


def numero_factura(numero: str) -> str:
    """F-000123 → 00000123 (como lo imprime la máquina fiscal)."""
    digitos = "".join(c for c in numero if c.isdigit())
    return digitos.zfill(8) if digitos else numero


def ticket_factura(factura, detalles=None) -> str:
    pedido = factura.pedido
    if detalles is None:
        detalles = [d for d in pedido.detalles.all() if d.estado != "ANU"]
    emitida = timezone.localtime(factura.emitida_en)
    lineas = encabezado_local()

    if factura.personalizada:
        lineas += izq(f"Cliente: {factura.cliente_nombre.upper()}", "  ")
        lineas.append(f"CI/RIF: {factura.cliente_documento}")
        lineas += izq(f"Direccion: {factura.cliente_direccion.upper()}", "  ")
        if factura.cliente_telefono:
            lineas.append(f"Telefono: {factura.cliente_telefono}")
        if factura.cliente_correo:
            lineas += izq(f"Correo: {factura.cliente_correo}")
    else:
        lineas.append("Cliente: CONSUMIDOR FINAL")
    lineas.append(f"{pedido.pk:08d}/CONTADO")
    lugar = f"Mesa: {pedido.mesa.numero}" if pedido.mesa_id else f"Para llevar {pedido.codigo}"
    lineas += izq(f"Mesonero: {pedido.mesero.username} / {lugar}")
    lineas.append(f"Cajero: {factura.cajero.username}")
    lineas.append(f"Tasa Ref. Bs./$: {num(factura.tasa_cambio, 2)}")
    lineas += centro("FACTURA")
    lineas.append(par("FACTURA:", numero_factura(factura.numero)))
    lineas.append(par(f"FECHA: {emitida:%d-%m-%Y}", f"HORA: {emitida:%H:%M}"))
    lineas.append(LINEA)

    for d in detalles:
        lineas.append(f"{d.cantidad}x {bs(d.precio_unitario)}")
        lineas.append(par(f"{d.producto.nombre.upper()} (G)", bs(d.subtotal)))
    lineas.append(LINEA)

    if factura.descuento_local:
        lineas.append(par("SUBTOTAL", bs(factura.subtotal_local)))
        lineas.append(par("DESCUENTO", "-" + bs(factura.descuento_local)))
    iva = f"{num(factura.iva_porcentaje, 2)}%"
    lineas.append(par(f"BI G{iva}", bs(factura.base_imponible_local)))
    lineas.append(par(f"IVA G{iva}", bs(factura.iva_local)))
    lineas.append(LINEA)

    pagos = list(factura.pagos.all())
    for p in pagos:
        nombre = NOMBRE_METODO.get(p.metodo, p.get_metodo_display().upper())
        if p.moneda == "USD":
            nombre += f" {usd(p.monto_recibido)}"
        lineas.append(par(nombre, bs(p.monto_local)))
        if p.referencia:
            lineas.append(f"  Ref. {p.referencia}")
        if p.vuelto_local and p.vuelto_local > 0:
            lineas.append(par("  VUELTO", bs(p.vuelto_local)))
        if p.igtf_local:
            lineas.append(par(f"  IGTF {num(settings.IGTF_PORCENTAJE, 2)}%", bs(p.igtf_local)))
    if pagos:
        lineas.append(LINEA)

    lineas.append(par("TOTAL", bs(factura.total_local)))
    if factura.igtf_local:
        lineas.append(par("TOTAL PAGADO CON IGTF", bs(factura.total_con_igtf_local)))
    lineas.append(par("REF. EN DOLARES", usd(factura.total_usd)))
    if factura.estado == "ANU":
        lineas.append("")
        lineas += centro("*** FACTURA ANULADA ***")
        if factura.motivo_anulacion:
            lineas += centro(factura.motivo_anulacion)
    lineas.append("")
    lineas += centro("¡GRACIAS POR SU COMPRA!")
    return "\n".join(lineas)


def ticket_cierre(cierre, datos=None) -> str:
    """Nota de cierre de caja: lo cobrado por método, efectivo y divisas que deben estar en caja, impuestos."""
    datos = datos or cierre.datos_guardados()
    cerrado = timezone.localtime(cierre.cerrado_en or cierre.hasta)
    lineas = encabezado_local()
    lineas += centro("NOTA DE CIERRE DE CAJA")
    lineas.append(par("CIERRE N.:", f"{cierre.secuencia:04d}"))
    lineas.append(f"Cajero: {cierre.cajero.username}")
    desde = timezone.localtime(cierre.desde).strftime("%d-%m-%Y %H:%M") if cierre.desde else "inicio"
    lineas.append(f"Desde: {desde}")
    lineas.append(f"Hasta: {timezone.localtime(cierre.hasta):%d-%m-%Y %H:%M}")
    lineas.append(par(f"FECHA: {cerrado:%d-%m-%Y}", f"HORA: {cerrado:%H:%M}"))
    lineas.append(f"Tasa Ref. Bs./$: {num(cierre.tasa_cambio, 2)}")
    lineas.append(LINEA)

    lineas += centro("COBROS POR METODO")
    for l in datos["lineas"]:
        nombre = NOMBRE_METODO.get(l["metodo"], l["metodo_label"].upper())
        lineas.append(par(f"{nombre} ({l['cantidad']})", bs(l["aplicado_local"])))
        if l["moneda"] == "USD":
            lineas.append(par("  Recibido", usd(l["recibido"])))
        if l["vuelto_local"]:
            lineas.append(par("  Vuelto entregado", bs(l["vuelto_local"])))
        if l["devuelto_local"]:
            lineas.append(par("  Devuelto", "-" + bs(l["devuelto_local"])))
    if not datos["lineas"]:
        lineas.append("Sin cobros en este periodo.")
    lineas.append(LINEA)
    lineas.append(par("TOTAL COBRADO", bs(datos["total_local"])))
    lineas.append(par("REF. EN DOLARES", usd(datos["total_usd"])))
    lineas.append(LINEA)

    lineas += centro("DEBE HABER EN CAJA")
    lineas.append(par("Efectivo en bolivares", bs(datos["efectivo_bs_neto"])))
    lineas.append(par("Divisas en efectivo", usd(datos["divisas_usd"])))
    lineas.append(LINEA)

    lineas.append(par("Facturas cobradas", str(datos["cantidad_facturas"])))
    lineas.append(par("Facturas anuladas", str(datos["facturas_anuladas"])))
    if datos["cantidad_devoluciones"]:
        lineas.append(par(f"Devoluciones ({datos['cantidad_devoluciones']})", "-" + bs(datos["devuelto_local"])))
    if datos["descuentos_local"]:
        lineas.append(par("Descuentos otorgados", bs(datos["descuentos_local"])))
    lineas.append(par("BASE IMPONIBLE", bs(datos["base_local"])))
    lineas.append(par("IVA", bs(datos["iva_local"])))
    lineas.append(par("IGTF", bs(datos["igtf_local"])))
    if datos["propinas"]:
        lineas.append(LINEA)
        lineas += centro("PROPINAS (DE LOS MESEROS)")
        for x in datos["propinas"]:
            nombre = NOMBRE_METODO.get(x["metodo"], x["metodo_label"].upper())
            monto = usd(x["monto"]) if x["moneda"] == "USD" else bs(x["monto"])
            lineas.append(par(f"{nombre} ({x['cantidad']})", monto))
    if datos["facturas"]:
        lineas.append(LINEA)
        lineas += centro("FACTURAS DEL PERIODO")
        for f in datos["facturas"]:
            marca = " DEV" if f["estado"] == "ANU" else ""
            lugar = f"Mesa {f['mesa']}" if str(f["mesa"]).isdigit() else str(f["mesa"])
            lineas.append(par(f"{numero_factura(f['numero'])} {lugar}{marca}", bs(f["total_local"])))
    lineas.append(LINEA)
    lineas.append("")
    lineas.append("Firma cajero: ______________________")
    lineas.append("")
    lineas.append("Recibido por: ______________________")
    return "\n".join(lineas)
