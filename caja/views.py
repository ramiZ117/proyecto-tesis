import csv
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Max, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from caja.correo import cierre_en_segundo_plano, destinos_cierre, enviar_cierre, enviar_en_segundo_plano, enviar_factura
from caja.models import (CierreCaja, ClienteFiscal, FinanciamientoCashea, Factura, LiquidacionCashea, Pago,
                         Propina, SolicitudCuenta, TasaCambio, datos_cliente_validos)
from caja.ticket import ticket_cierre, ticket_factura
from gestion.models import AjustesLocal
from config.formato import num
from config.permisos import GRUPO_CAJA, GRUPO_MESERO, en_grupo, es_admin, requiere_admin, requiere_grupo, texto_validacion
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
        if pedido.facturas_vigentes and pedido.facturas_vigentes[0].estado == Factura.Estado.PAGADA:
            continue  # cobrado por adelantado (para llevar): solo falta entregarlo
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
    contexto["metodos_propina"] = list(contexto["metodos"])
    contexto["cashea_activo"] = AjustesLocal.actual().cashea_activo
    if contexto["cashea_activo"]:
        contexto["metodos"].append((Pago.Metodo.CASHEA, "Financiado", "Lo financiado, en $, con n.º de orden"))
    contexto["cuenta_cliente"] = SolicitudCuenta.activa_de(factura.pedido)
    contexto["detalles_vigentes"] = [d for d in factura.pedido.detalles.all() if d.estado != DetallePedido.Estado.ANULADO]
    contexto["ticket"] = ticket_factura(factura, contexto["detalles_vigentes"])
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

    if any(p["metodo"] == Pago.Metodo.CASHEA for p in pagos) and not AjustesLocal.actual().cashea_activo:
        messages.error(request, "Las ventas financiadas no están activas en «Cobros y delivery».")
        return redirect("caja:factura", factura_id=factura.pk)
    if pagos and all(p["metodo"] == Pago.Metodo.CASHEA for p in pagos) and factura.saldo_local == factura.total_local:
        messages.warning(request, "Ojo: se registró todo como financiado, sin inicial.")
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
        destinos = destinos_cierre()
        cierre_en_segundo_plano(cierre_caja)
        envio = f" La nota se está enviando a {', '.join(destinos)}." if destinos else ""
        messages.success(request, f"Cierre de caja generado: {cierre_caja.cantidad_facturas} factura{'s' if cierre_caja.cantidad_facturas != 1 else ''} cobrada{'s' if cierre_caja.cantidad_facturas != 1 else ''}. "
                                  f"Imprime la nota de cierre o envíala por correo.{envio}")
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
    return render(request, "caja/cierre_detalle.html", {
        "cierre": cierre_caja, "datos": datos, "nota": ticket_cierre(cierre_caja, datos),
        "destinos": ", ".join(destinos_cierre()),
    })


@requiere_grupo(GRUPO_CAJA)
@require_POST
def cierre_correo(request, cierre_id):
    """Envía la nota de cierre a uno o varios correos (separados por coma)."""
    from django.core.validators import validate_email

    cierre_caja = _cierre_visible(request, cierre_id)
    destinos = [c.strip() for c in (request.POST.get("correos") or "").replace(";", ",").split(",") if c.strip()]
    if not destinos or len(destinos) > 5:
        messages.error(request, "Escribe entre uno y cinco correos, separados por coma.")
        return redirect("caja:cierre_detalle", cierre_id=cierre_caja.pk)
    for correo in destinos:
        try:
            validate_email(correo)
        except ValidationError:
            messages.error(request, f"El correo «{correo[:60]}» no es válido.")
            return redirect("caja:cierre_detalle", cierre_id=cierre_caja.pk)
    enviado, texto = enviar_cierre(cierre_caja, destinos)
    (messages.success if enviado else messages.error)(request, texto)
    return redirect("caja:cierre_detalle", cierre_id=cierre_caja.pk)


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



@requiere_grupo(GRUPO_CAJA)
def llevar(request):
    """La caja abre y atiende los pedidos para llevar de quien llega al mostrador."""
    from mesas.views import _para_llevar

    pedidos = _para_llevar()
    for p in pedidos:
        p.factura_vigente = p.factura_activa
    return render(request, "caja/llevar.html", {"llevar": pedidos, "seccion_caja": "llevar"})


# ---- clientes que piden factura con sus datos -------------------------------------------------
def _buscar_clientes(texto: str):
    texto = " ".join((texto or "").split())[:60]
    if len(texto) < 2:
        return ClienteFiscal.objects.none()
    digitos = "".join(c for c in texto if c.isdigit())
    filtro = Q(nombre__icontains=texto) | Q(documento__icontains=texto.upper().replace(" ", ""))
    if len(digitos) >= 3:
        filtro |= Q(documento__contains=digitos)
    return ClienteFiscal.objects.filter(filtro)


@requiere_grupo(GRUPO_CAJA)
def cliente_buscar(request):
    """Sugerencias para la factura personalizada: por cédula/RIF (o parte) o por nombre."""
    clientes = _buscar_clientes(request.GET.get("q", ""))[:8]
    return JsonResponse({"clientes": [c.como_datos() for c in clientes]})


@requiere_grupo(GRUPO_CAJA)
def clientes(request):
    q = (request.GET.get("q") or "").strip()
    lista = _buscar_clientes(q) if q else ClienteFiscal.objects.all()
    lista = list(lista.order_by("nombre")[:300])
    resumen = {
        fila["cliente_documento"]: fila
        for fila in Factura.objects.exclude(estado=Factura.Estado.ANULADA)
        .filter(cliente_documento__in=[c.documento for c in lista])
        .values("cliente_documento").annotate(facturas=Count("pk"), ultima=Max("emitida_en"))
    }
    for c in lista:
        fila = resumen.get(c.documento, {})
        c.facturas, c.ultima = fila.get("facturas", 0), fila.get("ultima")
    return render(request, "caja/clientes.html", {"clientes": lista, "q": q})


