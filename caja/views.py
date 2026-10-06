import csv
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from caja.models import CierreCaja, Factura, Pago, TasaCambio
from config.permisos import GRUPO_CAJA, es_admin, requiere_grupo, texto_validacion
from mesas.models import DetallePedido, Mesa, Pedido

CENTAVO = Decimal("0.01")


def _leer_monto(texto, decimales=2):
    """Convierte texto del formulario a Decimal > 0, finito y acotado; None si no es válido."""
    try:
        valor = Decimal((texto or "").strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not valor.is_finite() or valor <= 0 or valor >= Decimal("10000000"):
        return None
    valor = valor.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)
    return valor if valor > 0 else None


def _volver_factura(factura_id, dividir=False):
    url = reverse("caja:factura", args=[factura_id])
    if dividir:
        url += "?dividir=1#division"
    return redirect(url)


def _tasa_activa():
    return TasaCambio.objects.filter(activa=True).order_by("-vigente_desde").first()


@requiere_grupo(GRUPO_CAJA)
def cuentas(request):
    """Consumo consolidado por mesa, con las cuentas solicitadas primero."""
    pedidos = (
        Pedido.objects.exclude(estado__in=[Pedido.Estado.CERRADO, Pedido.Estado.ANULADO])
        .select_related("mesa", "mesero")
        .prefetch_related(
            Prefetch(
                "detalles",
                queryset=DetallePedido.objects.exclude(estado=DetallePedido.Estado.ANULADO).select_related("producto"),
                to_attr="items_cuenta",
            )
        )
    )
    tasa = _tasa_activa()
    cuentas_abiertas = []
    for pedido in pedidos:
        subtotal = sum((d.subtotal for d in pedido.items_cuenta), Decimal("0.00"))
        cuentas_abiertas.append(
            {
                "pedido": pedido,
                "items": pedido.items_cuenta,
                "subtotal": subtotal,
                "usd": tasa.a_usd(subtotal) if tasa else None,
                "factura": pedido.factura_activa,
                "solicitada": pedido.mesa.estado == Mesa.Estado.POR_FACTURAR,
                "sin_enviar": any(not d.enviado for d in pedido.items_cuenta),
            }
        )
    cuentas_abiertas.sort(key=lambda c: (not c["solicitada"], c["pedido"].mesa.numero))
    return render(request, "caja/cuentas.html", {"cuentas_abiertas": cuentas_abiertas, "tasa": tasa})


@requiere_grupo(GRUPO_CAJA)
@require_POST
def emitir_factura(request, pedido_id):
    pedido = get_object_or_404(Pedido, pk=pedido_id)
    try:
        factura = Factura.emitir_desde_pedido(pedido, request.user)
        messages.success(request, f"Factura {factura.numero} emitida.")
        return redirect("caja:factura", factura_id=factura.pk)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return redirect("caja:cuentas")


@requiere_grupo(GRUPO_CAJA)
def factura_detalle(request, factura_id):
    factura = get_object_or_404(
        Factura.objects.select_related("pedido__mesa", "cajero").prefetch_related(
            "pagos",
            "pedido__detalles__producto",
        ),
        pk=factura_id,
    )
    saldo = factura.saldo_local
    contexto = {"factura": factura, "saldo": saldo, "saldo_usd": factura.saldo_usd}
    try:
        partes = int(request.GET.get("partes", "0"))
    except ValueError:
        partes = 0
    if factura.estado == Factura.Estado.ABIERTA:
        division = factura.resumen_por_pagador()
        contexto["division"] = division
        elegido = (request.GET.get("pagador") or "").strip().casefold()
        for persona in division["personas"]:
            if persona["nombre"].casefold() == elegido:
                contexto["prefill_pagador"] = persona["nombre"]
                contexto["prefill_monto"] = min(persona["pendiente_local"], saldo)
        if 2 <= partes <= 30 and saldo > 0:
            cuota = (saldo / partes).quantize(CENTAVO, rounding=ROUND_HALF_UP)
            contexto.update(
                partes=partes,
                cuota_local=cuota,
                cuota_usd=(cuota / factura.tasa_cambio).quantize(CENTAVO, rounding=ROUND_HALF_UP),
            )
        contexto["mostrar_division"] = bool(
            request.GET.get("dividir") == "1"
            or division["hay_asignaciones"]
            or contexto.get("partes")
            or contexto.get("prefill_pagador")
        )
    else:
        contexto["devoluciones"] = list(factura.devoluciones.select_related("registrado_por"))
    return render(request, "caja/factura.html", contexto)


