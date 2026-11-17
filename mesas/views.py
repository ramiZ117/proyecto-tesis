from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from caja.models import TasaCambio
from config.dinero import precio_local_vigente
from config.permisos import GRUPO_MESERO, es_admin, requiere_grupo, texto_validacion
from inventario.models import Categoria, Producto, Promocion
from inventario.servicios import disponibilidad_menu
from mesas.models import DetallePedido, Mesa, Pedido, SolicitudCliente


def _atiende(request, pedido: Pedido) -> bool:
    """Un mesero solo gestiona sus pedidos; el administrador gestiona todos."""
    return es_admin(request.user) or pedido.mesero_id == request.user.id


def _pedido_propio(request, pedido_id: int):
    """Devuelve (pedido, respuesta_redirect). Si hay respuesta, se debe devolver tal cual."""
    pedido = get_object_or_404(Pedido.objects.select_related("mesa", "mesero"), pk=pedido_id)
    if not _atiende(request, pedido):
        quien = f"La {pedido.mesa}" if pedido.mesa_id else f"El pedido {pedido.etiqueta}"
        messages.error(request, f"{quien} la atiende {pedido.mesero.username}.")
        return pedido, redirect("mesas:mapa")
    return pedido, None


def _volver(pedido: Pedido):
    return redirect("mesas:comanda", pedido_id=pedido.pk)


def _contexto_mapa(seleccionada: int | None = None):
    pedidos_abiertos = {
        p.mesa_id: p
        for p in Pedido.objects.filter(tipo=Pedido.Tipo.MESA)
        .exclude(estado__in=[Pedido.Estado.CERRADO, Pedido.Estado.ANULADO])
        .select_related("mesero")
        .prefetch_related(
            Prefetch("detalles", queryset=DetallePedido.objects.exclude(estado=DetallePedido.Estado.ANULADO))
        )
    }
    mesas = list(Mesa.objects.filter(activa=True))
    llamados = {s.mesa_id: s for s in SolicitudCliente.objects.filter(estado=SolicitudCliente.Estado.PENDIENTE)}
    conteo = {estado: 0 for estado in Mesa.Estado.values}
    for mesa in mesas:
        conteo[mesa.estado] += 1
        mesa.pedido_info = pedidos_abiertos.get(mesa.pk)
        mesa.llamado = llamados.get(mesa.pk)
        mesa.seleccionada = mesa.pk == seleccionada
        if mesa.pedido_info:
            detalles = list(mesa.pedido_info.detalles.all())
            mesa.total_info = sum((d.subtotal for d in detalles), 0)
            mesa.listos_info = sum(1 for d in detalles if d.estado == DetallePedido.Estado.LISTO)
    celdas = [c for m in mesas for c in m.celdas()]
    usar_posiciones = len(set(celdas)) == len(celdas) and len(mesas) > 0
    return {
        "mesas": mesas,
        "usar_posiciones": usar_posiciones,
        "columnas": max((m.pos_x + m.ancho for m in mesas), default=1) if usar_posiciones else 4,
        "conteo": conteo,
        "seleccionada": seleccionada,
        "llamados": len(llamados),
    }


def _para_llevar():
    return list(
        Pedido.objects.filter(tipo=Pedido.Tipo.LLEVAR, en_linea=False)
        .exclude(estado__in=[Pedido.Estado.CERRADO, Pedido.Estado.ANULADO])
        .select_related("mesero")
        .prefetch_related(
            Prefetch(
                "detalles",
                queryset=DetallePedido.objects.exclude(estado=DetallePedido.Estado.ANULADO).select_related("producto"),
            )
        )
        .order_by("creado_en")
    )


@requiere_grupo(GRUPO_MESERO)
def mapa(request):
    contexto = _contexto_mapa()
    contexto["llevar"] = _para_llevar()
    return render(request, "mesas/salon.html", contexto)


