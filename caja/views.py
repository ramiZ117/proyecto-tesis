import csv
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
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

from caja.correo import enviar_en_segundo_plano, enviar_factura
from caja.models import CierreCaja, Factura, Pago, Propina, SolicitudCuenta, TasaCambio, datos_cliente_validos
from config.formato import num
from config.permisos import GRUPO_CAJA, GRUPO_MESERO, en_grupo, es_admin, requiere_grupo, texto_validacion
from mesas.models import DetallePedido, Mesa, Pedido, PedidoEnLinea

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


def _cuentas_abiertas(tasa):
    """Comandas abiertas (mesas y para llevar) con su consumo; las que piden cuenta van primero."""
    pedidos = (
        Pedido.objects.exclude(estado__in=[Pedido.Estado.CERRADO, Pedido.Estado.ANULADO])
        .exclude(en_linea=True)  # los pedidos en línea ya vienen pagados: tienen su propia pantalla
        .select_related("mesa", "mesero")
        .prefetch_related(
            Prefetch(
                "detalles",
                queryset=DetallePedido.objects.exclude(estado=DetallePedido.Estado.ANULADO).select_related("producto"),
                to_attr="items_cuenta",
            ),
            Prefetch("facturas", queryset=Factura.objects.exclude(estado=Factura.Estado.ANULADA), to_attr="facturas_vigentes"),
            Prefetch("solicitudes_cuenta", queryset=SolicitudCuenta.objects.filter(estado__in=SolicitudCuenta.ACTIVAS),
                     to_attr="cuenta_cliente"),
        )
    )
    cuentas_abiertas = []
    for pedido in pedidos:
        subtotal = sum((d.subtotal for d in pedido.items_cuenta), Decimal("0.00"))
        if pedido.mesa_id:
            solicitada = pedido.mesa.estado == Mesa.Estado.POR_FACTURAR
        else:
            solicitada = pedido.estado in {Pedido.Estado.LISTO, Pedido.Estado.ENTREGADO}
        cuentas_abiertas.append(
            {
                "pedido": pedido,
                "items": pedido.items_cuenta,
                "platos": sum(d.cantidad for d in pedido.items_cuenta),
                "subtotal": subtotal,
                "usd": tasa.a_usd(subtotal) if tasa else None,
                "factura": pedido.facturas_vigentes[0] if pedido.facturas_vigentes else None,
                "solicitada": solicitada,
                "sin_enviar": any(not d.enviado for d in pedido.items_cuenta),
                "cuenta_cliente": pedido.cuenta_cliente[0] if pedido.cuenta_cliente else None,
            }
        )
    cuentas_abiertas.sort(
        key=lambda c: (not (c["cuenta_cliente"] and c["cuenta_cliente"].estado == SolicitudCuenta.Estado.REPORTADA),
                       not c["solicitada"], c["pedido"].mesa is None, c["pedido"].mesa.numero if c["pedido"].mesa else c["pedido"].pk)
    )
    return cuentas_abiertas


@requiere_grupo(GRUPO_CAJA)
def cuentas(request):
    """Consumo consolidado por mesa y pedidos para llevar, con las cuentas solicitadas primero."""
    tasa = _tasa_activa()
    return render(request, "caja/cuentas.html", {"cuentas_abiertas": _cuentas_abiertas(tasa), "tasa": tasa})


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
        Factura.objects.select_related("pedido__mesa", "pedido__mesero", "cajero", "descuento_por").prefetch_related(
            "pagos",
            "pedido__detalles__producto",
        ),
        pk=factura_id,
    )
    saldo = factura.saldo_local
    factor_igtf = 1 + settings.IGTF_PORCENTAJE / 100
    contexto = {
        "factura": factura, "saldo": saldo, "saldo_usd": factura.saldo_usd,
        "saldo_usd_igtf": (saldo * factor_igtf / factura.tasa_cambio).quantize(CENTAVO, rounding=ROUND_HALF_UP),
        "igtf_porcentaje": settings.IGTF_PORCENTAJE, "igtf_metodos": ",".join(settings.IGTF_METODOS),
        "local_nombre": settings.RESTAURANTE_NOMBRE, "local_rif": settings.RESTAURANTE_RIF,
        "local_direccion": settings.RESTAURANTE_DIRECCION,
    }
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
        ultimo = factura.pagos.order_by("-registrado_en", "-pk").first()
        contexto["ultimo_vuelto"] = ultimo.vuelto_local if ultimo and ultimo.vuelto_local > 0 else None
    contexto["cola"] = [c for c in _cuentas_abiertas(_tasa_activa()) if c["pedido"].pk != factura.pedido_id][:8]
    contexto["metodos"] = [
        (Pago.Metodo.EFECTIVO, "Efectivo", "Bolívares, con vuelto"),
        (Pago.Metodo.PAGO_MOVIL, "Pago móvil", "Bolívares, con referencia"),
        (Pago.Metodo.DIVISA, "Divisa", f"Dólares en efectivo + {settings.IGTF_PORCENTAJE:g} % IGTF"),
        (Pago.Metodo.TARJETA, "Tarjeta", "Punto de venta"),
        (Pago.Metodo.BINANCE, "Binance", "Dólares, con referencia"),
        (Pago.Metodo.ZINLI, "Zinli", "Dólares, con referencia"),
    ]
    contexto["cuenta_cliente"] = SolicitudCuenta.activa_de(factura.pedido)
    contexto["detalles_vigentes"] = [d for d in factura.pedido.detalles.all() if d.estado != DetallePedido.Estado.ANULADO]
    contexto["propinas"] = list(factura.propinas.all())
    return render(request, "caja/factura.html", contexto)


