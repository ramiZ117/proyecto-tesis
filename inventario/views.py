import csv
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from config.formato import num
from config.permisos import GRUPO_CAJA, es_admin, requiere_grupo, texto_validacion
from inventario.models import Insumo, MovimientoStock, Producto
from inventario.servicios import (
    costos_de_platos,
    disponibilidad_menu,
    estado_insumos,
    lista_compra,
    registrar_ajuste,
    registrar_entrada,
    registrar_merma,
    valor_merma_del_mes,
)

ORDEN_NIVEL = {"critico": 0, "reponer": 1, "ok": 2}


def _leer_cantidad(texto) -> Decimal | None:
    try:
        valor = Decimal((texto or "").strip().replace(",", ".")).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError):
        return None
    if not valor.is_finite() or valor < 0 or valor >= Decimal("1000000"):
        return None
    return valor


@requiere_grupo(GRUPO_CAJA)
def stock(request):
    insumos = estado_insumos()
    insumos.sort(key=lambda i: (ORDEN_NIVEL[i.nivel], i.nombre))
    productos = list(
        Producto.objects.filter(disponible=True).select_related("categoria").prefetch_related("recetas__insumo")
    )
    porciones = disponibilidad_menu()
    for p in productos:
        p.porciones = porciones.get(p.pk)
    grupos = [(clave, etiqueta) for clave, etiqueta in Insumo.Grupo.choices if any(i.grupo == clave for i in insumos)]
    compra = lista_compra(insumos)
    tipo = request.GET.get("tipo", "ENT")
    return render(
        request,
        "inventario/stock.html",
        {
            "insumos": insumos,
            "productos": productos,
            "grupos": grupos,
            "movimientos": MovimientoStock.objects.select_related("insumo", "usuario")[:8],
            "criticos": sum(1 for i in insumos if i.critico),
            "por_reponer": sum(1 for i in insumos if i.reponer),
            "valor_inventario": sum((i.valor_usd for i in insumos), Decimal("0.00")),
            "merma_mes": valor_merma_del_mes(),
            "compra": compra,
            "total_compra": sum((f["costo_usd"] for f in compra), Decimal("0.00")),
            "costos": costos_de_platos(productos),
            "motivos_merma": [
                (c, e) for c, e in MovimientoStock.Motivo.choices
                if c not in (MovimientoStock.Motivo.CONTEO, MovimientoStock.Motivo.ANULACION)
            ],
            "tipo_activo": tipo if tipo in ("ENT", "MER", "AJU") else "ENT",
            "insumo_activo": request.GET.get("insumo", ""),
            "puede_ajustar": es_admin(request.user),
        },
    )


def _volver(tipo: str = "", insumo_id: str = ""):
    destino = "/inventario/"
    params = [f"tipo={tipo}" if tipo else "", f"insumo={insumo_id}" if insumo_id else ""]
    params = [p for p in params if p]
    return redirect(destino + ("?" + "&".join(params) if params else "") + "#movimiento")


@requiere_grupo(GRUPO_CAJA)
@require_POST
def movimiento(request):
    """Entrada (compra), merma o ajuste por conteo desde la pantalla de inventario."""
    tipo = request.POST.get("tipo", "ENT")
    insumo_id = request.POST.get("insumo_id", "")
    cantidad = _leer_cantidad(request.POST.get("cantidad"))
    nota = (request.POST.get("nota") or "").strip()[:200]
    try:
        insumo = Insumo.objects.get(pk=insumo_id, activo=True)
    except (Insumo.DoesNotExist, ValueError, TypeError):
        messages.error(request, "Elige un insumo de la lista.")
        return _volver(tipo)
    if cantidad is None:
        messages.error(request, "Indica una cantidad válida.")
        return _volver(tipo, insumo_id)
    try:
        if tipo == "MER":
            mov = registrar_merma(insumo.pk, cantidad, request.POST.get("motivo", ""), nota, request.user)
            messages.success(
                request, f"Merma registrada: −{num(cantidad)} {insumo.abreviatura} de {insumo.nombre} "
                f"({mov.get_motivo_display().lower()})."
            )
        elif tipo == "AJU":
            if not es_admin(request.user):
                messages.error(request, "Solo un administrador puede ajustar el stock por conteo.")
                return _volver(tipo, insumo_id)
            registrar_ajuste(insumo.pk, cantidad, nota, request.user)
            messages.success(request, f"Conteo guardado: {insumo.nombre} queda en {num(cantidad)} {insumo.abreviatura}.")
        else:
            costo = _leer_cantidad(request.POST.get("costo"))
            registrar_entrada(insumo.pk, cantidad, nota, request.user, costo if costo is not None and costo > 0 else None)
            messages.success(request, f"Entrada registrada: +{num(cantidad)} {insumo.abreviatura} de {insumo.nombre}.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return _volver(tipo, insumo_id)
    return _volver()


@requiere_grupo(GRUPO_CAJA)
@require_POST
def entrada(request):
    """Ruta histórica de la reposición: equivale a un movimiento de tipo Entrada."""
    try:
        cantidad = Decimal(request.POST.get("cantidad", "0").strip().replace(",", ".")).quantize(Decimal("0.001"))
        if not cantidad.is_finite() or cantidad <= 0 or cantidad >= Decimal("1000000"):
            raise InvalidOperation
        insumo = Insumo.objects.get(pk=request.POST.get("insumo_id"), activo=True)
    except (InvalidOperation, TypeError, Insumo.DoesNotExist, ValueError):
        messages.error(request, "Elige un insumo y una cantidad mayor a cero.")
        return redirect("inventario:stock")
    registrar_entrada(insumo.pk, cantidad, request.POST.get("nota", "").strip()[:200], request.user)
    messages.success(request, f"Entrada registrada: +{num(cantidad)} {insumo.abreviatura} de {insumo.nombre}.")
    return redirect("inventario:stock")


@requiere_grupo(GRUPO_CAJA)
def compra_csv(request):
    """Lista de compra sugerida agrupada por proveedor, lista para enviar."""
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="compra-{timezone.localdate()}.csv"'
    respuesta.write("﻿")
    escritor = csv.writer(respuesta, delimiter=";")
    escritor.writerow(["Proveedor", "Insumo", "Cantidad", "Unidad", "Costo estimado (USD)"])
    for fila in lista_compra():
        i = fila["insumo"]
        escritor.writerow([fila["proveedor"], i.nombre, f"{fila['cantidad'].normalize():f}", i.abreviatura, fila["costo_usd"]])
    return respuesta
