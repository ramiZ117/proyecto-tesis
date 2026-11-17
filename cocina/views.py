from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from cocina.models import PedidoKDS
from config.permisos import GRUPO_COCINA, requiere_grupo, texto_validacion
from inventario.models import Categoria, Insumo
from inventario.servicios import estado_insumos
from mesas.models import DetallePedido

COLUMNAS = [
    ("pen", "Nuevos", "Sin pedidos nuevos."),
    ("prep", "Preparando", "Nada en preparación."),
    ("listo", "Listos para servir", "Nada esperando al mesero."),
]


def _estacion(request) -> str:
    valor = request.GET.get("estacion", "")
    return valor if valor in Categoria.Estacion.values else ""


def _contexto(request):
    estacion = _estacion(request)
    pedidos = PedidoKDS.objects.en_cola(estacion or None)
    columnas = [
        {"clave": clave, "titulo": titulo, "vacio": vacio, "pedidos": [p for p in pedidos if p.etapa == clave]}
        for clave, titulo, vacio in COLUMNAS
    ]
    parcial = reverse("cocina:kds_parcial") + (f"?estacion={estacion}" if estacion else "")
    return {
        "pedidos": pedidos,
        "columnas": columnas,
        "estacion": estacion,
        "estaciones": [("", "Todo")] + list(Categoria.Estacion.choices),
        "url_parcial": parcial,
        "nuevos": sum(1 for p in pedidos if p.etapa == "pen"),
        "minutos_alerta": settings.KDS_MINUTOS_ALERTA,
        "minutos_critico": settings.KDS_MINUTOS_CRITICO,
    }


@requiere_grupo(GRUPO_COCINA)
def kds(request):
    return render(request, "cocina/kds.html", _contexto(request))


@requiere_grupo(GRUPO_COCINA)
def kds_parcial(request):
    return render(request, "cocina/_tickets.html", _contexto(request))


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
    destino = reverse("cocina:kds")
    estacion = request.POST.get("estacion", "")
    if estacion in Categoria.Estacion.values:
        destino += f"?estacion={estacion}"
    return redirect(destino)