@requiere_grupo(GRUPO_MESERO)
def mapa_parcial(request):
    try:
        seleccionada = int(request.GET.get("sel", ""))
    except ValueError:
        seleccionada = None
    return render(request, "mesas/_mapa_grid.html", _contexto_mapa(seleccionada))


@requiere_grupo(GRUPO_MESERO)
@require_POST
def abrir_pedido(request, mesa_id):
    mesa = get_object_or_404(Mesa, pk=mesa_id, activa=True)
    try:
        pedido, _ = Pedido.abrir_para(mesa.pk, request.user)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return redirect("mesas:mapa")
    if not _atiende(request, pedido):
        messages.error(request, f"La {mesa} la atiende {pedido.mesero.username}.")
        return redirect("mesas:mapa")
    return _volver(pedido)


@requiere_grupo(GRUPO_MESERO)
@require_POST
def nuevo_para_llevar(request):
    try:
        pedido = Pedido.crear_para_llevar(request.POST.get("cliente", ""), request.user)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return redirect("mesas:mapa")
    messages.success(request, f"Pedido para llevar {pedido.codigo} abierto para {pedido.cliente}.")
    return _volver(pedido)


@requiere_grupo(GRUPO_MESERO)
def comanda(request, pedido_id):
    pedido, respuesta = _pedido_propio(request, pedido_id)
    if respuesta:
        return respuesta
    pedido = Pedido.objects.select_related("mesa", "mesero").prefetch_related("detalles__producto").get(pk=pedido.pk)
    porciones = disponibilidad_menu()
    categorias = list(
        Categoria.objects.filter(activa=True, productos__disponible=True)
        .prefetch_related(Prefetch("productos", queryset=Producto.objects.filter(disponible=True)))
        .distinct()
    )
    for categoria in categorias:
        for producto in categoria.productos.all():
            producto.precio_bs = precio_local_vigente(producto.precio_usd)
            producto.porciones = porciones.get(producto.pk)
            producto.agotado = producto.porciones is not None and producto.porciones <= 0
    detalles = [d for d in pedido.detalles.all() if d.estado != DetallePedido.Estado.ANULADO]
    promociones = [p for p in Promocion.vigentes() if p.disponible]
    for promo in promociones:
        promo.precio_bs = precio_local_vigente(promo.precio_usd)
        promo.agotada = any(
            porciones.get(i.producto_id) is not None and porciones[i.producto_id] < i.cantidad for i in promo.items.all()
        )
    solicitud = None
    if pedido.mesa_id:
        solicitud = SolicitudCliente.objects.filter(mesa_id=pedido.mesa_id, estado=SolicitudCliente.Estado.PENDIENTE).first()
        if solicitud:
            solicitud.filas = solicitud.detalle()
    contexto = _contexto_mapa(pedido.mesa_id)
    contexto.update(
        {
            "llevar": _para_llevar(),
            "pedido": pedido,
            "categorias": categorias,
            "promociones": promociones,
            "solicitud": solicitud,
            "cuenta_cliente": _cuenta_cliente(pedido),
            "minutos_estimados": _minutos_pedido(pedido),
            "facturado": pedido.factura_activa,
            "hay_sin_enviar": any(not d.enviado for d in detalles),
            "nuevos": sum(d.cantidad for d in detalles if not d.enviado),
            "detalles": detalles,
            "subtotal_usd": _a_usd(pedido.subtotal),
            "mesas_libres": [m for m in contexto["mesas"] if m.estado == Mesa.Estado.DISPONIBLE and not m.pedido_info],
        }
    )
    return render(request, "mesas/salon.html", contexto)


def _a_usd(monto_local):
    tasa = TasaCambio.objects.filter(activa=True).order_by("-vigente_desde").first()
    return tasa.a_usd(monto_local) if tasa else None


