from django.contrib import messages
from django.core.exceptions import ValidationError
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
        messages.error(request, f"La {pedido.mesa} la atiende {pedido.mesero.username}.")
        return pedido, redirect("mesas:mapa")
    return pedido, None


def _contexto_mapa():
    mesas = list(Mesa.objects.filter(activa=True))
    for mesa in mesas:
        mesa.pedido_info = None
        if mesa.estado in {Mesa.Estado.OCUPADA, Mesa.Estado.POR_FACTURAR}:
            mesa.pedido_info = mesa.pedido_activo()
    posiciones = {(m.pos_x, m.pos_y) for m in mesas}
    usar_posiciones = len(posiciones) == len(mesas) and len(mesas) > 0
    return {
        "mesas": mesas,
        "usar_posiciones": usar_posiciones,
        "columnas": (max((m.pos_x for m in mesas), default=0) + 1) if usar_posiciones else 4,
    }


@requiere_grupo(GRUPO_MESERO)
def mapa(request):
    return render(request, "mesas/mapa.html", _contexto_mapa())


@requiere_grupo(GRUPO_MESERO)
def mapa_parcial(request):
    return render(request, "mesas/_mapa_grid.html", _contexto_mapa())


@requiere_grupo(GRUPO_MESERO)
@require_POST
def abrir_pedido(request, mesa_id):
    mesa = get_object_or_404(Mesa, pk=mesa_id, activa=True)
    if mesa.estado == Mesa.Estado.POR_LIMPIAR:
        messages.error(request, "La mesa debe limpiarse antes de sentar un nuevo cliente.")
        return redirect("mesas:mapa")
    pedido = mesa.pedido_activo()
    if pedido is None:
        pedido = Pedido.objects.create(mesa=mesa, mesero=request.user)
        mesa.ocupar()
    elif not _atiende(request, pedido):
        messages.error(request, f"La {mesa} la atiende {pedido.mesero.username}.")
        return redirect("mesas:mapa")
    return redirect("mesas:comanda", pedido_id=pedido.pk)


@requiere_grupo(GRUPO_MESERO)
def comanda(request, pedido_id):
    pedido, respuesta = _pedido_propio(request, pedido_id)
    if respuesta:
        return respuesta
    pedido = Pedido.objects.select_related("mesa", "mesero").prefetch_related("detalles__producto").get(pk=pedido.pk)
    porciones = disponibilidad_menu()
    categorias = list(
        Categoria.objects.filter(activa=True, productos__disponible=True).prefetch_related("productos").distinct()
    )
    for categoria in categorias:
        for producto in categoria.productos.all():
            producto.precio_bs = precio_local_vigente(producto.precio_usd)
            producto.porciones = porciones.get(producto.pk)
            producto.agotado = producto.porciones is not None and producto.porciones <= 0
    facturado = pedido.factura_activa
    return render(
        request,
        "mesas/comanda.html",
        {
            "pedido": pedido,
            "categorias": categorias,
            "facturado": facturado,
            "hay_sin_enviar": pedido.sin_enviar.exists(),
            "detalles": [d for d in pedido.detalles.all() if d.estado != DetallePedido.Estado.ANULADO],
        },
    )


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
        return redirect("mesas:comanda", pedido_id=pedido.pk)
    try:
        producto = Producto.objects.get(pk=request.POST.get("producto_id"), disponible=True)
        cantidad = int(request.POST.get("cantidad") or 1)
        if cantidad < 1:
            raise ValueError
    except (Producto.DoesNotExist, ValueError, TypeError):
        messages.error(request, "Selecciona un platillo y una cantidad válida.")
        return redirect("mesas:comanda", pedido_id=pedido.pk)

    if not TasaCambio.objects.filter(activa=True).exists():
        messages.error(request, "No hay tasa de cambio vigente: pide al cajero o al administrador que la registre.")
        return redirect("mesas:comanda", pedido_id=pedido.pk)

    porciones = disponibilidad_menu().get(producto.pk)
    if porciones is not None and cantidad > porciones:
        if porciones <= 0:
            messages.error(request, f"{producto.nombre} está agotado por falta de insumos.")
        else:
            messages.error(request, f"Solo quedan {porciones} porciones de {producto.nombre}.")
        return redirect("mesas:comanda", pedido_id=pedido.pk)

    DetallePedido.objects.create(
        pedido=pedido,
        producto=producto,
        cantidad=cantidad,
        precio_unitario=precio_local_vigente(producto.precio_usd),
        observaciones=(request.POST.get("observaciones") or "").strip()[:250],
    )
    if pedido.mesa.estado != Mesa.Estado.OCUPADA:
        pedido.mesa.ocupar()
    messages.success(request, f"Se agregó {cantidad} × {producto.nombre}. Recuerda enviarlo a cocina.")
    return redirect("mesas:comanda", pedido_id=pedido.pk)


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
    return redirect("mesas:comanda", pedido_id=pedido.pk)


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
    return redirect("mesas:comanda", pedido_id=pedido.pk)


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
        return redirect("mesas:comanda", pedido_id=pedido.pk)
    return redirect("mesas:mapa")


@requiere_grupo(GRUPO_MESERO)
@require_POST
def anular_pedido(request, pedido_id):
    pedido, respuesta = _pedido_propio(request, pedido_id)
    if respuesta:
        return respuesta
    try:
        pedido.anular(forzar=es_admin(request.user))
        messages.success(request, f"Pedido anulado. {pedido.mesa} quedó disponible.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return redirect("mesas:comanda", pedido_id=pedido.pk)
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
    return redirect("mesas:comanda", pedido_id=pedido.pk)


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