@requiere_grupo(GRUPO_CAJA)
@require_POST
def registrar_pago(request, factura_id):
    factura = get_object_or_404(Factura, pk=factura_id)
    monto = _leer_monto(request.POST.get("monto_recibido"))
    if monto is None:
        messages.error(request, "Ingresa un monto válido.")
        return redirect("caja:factura", factura_id=factura.pk)
    try:
        _, factura = factura.cobrar(
            metodo=request.POST.get("metodo") or Pago.Metodo.EFECTIVO,
            monto=monto,
            referencia=request.POST.get("referencia", "").strip()[:60],
            pagador=request.POST.get("pagador", "").strip()[:80],
        )
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return redirect("caja:factura", factura_id=factura.pk)
    if factura.estado == Factura.Estado.PAGADA:
        messages.success(request, "Pago registrado. Cuenta cerrada y mesa por limpiar.")
    else:
        messages.success(request, f"Pago registrado. Saldo pendiente: Bs. {factura.saldo_local}.")
    return redirect("caja:factura", factura_id=factura.pk)


@requiere_grupo(GRUPO_CAJA)
@require_POST
def anular_factura(request, factura_id):
    factura = get_object_or_404(Factura, pk=factura_id)
    motivo = request.POST.get("motivo", "")
    try:
        if not factura.pagos.exists():
            factura.anular(motivo)
            messages.success(request, f"Factura {factura.numero} anulada. La mesa puede modificarse de nuevo.")
            return redirect("caja:cuentas")
        if not es_admin(request.user):
            messages.error(request, "Solo un administrador puede anular una factura con pagos y devolver el dinero.")
            return redirect("caja:factura", factura_id=factura.pk)
        devoluciones = factura.anular_con_devolucion(motivo, request.user)
        resumen = " · ".join(
            f"{d.get_metodo_display()}: {d.monto} {'USD' if d.moneda == 'USD' else 'Bs.'}" for d in devoluciones
        )
        messages.success(request, f"Factura {factura.numero} anulada. Devolver al cliente → {resumen}.")
        return redirect("caja:factura", factura_id=factura.pk)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return redirect("caja:factura", factura_id=factura.pk)


