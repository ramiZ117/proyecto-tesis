from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from cocina.models import PedidoKDS
from config.permisos import GRUPO_COCINA, requiere_grupo, texto_validacion
from inventario.models import Insumo
from inventario.servicios import estado_insumos
from mesas.models import DetallePedido


@requiere_grupo(GRUPO_COCINA)
def kds(request):
    return render(request, "cocina/kds.html", {"pedidos": PedidoKDS.objects.en_cola()})


@requiere_grupo(GRUPO_COCINA)
def kds_parcial(request):
    return render(request, "cocina/_tickets.html", {"pedidos": PedidoKDS.objects.en_cola()})


@requiere_grupo(GRUPO_COCINA)
@require_POST
def cambiar_estado(request, detalle_id):
    detalle = get_object_or_404(DetallePedido.objects.select_related("producto"), pk=detalle_id)
    accion = request.POST.get("accion")
    try:
        if accion == "preparar":
            detalle.marcar_en_preparacion()
            messages.success(request, f"{detalle.producto.nombre} en preparación.")
            # El stock real baja al cobrar; la alerta usa lo disponible (stock menos lo ya pedido).
            for insumo in estado_insumos(Insumo.objects.filter(recetas__producto=detalle.producto).distinct()):
                if insumo.disponible < 0:
                    messages.warning(
                        request,
                        f"Alerta: el stock registrado de {insumo.nombre} no cubre lo pedido "
                        f"(hay {insumo.stock_actual} {insumo.get_unidad_display()}). Revisa el inventario.",
                    )
                elif insumo.critico:
                    messages.warning(
                        request,
                        f"Alerta: {insumo.nombre} está en nivel crítico "
                        f"({insumo.disponible} {insumo.get_unidad_display()} disponibles).",
                    )
        elif accion == "listo":
            detalle.marcar_listo()
            messages.success(request, f"{detalle.producto.nombre} listo para servir.")
        else:
            messages.error(request, "Acción de cocina no válida.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return redirect("cocina:kds")
