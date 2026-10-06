from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from config.permisos import GRUPO_CAJA, requiere_grupo
from inventario.models import Insumo, MovimientoStock, Producto
from inventario.servicios import disponibilidad_menu, estado_insumos, registrar_entrada


@requiere_grupo(GRUPO_CAJA)
def stock(request):
    insumos = estado_insumos()
    insumos.sort(key=lambda i: (not i.critico, i.nombre))
    productos = list(
        Producto.objects.filter(disponible=True).select_related("categoria").prefetch_related("recetas__insumo")
    )
    porciones = disponibilidad_menu()
    for p in productos:
        p.porciones = porciones.get(p.pk)
    return render(
        request,
        "inventario/stock.html",
        {
            "insumos": insumos,
            "productos": productos,
            "movimientos": MovimientoStock.objects.select_related("insumo")[:15],
            "criticos": sum(1 for i in insumos if i.critico),
        },
    )


@requiere_grupo(GRUPO_CAJA)
@require_POST
def entrada(request):
    try:
        cantidad = Decimal(request.POST.get("cantidad", "0").strip().replace(",", ".")).quantize(Decimal("0.001"))
        if not cantidad.is_finite() or cantidad <= 0 or cantidad >= Decimal("1000000"):
            raise InvalidOperation
        insumo = Insumo.objects.get(pk=request.POST.get("insumo_id"), activo=True)
    except (InvalidOperation, TypeError, Insumo.DoesNotExist, ValueError):
        messages.error(request, "Elige un insumo y una cantidad mayor a cero.")
        return redirect("inventario:stock")
    registrar_entrada(insumo.pk, cantidad, request.POST.get("nota", "").strip()[:200])
    messages.success(request, f"Entrada registrada: +{cantidad} {insumo.get_unidad_display()} de {insumo.nombre}.")
    return redirect("inventario:stock")