@requiere_grupo(GRUPO_MESERO)
@require_POST
def agregar_item(request, pedido_id):
    pedido, respuesta = _pedido_propio(request, pedido_id)
    if respuesta:
        return respuesta
    try:
        pedido._exigir_abierto()
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return _volver(pedido)
    try:
        producto = Producto.objects.get(pk=request.POST.get("producto_id"), disponible=True)
        cantidad = int(request.POST.get("cantidad") or 1)
        if cantidad < 1 or cantidad > 99:
            raise ValueError
    except (Producto.DoesNotExist, ValueError, TypeError):
        messages.error(request, "Selecciona un platillo y una cantidad válida.")
        return _volver(pedido)

    if not TasaCambio.objects.filter(activa=True).exists():
        messages.error(request, "No hay tasa de cambio vigente: pide al cajero o al administrador que la registre.")
        return _volver(pedido)

    porciones = disponibilidad_menu().get(producto.pk)
    if porciones is not None and cantidad > porciones:
        if porciones <= 0:
            messages.error(request, f"{producto.nombre} está agotado por falta de insumos.")
        else:
            messages.error(request, f"Solo quedan {porciones} porciones de {producto.nombre}.")
        return _volver(pedido)

    observaciones = (request.POST.get("observaciones") or "").strip()[:250]
    # Tocar el mismo plato otra vez suma una unidad al que aún no se envió (misma nota).
    existente = pedido.detalles.filter(
        producto=producto, enviado=False, estado=DetallePedido.Estado.PENDIENTE, observaciones=observaciones
    ).first()
    if existente:
        existente.cambiar_cantidad(cantidad)
    else:
        DetallePedido.objects.create(
            pedido=pedido,
            producto=producto,
            cantidad=cantidad,
            precio_unitario=precio_local_vigente(producto.precio_usd),
            observaciones=observaciones,
        )
    if pedido.mesa and pedido.mesa.estado != Mesa.Estado.OCUPADA:
        pedido.mesa.ocupar()
    messages.success(request, f"Se agregó {cantidad} × {producto.nombre}. Recuerda enviarlo a cocina.")
    return _volver(pedido)


@requiere_grupo(GRUPO_MESERO)
@require_POST
def cambiar_cantidad(request, detalle_id):
    detalle = get_object_or_404(DetallePedido.objects.select_related("pedido"), pk=detalle_id)
    pedido, respuesta = _pedido_propio(request, detalle.pedido_id)
    if respuesta:
        return respuesta
    delta = 1 if request.POST.get("delta") == "1" else -1
    if delta > 0:
        porciones = disponibilidad_menu().get(detalle.producto_id)
        if porciones is not None and porciones < 1:
            messages.error(request, f"No quedan porciones de {detalle.producto.nombre}.")
            return _volver(pedido)
    try:
        detalle.cambiar_cantidad(delta)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return _volver(pedido)


@requiere_grupo(GRUPO_MESERO)
@require_POST
def nota_pedido(request, pedido_id):
    pedido, respuesta = _pedido_propio(request, pedido_id)
    if respuesta:
        return respuesta
    try:
        pedido._exigir_abierto()
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return _volver(pedido)
    pedido.notas = " ".join((request.POST.get("notas") or "").split())[:250]
    pedido.save(update_fields=["notas", "actualizado_en"])
    messages.success(request, "Nota de la comanda guardada." if pedido.notas else "Nota de la comanda borrada.")
    return _volver(pedido)