@requiere_grupo(GRUPO_CAJA)
@require_POST
def registrar_pago(request, factura_id):
    """Cobro simple o mixto (varias líneas método/monto/referencia) y, si la hay, la propina."""
    factura = get_object_or_404(Factura, pk=factura_id)
    metodos = request.POST.getlist("metodo") or [Pago.Metodo.EFECTIVO]
    montos = request.POST.getlist("monto_recibido")
    referencias = request.POST.getlist("referencia")
    pagos = []
    for n, metodo in enumerate(metodos):
        texto = montos[n] if n < len(montos) else ""
        if not (texto or "").strip() and n > 0:
            continue  # fila adicional vacía: se ignora
        monto = _leer_monto(texto)
        if monto is None:
            messages.error(request, "Ingresa un monto válido." if len(metodos) == 1 else f"El monto del pago {n + 1} no es válido.")
            return redirect("caja:factura", factura_id=factura.pk)
        pagos.append({"metodo": metodo, "monto": monto, "referencia": (referencias[n] if n < len(referencias) else "").strip()[:60]})

    propina_texto = (request.POST.get("propina_monto") or "").strip()
    propina = _leer_monto(propina_texto) if propina_texto else None
    if propina_texto and propina is None:
        messages.error(request, "La propina debe ser un monto mayor a cero.")
        return redirect("caja:factura", factura_id=factura.pk)
    try:
        with transaction.atomic():
            if len(pagos) == 1:
                _, factura = factura.cobrar(
                    metodo=pagos[0]["metodo"], monto=pagos[0]["monto"], referencia=pagos[0]["referencia"],
                    pagador=request.POST.get("pagador", "").strip()[:80],
                )
            else:
                _, factura = factura.cobrar_mixto(pagos, pagador=request.POST.get("pagador", "").strip()[:80])
            if propina:
                Propina.registrar(factura, metodo=request.POST.get("propina_metodo") or Pago.Metodo.EFECTIVO,
                                  monto=propina, usuario=request.user)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return redirect("caja:factura", factura_id=factura.pk)
    extra = " Propina registrada a favor del mesero." if propina else ""
    if factura.estado == Factura.Estado.PAGADA:
        messages.success(request, "Pago registrado. Cuenta cerrada y mesa por limpiar." + extra
                         + (f" Enviando la factura a {factura.cliente_correo}." if factura.cliente_correo else ""))
        enviar_en_segundo_plano(factura)
    else:
        messages.success(request, f"Pago registrado. Saldo pendiente: Bs. {factura.saldo_local}." + extra)
    return redirect("caja:factura", factura_id=factura.pk)


