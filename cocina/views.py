from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from cocina.models import AREA_COCINA, AREA_SHAWARMA, AvisoCocina, PedidoKDS, filtro_area
from config.permisos import GRUPO_COCINA, GRUPO_SHAWARMA, en_grupo, requiere_grupo, texto_validacion
from inventario.models import Categoria, Insumo
from inventario.servicios import estado_insumos
from mesas.models import DetallePedido, Pedido

COLUMNAS = [
    ("pen", "Nuevos", "Sin pedidos nuevos."),
    ("prep", "Preparando", "Nada en preparación."),
    ("listo", "Listos para servir", "Nada esperando al mesero."),
]

# Cada área tiene su pantalla: la cocina (platos, entradas, jugos…) y el shawarmero (solo shawarmas).
AREAS = {
    AREA_COCINA: {"grupo": GRUPO_COCINA, "titulo": "Cocina", "vista": "cocina:kds", "parcial": "cocina:kds_parcial"},
    AREA_SHAWARMA: {"grupo": GRUPO_SHAWARMA, "titulo": "Shawarmas", "vista": "cocina:shawarma",
                    "parcial": "cocina:shawarma_parcial"},
}
ESTACIONES_COCINA = [(c, n) for c, n in Categoria.Estacion.choices if c != Categoria.Estacion.SHAWARMA]


def _area(valor) -> str:
    return valor if valor in AREAS else AREA_COCINA


def _exigir_area(user, area: str) -> None:
    if not en_grupo(user, AREAS[area]["grupo"]):
        raise PermissionDenied


def _estacion(request, area: str) -> str:
    valor = request.GET.get("estacion", "") or request.POST.get("estacion", "")
    return valor if area == AREA_COCINA and valor in dict(ESTACIONES_COCINA) else ""


def _destino(area: str, estacion: str = "") -> str:
    return reverse(AREAS[area]["vista"]) + (f"?estacion={estacion}" if estacion else "")


def _contexto(request, area: str):
    estacion = _estacion(request, area)
    pedidos = PedidoKDS.objects.en_cola(estacion or None, area=area)
    columnas = [
        {"clave": clave, "titulo": titulo, "vacio": vacio, "pedidos": [p for p in pedidos if p.etapa == clave]}
        for clave, titulo, vacio in COLUMNAS
    ]
    parcial = reverse(AREAS[area]["parcial"]) + (f"?estacion={estacion}" if estacion else "")
    return {
        "area": area,
        "titulo_area": AREAS[area]["titulo"],
        "pedidos": pedidos,
        "columnas": columnas,
        "estacion": estacion,
        "estaciones": [("", "Todo")] + ESTACIONES_COCINA if area == AREA_COCINA else [],
        "url_parcial": parcial,
        "nuevos": sum(1 for p in pedidos if p.etapa == "pen"),
        "avisos": list(AvisoCocina.pendientes(area).select_related("pedido")[:6]),
        "minutos_alerta": settings.KDS_MINUTOS_ALERTA,
        "minutos_critico": settings.KDS_MINUTOS_CRITICO,
    }


@requiere_grupo(GRUPO_COCINA)
def kds(request):
    return render(request, "cocina/kds.html", _contexto(request, AREA_COCINA))


@requiere_grupo(GRUPO_COCINA)
def kds_parcial(request):
    return render(request, "cocina/_tickets.html", _contexto(request, AREA_COCINA))


@requiere_grupo(GRUPO_SHAWARMA)
def shawarma(request):
    return render(request, "cocina/kds.html", _contexto(request, AREA_SHAWARMA))


@requiere_grupo(GRUPO_SHAWARMA)
def shawarma_parcial(request):
    return render(request, "cocina/_tickets.html", _contexto(request, AREA_SHAWARMA))


@requiere_grupo(GRUPO_COCINA, GRUPO_SHAWARMA)
@require_POST
def atender_aviso(request, aviso_id):
    aviso = get_object_or_404(AvisoCocina, pk=aviso_id)
    _exigir_area(request.user, aviso.area)
    aviso.atender(request.user)
    return redirect(_destino(aviso.area, _estacion(request, aviso.area)))


def _alertas_de_stock(request, productos) -> None:
    """Avisa si lo que se empieza a cocinar deja algún insumo sin cubrir o en nivel crítico."""
    vistos = set()
    for insumo in estado_insumos(Insumo.objects.filter(recetas__producto__in=productos).distinct()):
        if insumo.pk in vistos:
            continue
        vistos.add(insumo.pk)
        if insumo.disponible < 0:
            messages.warning(request, f"Alerta: el stock registrado de {insumo.nombre} no cubre lo pedido. Avisa a caja.")
        elif insumo.critico:
            messages.warning(request, f"Alerta: {insumo.nombre} está en nivel crítico. Avisa a caja para reponer.")


@requiere_grupo(GRUPO_COCINA, GRUPO_SHAWARMA)
@require_POST
def cambiar_estado(request, detalle_id):
    """Avance de un solo plato (se conserva para compatibilidad; la pantalla usa un botón por pedido)."""
    detalle = get_object_or_404(DetallePedido.objects.select_related("producto__categoria"), pk=detalle_id)
    area = AREA_SHAWARMA if detalle.producto.area == AREA_SHAWARMA else AREA_COCINA
    _exigir_area(request.user, area)
    accion = request.POST.get("accion")
    try:
        if accion == "preparar":
            detalle.marcar_en_preparacion()
            messages.success(request, f"{detalle.producto.nombre} en preparación.")
            _alertas_de_stock(request, [detalle.producto_id])
        elif accion == "listo":
            detalle.marcar_listo()
            messages.success(request, f"{detalle.producto.nombre} listo para servir.")
        else:
            messages.error(request, "Acción de cocina no válida.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return redirect(_destino(area, _estacion(request, area)))


@requiere_grupo(GRUPO_COCINA, GRUPO_SHAWARMA)
@require_POST
def avanzar_pedido(request, pedido_id):
    """Un solo botón por pedido: «Empezar» pasa sus platos nuevos a preparación y «Listo» los termina.

    Solo toca los platos del área de quien lo pulsa (la cocina no mueve los shawarmas ni al revés) y,
    si la pantalla está filtrada por estación, solo los de esa estación.
    """
    area = _area(request.POST.get("area"))
    _exigir_area(request.user, area)
    pedido = get_object_or_404(Pedido, pk=pedido_id)
    accion = request.POST.get("accion")
    estacion = _estacion(request, area)
    platos = pedido.detalles.filter(enviado=True).filter(filtro_area(area)).select_related("producto")
    if estacion:
        platos = platos.filter(producto__categoria__estacion=estacion)
    try:
        if accion == "empezar":
            pendientes = list(platos.filter(estado=DetallePedido.Estado.PENDIENTE))
            if not pendientes:
                raise ValidationError("No hay platos nuevos por empezar en este pedido.")
            for d in pendientes:
                d.marcar_en_preparacion()
            messages.success(request, f"{pedido.etiqueta}: {len(pendientes)} plato{'s' if len(pendientes) != 1 else ''} en preparación.")
            _alertas_de_stock(request, [d.producto_id for d in pendientes])
        elif accion == "listo":
            en_marcha = list(platos.filter(estado=DetallePedido.Estado.EN_PREPARACION))
            if not en_marcha:
                raise ValidationError("No hay platos en preparación en este pedido.")
            for d in en_marcha:
                d.marcar_listo()
            messages.success(request, f"{pedido.etiqueta} listo para servir.")
        else:
            messages.error(request, "Acción de cocina no válida.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
    return redirect(_destino(area, estacion))