@requiere_grupo(GRUPO_CAJA)
@require_POST
def asignar_platillo(request, factura_id):
    factura = get_object_or_404(Factura, pk=factura_id)
    try:
        detalle_id = int(request.POST.get("detalle_id", ""))
        texto = (request.POST.get("cantidad") or "").strip()
        cantidad = int(texto) if texto else None
        factura.asignar(detalle_id, request.POST.get("pagador", ""), cantidad)
    except (ValueError, TypeError):
        messages.error(request, "Elige un platillo y una cantidad válida.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return _volver_factura(factura.pk, dividir=True)


@requiere_grupo(GRUPO_CAJA)
@require_POST
def quitar_asignacion(request, factura_id):
    factura = get_object_or_404(Factura, pk=factura_id)
    try:
        texto = request.POST.get("asignacion_id", "").strip()
        factura.quitar_asignacion(int(texto) if texto else None)
    except ValueError:
        messages.error(request, "Asignación no válida.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return _volver_factura(factura.pk, dividir=True)


@requiere_grupo(GRUPO_CAJA)
def tasa_cambio(request):
    tasas = TasaCambio.objects.select_related("creada_por")[:20]
    return render(request, "caja/tasa.html", {"tasas": tasas, "vigente": _tasa_activa()})


@requiere_grupo(GRUPO_CAJA)
@require_POST
def guardar_tasa(request):
    valor = _leer_monto(request.POST.get("valor"), decimales=4)
    if valor is None:
        messages.error(request, "La tasa debe ser un número mayor a cero.")
        return redirect("caja:tasa")
    with transaction.atomic():
        TasaCambio.objects.filter(activa=True).update(activa=False)
        TasaCambio.objects.create(valor=valor, creada_por=request.user, activa=True)
    messages.success(request, f"Nueva tasa: 1 USD = {valor} Bs.")
    return redirect("caja:tasa")


# ---- cierre y reportes -------------------------------------------------------
@requiere_grupo(GRUPO_CAJA)
def cierre(request):
    """Cobros pendientes de cerrar (desde el último cierre de este cajero)."""
    ultimo = CierreCaja.ultimo_de(request.user)
    datos = CierreCaja.calcular(request.user, ultimo.hasta if ultimo else None)
    abiertas = Factura.objects.filter(cajero=request.user, estado=Factura.Estado.ABIERTA).count()
    return render(
        request,
        "caja/cierre.html",
        {"datos": datos, "ultimo": ultimo, "tasa": _tasa_activa(), "facturas_abiertas": abiertas},
    )


@requiere_grupo(GRUPO_CAJA)
@require_POST
def generar_cierre(request):
    try:
        cierre_caja = CierreCaja.generar(request.user)
        messages.success(request, f"Cierre de caja generado: {cierre_caja.cantidad_facturas} facturas cobradas.")
        return redirect("caja:cierre_detalle", cierre_id=cierre_caja.pk)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return redirect("caja:cierre")


@requiere_grupo(GRUPO_CAJA)
def historial(request):
    cierres = CierreCaja.objects.select_related("cajero")
    if not es_admin(request.user):
        cierres = cierres.filter(cajero=request.user)
    return render(request, "caja/historial.html", {"cierres": cierres[:60]})


def _cierre_visible(request, cierre_id):
    cierre_caja = get_object_or_404(CierreCaja.objects.select_related("cajero"), pk=cierre_id)
    if not es_admin(request.user) and cierre_caja.cajero_id != request.user.id:
        raise PermissionDenied
    return cierre_caja


@requiere_grupo(GRUPO_CAJA)
def cierre_detalle(request, cierre_id):
    cierre_caja = _cierre_visible(request, cierre_id)
    datos = CierreCaja.calcular(cierre_caja.cajero, cierre_caja.desde, cierre_caja.hasta)
    return render(request, "caja/cierre_detalle.html", {"cierre": cierre_caja, "datos": datos})


@requiere_grupo(GRUPO_CAJA)
def cierre_csv(request, cierre_id):
    cierre_caja = _cierre_visible(request, cierre_id)
    datos = CierreCaja.calcular(cierre_caja.cajero, cierre_caja.desde, cierre_caja.hasta)
    return _respuesta_csv(
        f"cierre-{cierre_caja.fecha}-{cierre_caja.cajero.username}.csv",
        titulo=f"Cierre de caja {cierre_caja.fecha} - cajero {cierre_caja.cajero.username}",
        datos=datos,
    )


def _rango_dia(fecha):
    tz = timezone.get_current_timezone()
    inicio = timezone.make_aware(datetime.combine(fecha, time.min), tz)
    fin = timezone.make_aware(datetime.combine(fecha, time.max), tz)
    return inicio, fin


@requiere_grupo(GRUPO_CAJA)
def reporte_dia(request):
    """Reporte de ventas del día. El administrador ve a todos los cajeros; el cajero, lo suyo."""
    fecha = parse_date(request.GET.get("fecha", "")) or timezone.localdate()
    inicio, fin = _rango_dia(fecha)
    cajero = None if es_admin(request.user) else request.user
    datos = CierreCaja.calcular(cajero, inicio - timedelta(microseconds=1), fin)
    if request.GET.get("formato") == "csv":
        return _respuesta_csv(f"reporte-{fecha}.csv", titulo=f"Reporte de ventas {fecha}", datos=datos)
    return render(
        request,
        "caja/reporte.html",
        {"fecha": fecha, "datos": datos, "consolidado": cajero is None},
    )


def _respuesta_csv(nombre: str, titulo: str, datos: dict) -> HttpResponse:
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre}"'
    respuesta.write("\ufeff")  # BOM para que Excel respete los acentos
    escritor = csv.writer(respuesta, delimiter=";")
    escritor.writerow([titulo])
    escritor.writerow([])
    escritor.writerow(["Método", "Moneda", "Pagos", "Recibido", "Aplicado (Bs.)", "Vuelto (Bs.)", "Devuelto"])
    for linea in datos["lineas"]:
        escritor.writerow(
            [linea["metodo_label"], linea["moneda"], linea["cantidad"], linea["recibido"],
             linea["aplicado_local"], linea["vuelto_local"], linea["devuelto"]]
        )
    escritor.writerow([])
    escritor.writerow(["Total cobrado neto (Bs.)", datos["total_local"]])
    escritor.writerow(["Devoluciones (Bs.)", datos["devuelto_local"]])
    escritor.writerow(["Total cobrado (USD)", datos["total_usd"]])
    escritor.writerow(["Vuelto entregado (Bs.)", datos["vuelto_local"]])
    escritor.writerow(["Efectivo neto en caja (Bs.)", datos["efectivo_bs_neto"]])
    escritor.writerow(["Divisas en caja (USD)", datos["divisas_usd"]])
    escritor.writerow(["Facturas anuladas", datos["facturas_anuladas"]])
    escritor.writerow([])
    escritor.writerow(["Factura", "Mesa", "Cajero", "Total (Bs.)", "Total (USD)", "Pagada", "Estado"])
    for f in datos["facturas"]:
        escritor.writerow(
            [f.numero, f.pedido.mesa.numero, f.cajero.username, f.total_local, f.total_usd,
             timezone.localtime(f.pagada_en).strftime("%d/%m/%Y %H:%M") if f.pagada_en else "",
             "Devuelta" if f.estado == Factura.Estado.ANULADA else "Cobrada"]
        )
    return respuesta