@requiere_grupo(GRUPO_CAJA)
@require_POST
def registrar_propina(request, factura_id):
    """Propina que llega aparte del cobro (por ejemplo, cuando el cliente ya pagó)."""
    factura = get_object_or_404(Factura.objects.select_related("pedido"), pk=factura_id)
    monto = _leer_monto(request.POST.get("propina_monto"))
    if monto is None:
        messages.error(request, "La propina debe ser un monto mayor a cero.")
        return redirect("caja:factura", factura_id=factura.pk)
    try:
        propina = Propina.registrar(factura, metodo=request.POST.get("propina_metodo") or Pago.Metodo.EFECTIVO,
                                    monto=monto, usuario=request.user)
        messages.success(request, f"Propina registrada a favor de {propina.mesero.username}.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return redirect("caja:factura", factura_id=factura.pk)


@requiere_grupo(GRUPO_CAJA)
@require_POST
def descuento(request, factura_id):
    factura = get_object_or_404(Factura, pk=factura_id)
    try:
        if request.POST.get("quitar"):
            factura.quitar_descuento()
            messages.success(request, "Descuento quitado.")
        else:
            valor = _leer_monto(request.POST.get("valor"))
            aplicado = factura.aplicar_descuento(
                tipo=request.POST.get("tipo", ""), valor=valor, motivo=request.POST.get("motivo", ""), usuario=request.user
            )
            messages.success(request, f"Descuento de Bs {num(aplicado, 2)} aplicado.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return redirect("caja:factura", factura_id=factura.pk)


@requiere_grupo(GRUPO_CAJA, GRUPO_MESERO)
def propinas(request):
    """Saldo a favor de cada mesero por propinas no entregadas, y el historial reciente.

    La caja ve a todos y marca las entregas; el mesero ve solo las suyas ("Mis propinas").
    """
    from django.contrib.auth.models import User
    from django.db.models import Count, Sum

    es_caja = en_grupo(request.user, GRUPO_CAJA)
    base = Propina.objects.all() if es_caja else Propina.objects.filter(mesero=request.user)
    pendientes = (
        base.filter(pagada=False).values("mesero").annotate(
            total_local=Sum("monto_local"), total_usd=Sum("monto_usd"), cantidad=Count("id")
        )
    )
    usuarios = User.objects.in_bulk([fila["mesero"] for fila in pendientes])
    saldos = sorted(
        ({**f, "mesero": usuarios[f["mesero"]]} for f in pendientes), key=lambda f: f["mesero"].username
    )
    recientes = base.select_related("mesero", "factura__pedido__mesa", "pagada_por")[:40]
    return render(request, "caja/propinas.html", {"saldos": saldos, "recientes": recientes, "es_caja": es_caja})


@requiere_grupo(GRUPO_CAJA)
@require_POST
def pagar_propinas(request, mesero_id):
    """El negocio le entrega al mesero sus propinas pendientes: quedan marcadas como pagadas."""
    pendientes = list(Propina.objects.filter(mesero_id=mesero_id, pagada=False))
    if not pendientes:
        messages.error(request, "Ese mesero no tiene propinas pendientes.")
        return redirect("caja:propinas")
    total = sum((p.monto_local for p in pendientes), Decimal("0"))
    Propina.marcar_pagadas(pendientes, request.user)
    messages.success(request, f"Propinas de {pendientes[0].mesero.username} marcadas como pagadas (Bs {num(total, 2)}).")
    return redirect("caja:propinas")


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


VARIACION_MAXIMA_TASA = Decimal("0.20")


@requiere_grupo(GRUPO_CAJA)
def tasa_cambio(request):
    tasas = TasaCambio.objects.select_related("creada_por")[:20]
    propuesta = _leer_monto(request.GET.get("confirmar"), decimales=4)
    return render(request, "caja/tasa.html", {"tasas": tasas, "vigente": _tasa_activa(), "propuesta": propuesta})


@requiere_grupo(GRUPO_CAJA)
@require_POST
def tasa_bcv(request):
    """Consulta ya la tasa oficial del BCV (además de la revisión automática)."""
    from caja.tasa_bcv import actualizar

    resultado = actualizar(forzar=bool(request.POST.get("forzar")) and es_admin(request.user))
    (messages.success if resultado.actualizada else messages.warning)(request, resultado.mensaje)
    return redirect("caja:tasa")


@requiere_grupo(GRUPO_CAJA)
@require_POST
def guardar_tasa(request):
    valor = _leer_monto(request.POST.get("valor"), decimales=4)
    if valor is None:
        messages.error(request, "La tasa debe ser un número mayor a cero.")
        return redirect("caja:tasa")
    vigente = _tasa_activa()
    if vigente and not request.POST.get("confirmado"):
        variacion = abs(valor / vigente.valor - 1)
        if variacion > VARIACION_MAXIMA_TASA:
            messages.warning(
                request,
                f"La nueva tasa ({num(valor, 2)} Bs) cambia {variacion * 100:.0f}% respecto de la vigente ({num(vigente.valor, 2)} Bs). "
                "Confírmala si es correcta.",
            )
            return redirect(reverse("caja:tasa") + f"?confirmar={valor}")
    with transaction.atomic():
        TasaCambio.objects.filter(activa=True).update(activa=False)
        TasaCambio.objects.create(valor=valor, creada_por=request.user, activa=True)
    messages.success(request, f"Nueva tasa: 1 USD = {num(valor, 2)} Bs.")
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
    datos = cierre_caja.datos_guardados()
    return render(request, "caja/cierre_detalle.html", {"cierre": cierre_caja, "datos": datos})


@requiere_grupo(GRUPO_CAJA)
def cierre_csv(request, cierre_id):
    cierre_caja = _cierre_visible(request, cierre_id)
    datos = cierre_caja.datos_guardados()
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
            [f["numero"], f["mesa"], f["cajero"], f["total_local"], f["total_usd"],
             timezone.localtime(f["pagada_en"]).strftime("%d/%m/%Y %H:%M") if f["pagada_en"] else "",
             "Devuelta" if f["estado"] == Factura.Estado.ANULADA else "Cobrada"]
        )
    return respuesta


@requiere_grupo(GRUPO_CAJA)
@require_POST
def revisar_pago_cliente(request, factura_id):
    """Caja confirma (o no) el pago móvil, Binance o Zinli que el cliente reportó desde su mesa."""
    factura = get_object_or_404(Factura.objects.select_related("pedido__mesero"), pk=factura_id)
    solicitud = SolicitudCuenta.activa_de(factura.pedido)
    if solicitud is None:
        messages.error(request, "Esta cuenta no tiene un pago reportado por el cliente.")
        return redirect("caja:factura", factura_id=factura.pk)
    try:
        if request.POST.get("accion") == "rechazar":
            solicitud.rechazar(request.user)
            messages.warning(request, "Pago marcado como no recibido. El cliente verá el aviso en su teléfono.")
        else:
            with transaction.atomic():
                solicitud.confirmar(factura, request.user)
            factura.refresh_from_db()
            extra = f" Propina de {solicitud.get_metodo_display()} a favor de {factura.pedido.mesero.username}." if solicitud.propina else ""
            if factura.estado == Factura.Estado.PAGADA:
                messages.success(request, "Pago del cliente confirmado. Cuenta cerrada y mesa por limpiar." + extra)
                enviar_en_segundo_plano(factura)
            else:
                messages.success(request, f"Pago confirmado. Queda un saldo de Bs. {factura.saldo_local}." + extra)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc) + " Puedes registrarlo a mano en «Registrar pago».")
    return redirect("caja:factura", factura_id=factura.pk)