@requiere_grupo(GRUPO_CAJA)
def cliente_editar(request, cliente_id):
    cliente = get_object_or_404(ClienteFiscal, pk=cliente_id)
    error = ""
    if request.method == "POST":
        if request.POST.get("accion") == "borrar":
            if not es_admin(request.user):
                raise PermissionDenied
            cliente.delete()
            messages.success(request, f"Se borró a {cliente.nombre} de los clientes guardados (sus facturas no cambian).")
            return redirect("caja:clientes")
        datos, error = datos_cliente_validos(request.POST)
        if not error:
            otro = ClienteFiscal.objects.filter(documento=datos["documento"]).exclude(pk=cliente.pk).exists()
            if otro:
                error = "Ya hay otro cliente guardado con esa cédula o RIF."
            else:
                for campo in ClienteFiscal.CAMPOS:
                    setattr(cliente, campo, datos[campo])
                cliente.save()
                messages.success(request, f"Datos de {cliente.nombre} actualizados.")
                return redirect("caja:clientes")
        if error:
            messages.error(request, error)
    return render(request, "caja/cliente.html", {"cliente": cliente})


# ---- Cashea: lo financiado que Cashea paga cada miércoles -------------------------------------
def _por_semana(pendientes):
    semanas = {}
    for f in pendientes:
        semana = semanas.setdefault(f.fecha_pago, {"fecha_pago": f.fecha_pago, "ventas": [], "usd": Decimal("0.00"),
                                                   "local": Decimal("0.00")})
        semana["ventas"].append(f)
        semana["usd"] += f.monto_usd
        semana["local"] += f.monto_local
    hoy = timezone.localdate()
    for s in semanas.values():
        s["desde"] = s["fecha_pago"] - timedelta(days=9)
        s["hasta"] = s["fecha_pago"] - timedelta(days=3)
        s["estado"] = "atrasado" if s["fecha_pago"] < hoy else ("hoy" if s["fecha_pago"] == hoy else "proximo")
    return sorted(semanas.values(), key=lambda s: s["fecha_pago"])


@requiere_admin
def cashea(request):
    """Financiamiento actual: lo que Cashea le debe al local, agrupado por el miércoles en que lo paga."""
    pendientes = list(FinanciamientoCashea.objects.filter(estado=FinanciamientoCashea.Estado.PENDIENTE)
                      .select_related("factura__pedido__mesa"))
    por_devolver = list(FinanciamientoCashea.objects.filter(estado=FinanciamientoCashea.Estado.ANULADO,
                                                            liquidacion__isnull=False).select_related("factura"))
    liquidaciones = list(LiquidacionCashea.objects.select_related("registrada_por")
                         .annotate(ventas=Count("financiamientos"))[:20])
    total_usd = sum((f.monto_usd for f in pendientes), Decimal("0.00"))
    total_local = sum((f.monto_local for f in pendientes), Decimal("0.00"))
    hoy = timezone.localdate()
    return render(request, "caja/cashea.html", {
        "semanas": _por_semana(pendientes),
        "total_usd": total_usd, "total_local": total_local, "cantidad": len(pendientes),
        "atrasado_usd": sum((f.monto_usd for f in pendientes if f.fecha_pago < hoy), Decimal("0.00")),
        "por_devolver": por_devolver,
        "liquidaciones": liquidaciones,
        "cobrado_mes_usd": sum((l.monto_recibido_usd for l in liquidaciones
                                if l.fecha.year == hoy.year and l.fecha.month == hoy.month), Decimal("0.00")),
        "hoy": hoy,
        "activo": AjustesLocal.actual().cashea_activo,
    })


@requiere_admin
@require_POST
def cashea_liquidar(request):
    """Registra el pago que hizo Cashea por las ventas marcadas."""
    ids = [i for i in request.POST.getlist("financiamiento") if i.isdigit()]
    monto = _leer_monto(request.POST.get("monto"))
    fecha = parse_date(request.POST.get("fecha") or "") if request.POST.get("fecha") else timezone.localdate()
    if fecha is None:
        messages.error(request, "La fecha no es válida.")
        return redirect("caja:cashea")
    if not ids:
        messages.error(request, "Marca las ventas que cubre este pago de Cashea.")
        return redirect("caja:cashea")
    if monto is None:
        messages.error(request, "Escribe el monto que pagó Cashea.")
        return redirect("caja:cashea")
    try:
        liquidacion = LiquidacionCashea.registrar(
            financiamientos=list(FinanciamientoCashea.objects.filter(pk__in=ids)), fecha=fecha,
            moneda=request.POST.get("moneda") or Pago.Moneda.USD, monto=monto,
            referencia=request.POST.get("referencia", ""), nota=request.POST.get("nota", ""), usuario=request.user,
        )
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return redirect("caja:cashea")
    texto = f"Pago de Cashea registrado: ${num(liquidacion.monto_recibido_usd, 2)} por {len(ids)} venta{'s' if len(ids) != 1 else ''}."
    if liquidacion.descuento_usd > 0:
        texto += f" Cashea retuvo ${num(liquidacion.descuento_usd, 2)} (comisión)."
    messages.success(request, texto)
    return redirect("caja:cashea")


@requiere_grupo(GRUPO_CAJA)
def mesas(request):
    """La caja ve las comandas de las mesas y puede editarlas; siguen a nombre de su mesero."""
    from mesas.views import mesas_en_servicio

    return render(request, "caja/mesas.html", {"mesas_caja": mesas_en_servicio(), "seccion_caja": "mesas"})