@requiere_grupo(GRUPO_MESERO)
@require_POST
def mover_pedido(request, pedido_id):
    pedido, respuesta = _pedido_propio(request, pedido_id)
    if respuesta:
        return respuesta
    origen = pedido.etiqueta
    try:
        pedido.mover_a(int(request.POST.get("mesa_id", "")))
        messages.success(request, f"Comanda movida de {origen} a {pedido.mesa}.")
    except (ValueError, TypeError):
        messages.error(request, "Elige la mesa de destino.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return _volver(pedido)


@requiere_grupo(GRUPO_MESERO)
@require_POST
def quitar_item(request, detalle_id):
    detalle = get_object_or_404(DetallePedido.objects.select_related("pedido__mesa", "pedido__mesero"), pk=detalle_id)
    pedido, respuesta = _pedido_propio(request, detalle.pedido_id)
    if respuesta:
        return respuesta
    try:
        detalle.cancelar()
        messages.success(request, "Platillo quitado de la comanda.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return _volver(pedido)


@requiere_grupo(GRUPO_MESERO)
@require_POST
def enviar_cocina(request, pedido_id):
    pedido, respuesta = _pedido_propio(request, pedido_id)
    if respuesta:
        return respuesta
    try:
        pedido.enviar_a_cocina()
        messages.success(request, "Comanda enviada a cocina.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return _volver(pedido)


@requiere_grupo(GRUPO_MESERO)
@require_POST
def solicitar_cuenta(request, pedido_id):
    pedido, respuesta = _pedido_propio(request, pedido_id)
    if respuesta:
        return respuesta
    try:
        pedido.solicitar_cuenta()
        messages.success(request, f"{pedido.mesa} marcada como por facturar. Caja ya puede cobrarla.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return _volver(pedido)
    return redirect("mesas:mapa")


@requiere_grupo(GRUPO_MESERO)
@require_POST
def anular_pedido(request, pedido_id):
    pedido, respuesta = _pedido_propio(request, pedido_id)
    if respuesta:
        return respuesta
    try:
        pedido.anular(forzar=es_admin(request.user))
        if pedido.mesa:
            messages.success(request, f"Pedido anulado. {pedido.mesa} quedó disponible.")
        else:
            messages.success(request, f"Pedido para llevar {pedido.codigo} anulado.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return _volver(pedido)
    return redirect("mesas:mapa")


@requiere_grupo(GRUPO_MESERO)
@require_POST
def entregar_item(request, detalle_id):
    detalle = get_object_or_404(DetallePedido.objects.select_related("pedido__mesa", "pedido__mesero"), pk=detalle_id)
    pedido, respuesta = _pedido_propio(request, detalle.pedido_id)
    if respuesta:
        return respuesta
    try:
        detalle.marcar_entregado()
        messages.success(request, f"{detalle.producto.nombre} marcado como entregado.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return _volver(pedido)


@requiere_grupo(GRUPO_MESERO)
@require_POST
def marcar_limpia(request, mesa_id):
    mesa = get_object_or_404(Mesa, pk=mesa_id)
    if mesa.estado != Mesa.Estado.POR_LIMPIAR:
        messages.error(request, "La mesa no está en estado por limpiar.")
        return redirect("mesas:mapa")
    mesa.marcar_disponible()
    messages.success(request, f"Mesa {mesa.numero} disponible.")
    return redirect("mesas:mapa")


def _promo_vigente(promocion_id):
    from inventario.models import Promocion

    return Promocion.vigentes().filter(pk=promocion_id).first()


@requiere_grupo(GRUPO_MESERO)
@require_POST
def agregar_promocion(request, pedido_id):
    pedido, respuesta = _pedido_propio(request, pedido_id)
    if respuesta:
        return respuesta
    promo = _promo_vigente(request.POST.get("promocion_id") or 0) if (request.POST.get("promocion_id") or "").isdigit() else None
    if promo is None:
        messages.error(request, "Esa promoción no está disponible hoy.")
        return _volver(pedido)
    if not TasaCambio.objects.filter(activa=True).exists():
        messages.error(request, "No hay tasa de cambio vigente: pide al cajero o al administrador que la registre.")
        return _volver(pedido)
    porciones = disponibilidad_menu()
    for item in promo.items.all():
        libres = porciones.get(item.producto_id)
        if libres is not None and libres < item.cantidad:
            messages.error(request, f"No alcanza {item.producto.nombre} para la promoción (quedan {max(libres, 0)}).")
            return _volver(pedido)
    try:
        pedido.agregar_promocion(promo)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return _volver(pedido)
    messages.success(request, f"Se agregó la promoción {promo.nombre}. Recuerda enviarla a cocina.")
    return _volver(pedido)


def _solicitud_de(request, solicitud_id):
    """Solicitud pendiente + comanda de su mesa (la abre si hace falta). Devuelve (solicitud, pedido, respuesta)."""
    solicitud = get_object_or_404(SolicitudCliente.objects.select_related("mesa"), pk=solicitud_id)
    if solicitud.estado != SolicitudCliente.Estado.PENDIENTE:
        messages.info(request, "Esa solicitud ya fue atendida.")
        return solicitud, None, redirect("mesas:mapa")
    try:
        pedido, _ = Pedido.abrir_para(solicitud.mesa_id, request.user)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return solicitud, None, redirect("mesas:mapa")
    pedido, respuesta = _pedido_propio(request, pedido.pk)
    return solicitud, pedido, respuesta


@requiere_grupo(GRUPO_MESERO)
@require_POST
def cargar_solicitud(request, solicitud_id):
    """Pasa lo que pidió el cliente desde el menú a la comanda: queda sin enviar para revisarlo."""
    solicitud, pedido, respuesta = _solicitud_de(request, solicitud_id)
    if respuesta:
        return respuesta
    if not TasaCambio.objects.filter(activa=True).exists():
        messages.error(request, "No hay tasa de cambio vigente: pide al cajero o al administrador que la registre.")
        return _volver(pedido)
    try:
        agregados, avisos = solicitud.cargar_en(pedido, request.user)
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return _volver(pedido)
    for aviso in avisos:
        messages.warning(request, aviso)
    if agregados:
        messages.success(request, f"Pedido del cliente cargado ({agregados} plato{'s' if agregados != 1 else ''}). Revísalo y envíalo a cocina.")
    elif not avisos:
        messages.success(request, "Llamado atendido.")
    return _volver(pedido)


@requiere_grupo(GRUPO_MESERO)
@require_POST
def descartar_solicitud(request, solicitud_id):
    solicitud, pedido, respuesta = _solicitud_de(request, solicitud_id)
    if respuesta:
        return respuesta
    try:
        solicitud.descartar(request.user)
        messages.success(request, "Llamado atendido." if solicitud.es_solo_llamado else "Solicitud del cliente descartada.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return _volver(pedido)


def _cuenta_cliente(pedido):
    from caja.models import SolicitudCuenta

    return SolicitudCuenta.activa_de(pedido)



@requiere_grupo(GRUPO_MESERO)
@require_POST
def personalizar_item(request, detalle_id):
    """Opciones permitidas del plato (sin vegetales, salsa aparte…) y nota libre, antes de enviarlo a cocina."""
    detalle = get_object_or_404(DetallePedido.objects.select_related("pedido", "producto"), pk=detalle_id)
    pedido, respuesta = _pedido_propio(request, detalle.pedido_id)
    if respuesta:
        return respuesta
    if detalle.enviado or detalle.promocion_id or detalle.estado != DetallePedido.Estado.PENDIENTE:
        messages.error(request, "Solo se personalizan los platos que aún no se envían a cocina.")
        return _volver(pedido)
    permitidas = {o.casefold(): o for o in detalle.producto.lista_opciones}
    elegidas = []
    for opcion in request.POST.getlist("opcion"):
        clave = " ".join(opcion.split()).casefold()
        if clave not in permitidas:
            messages.error(request, f"«{opcion[:40]}» no es una opción de {detalle.producto.nombre}.")
            return _volver(pedido)
        if permitidas[clave] not in elegidas:
            elegidas.append(permitidas[clave])
    nota = " ".join((request.POST.get("nota") or "").split())[:120]
    texto = ", ".join(elegidas) + (" · " if elegidas and nota else "") + nota
    detalle.observaciones = texto[:250]
    detalle.save(update_fields=["observaciones", "actualizado_en"])
    messages.success(request, f"{detalle.producto.nombre}: {texto or 'sin personalizar'}.")
    return _volver(pedido)



def _minutos_pedido(pedido):
    from cocina.tiempos import estimar_pedido

    return estimar_pedido(pedido)