@requiere_grupo(GRUPO_CAJA)
@require_POST
def datos_cliente(request, factura_id):
    """Factura personalizada (nombre, cédula/RIF, dirección, teléfono, correo) o consumidor final."""
    factura = get_object_or_404(Factura, pk=factura_id)
    if factura.estado == Factura.Estado.ANULADA:
        messages.error(request, "La factura está anulada.")
        return redirect("caja:factura", factura_id=factura.pk)
    if request.POST.get("tipo") == "generica":
        factura.guardar_datos_cliente(None)
        messages.success(request, "Factura a consumidor final (sin datos del cliente).")
        return redirect("caja:factura", factura_id=factura.pk)
    datos, error = datos_cliente_validos(request.POST)
    if error:
        messages.error(request, error)
        return redirect(reverse("caja:factura", args=[factura.pk]) + "#datos-cliente")
    factura.guardar_datos_cliente(datos)
    messages.success(request, f"Factura a nombre de {datos['nombre']}.")
    return redirect("caja:factura", factura_id=factura.pk)


@requiere_grupo(GRUPO_CAJA)
@require_POST
def enviar_correo(request, factura_id):
    factura = get_object_or_404(Factura, pk=factura_id)
    correo = (request.POST.get("correo") or "").strip()
    if correo and correo != factura.cliente_correo:
        from django.core.validators import validate_email

        try:
            validate_email(correo)
        except ValidationError:
            messages.error(request, "El correo no es válido.")
            return redirect("caja:factura", factura_id=factura.pk)
        factura.cliente_correo = correo[:254]
        factura.save(update_fields=["cliente_correo"])
    enviado, texto = enviar_factura(factura)
    (messages.success if enviado else messages.error)(request, texto)
    return redirect("caja:factura", factura_id=factura.pk)


