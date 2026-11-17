from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from caja.models import TasaCambio
from config.dinero import precio_local_vigente
from config.permisos import GRUPO_MESERO, es_admin, requiere_grupo, texto_validacion
from inventario.models import Categoria, Producto
from inventario.servicios import disponibilidad_menu
from mesas.models import DetallePedido, Mesa, Pedido


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
    conteo = {estado: 0 for estado in Mesa.Estado.values}
    for mesa in mesas:
        conteo[mesa.estado] += 1
        mesa.pedido_info = pedidos_abiertos.get(mesa.pk)
        mesa.seleccionada = mesa.pk == seleccionada
        if mesa.pedido_info:
            detalles = list(mesa.pedido_info.detalles.all())
            mesa.total_info = sum((d.subtotal for d in detalles), 0)
            mesa.listos_info = sum(1 for d in detalles if d.estado == DetallePedido.Estado.LISTO)
    posiciones = {(m.pos_x, m.pos_y) for m in mesas}
    usar_posiciones = len(posiciones) == len(mesas) and len(mesas) > 0
    return {
        "mesas": mesas,
        "usar_posiciones": usar_posiciones,
        "columnas": (max((m.pos_x for m in mesas), default=0) + 1) if usar_posiciones else 4,
        "conteo": conteo,
        "seleccionada": seleccionada,
    }


def _para_llevar():
    return list(
        Pedido.objects.filter(tipo=Pedido.Tipo.LLEVAR)
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
    contexto = _contexto_mapa(pedido.mesa_id)
    contexto.update(
        {
            "llevar": _para_llevar(),
            "pedido": pedido,
            "categorias": categorias,
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