# ---- pedidos en línea para retirar ---------------------------------------------------------
def _pedidos_en_linea():
    activos = list(
        PedidoEnLinea.objects.filter(estado__in=[PedidoEnLinea.Estado.POR_VERIFICAR, PedidoEnLinea.Estado.ACEPTADO])
        .select_related("pedido", "revisado_por").order_by("creado_en")
    )
    grupos = {"verificar": [], "cocina": [], "listo": []}
    for p in activos:
        grupos.setdefault(p.etapa, []).append(p)
    recientes = list(PedidoEnLinea.objects.filter(estado__in=[PedidoEnLinea.Estado.ENTREGADO, PedidoEnLinea.Estado.RECHAZADO])
                     .order_by("-revisado_en", "-creado_en")[:10])
    return {**grupos, "recientes": recientes}


def _enlace_whatsapp(pedido_web) -> str:
    from urllib.parse import quote

    digitos = "".join(c for c in pedido_web.cliente_telefono if c.isdigit())
    if digitos.startswith("0"):
        digitos = "58" + digitos[1:]
    texto = (f"¡Hola {pedido_web.cliente_nombre}! Tu pedido {pedido_web.codigo} de {settings.RESTAURANTE_NOMBRE} "
             "está listo para retirar. ¡Te esperamos!")
    return f"https://wa.me/{digitos}?text={quote(texto)}"


@requiere_grupo(GRUPO_CAJA)
def en_linea(request):
    contexto = _pedidos_en_linea()
    for p in contexto["listo"]:
        p.whatsapp = _enlace_whatsapp(p)
    return render(request, "caja/en_linea.html", contexto)


@requiere_grupo(GRUPO_CAJA)
def en_linea_parcial(request):
    contexto = _pedidos_en_linea()
    for p in contexto["listo"]:
        p.whatsapp = _enlace_whatsapp(p)
    return render(request, "caja/_en_linea_grid.html", contexto)


@requiere_grupo(GRUPO_CAJA)
def comprobante(request, pedido_id):
    """El comprobante solo lo ve caja (no está en /media/)."""
    from django.http import FileResponse, Http404

    pedido_web = get_object_or_404(PedidoEnLinea, pk=pedido_id)
    if not pedido_web.comprobante:
        raise Http404
    try:
        archivo = pedido_web.comprobante.open("rb")
    except FileNotFoundError as exc:
        raise Http404 from exc
    nombre = pedido_web.comprobante.name.lower()
    tipo = "application/pdf" if nombre.endswith(".pdf") else "image/jpeg"
    respuesta = FileResponse(archivo, content_type=tipo)
    respuesta["Content-Disposition"] = "inline"
    respuesta["X-Content-Type-Options"] = "nosniff"
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta


@requiere_grupo(GRUPO_CAJA)
@require_POST
def en_linea_accion(request, pedido_id):
    pedido_web = get_object_or_404(PedidoEnLinea, pk=pedido_id)
    accion = request.POST.get("accion")
    try:
        if accion == "aceptar":
            factura = pedido_web.aceptar(request.user)
            messages.success(request, f"Pago de {pedido_web.codigo} verificado: el pedido ya está en cocina (factura {factura.numero}).")
            enviar_en_segundo_plano(factura)
        elif accion == "rechazar":
            pedido_web.rechazar(request.user, request.POST.get("motivo", ""))
            messages.warning(request, f"Pedido {pedido_web.codigo} rechazado. El cliente verá el motivo.")
        elif accion == "avisado":
            PedidoEnLinea.objects.filter(pk=pedido_web.pk).update(avisado_en=timezone.now())
            messages.success(request, f"Marcado: ya le avisaste a {pedido_web.cliente_nombre}.")
        elif accion == "entregar":
            pedido_web.entregar()
            messages.success(request, f"Pedido {pedido_web.codigo} entregado a {pedido_web.cliente_nombre}.")
        else:
            messages.error(request, "Acción no válida.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return redirect("caja:en_linea")
