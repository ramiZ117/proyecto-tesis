import csv
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from config.formato import num
from config.permisos import es_admin, requiere_admin, texto_validacion
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
from inventario.servicios import auditar_inventario, kardex as calcular_kardex

ORDEN_NIVEL = {"critico": 0, "reponer": 1, "ok": 2}


def _leer_cantidad(texto) -> Decimal | None:
    try:
        valor = Decimal((texto or "").strip().replace(",", ".")).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError):
        return None
    if not valor.is_finite() or valor < 0 or valor >= Decimal("1000000"):
        return None
    return valor


@requiere_admin
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


@requiere_admin
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
            documento = " ".join((request.POST.get("documento") or "").split())
            if len(documento) > 60:
                messages.error(request, "El número de documento es demasiado largo (máximo 60 caracteres).")
                return _volver(tipo, insumo_id)
            registrar_entrada(insumo.pk, cantidad, nota, request.user, costo if costo is not None and costo > 0 else None,
                              documento=documento)
            messages.success(request, f"Entrada registrada: +{num(cantidad)} {insumo.abreviatura} de {insumo.nombre}.")
    except ValidationError as exc:
        messages.error(request, texto_validacion(exc))
        return _volver(tipo, insumo_id)
    return _volver()


@requiere_admin
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


@requiere_admin
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



# ---- movimientos de inventario (auditoría) y kárdex ---------------------------------------------
POR_PAGINA = 50
MAX_DIAS_CONSULTA = 366


def _rango_fechas(request, por_defecto_dias: int = 30):
    """Lee ?desde=&hasta= (AAAA-MM-DD). Devuelve (desde, hasta, inicio_dt, fin_dt, error)."""
    from datetime import datetime, time, timedelta

    from django.utils.dateparse import parse_date

    hoy = timezone.localdate()
    texto_desde, texto_hasta = request.GET.get("desde", ""), request.GET.get("hasta", "")
    def leer(texto, defecto):
        if not texto:
            return defecto
        try:
            return parse_date(texto)
        except ValueError:  # bien escrita pero imposible, p. ej. mes 13
            return None

    desde = leer(texto_desde, hoy - timedelta(days=por_defecto_dias - 1))
    hasta = leer(texto_hasta, hoy)
    error = ""
    if desde is None or hasta is None:
        error = "Fecha no válida: usa el calendario."
        desde, hasta = hoy - timedelta(days=por_defecto_dias - 1), hoy
    elif desde > hasta:
        error = "La fecha «desde» no puede ser posterior a «hasta»."
        desde, hasta = hasta, desde
    elif (hasta - desde).days > MAX_DIAS_CONSULTA:
        error = f"Consulta como máximo {MAX_DIAS_CONSULTA} días; se acotó el rango."
        desde = hasta - timedelta(days=MAX_DIAS_CONSULTA)
    if hasta > hoy:
        hasta = hoy
    inicio = timezone.make_aware(datetime.combine(desde, time.min))
    fin = timezone.make_aware(datetime.combine(hasta + timedelta(days=1), time.min))
    return desde, hasta, inicio, fin, error


def _filtrar_movimientos(request):
    from django.contrib.auth.models import User
    from django.db.models import Q

    desde, hasta, inicio, fin, error = _rango_fechas(request)
    filtros = {"desde": desde, "hasta": hasta, "insumo": "", "tipo": "", "motivo": "", "usuario": "", "q": ""}
    consulta = (MovimientoStock.objects.filter(creado_en__gte=inicio, creado_en__lt=fin)
                .select_related("insumo", "usuario", "detalle_pedido__producto"))
    insumo = request.GET.get("insumo", "")
    if insumo.isdigit():
        consulta = consulta.filter(insumo_id=int(insumo))
        filtros["insumo"] = int(insumo)
    tipo = request.GET.get("tipo", "")
    if tipo in MovimientoStock.Tipo.values:
        consulta = consulta.filter(tipo=tipo)
        filtros["tipo"] = tipo
    motivo = request.GET.get("motivo", "")
    if motivo in MovimientoStock.Motivo.values:
        consulta = consulta.filter(motivo=motivo)
        filtros["motivo"] = motivo
    usuario = request.GET.get("usuario", "")
    if usuario == "sistema":
        consulta = consulta.filter(usuario__isnull=True)
        filtros["usuario"] = usuario
    elif usuario.isdigit():
        consulta = consulta.filter(usuario_id=int(usuario))
        filtros["usuario"] = int(usuario)
    q = " ".join((request.GET.get("q") or "").split())[:60]
    if q:
        consulta = consulta.filter(Q(nota__icontains=q) | Q(documento__icontains=q) | Q(insumo__nombre__icontains=q))
        filtros["q"] = q
    usuarios = User.objects.filter(movimientos_stock__isnull=False).distinct().order_by("username")
    return consulta, filtros, error, usuarios


@requiere_admin
def movimientos(request):
    """Registro completo de lo que entra y sale del inventario, con filtros, totales y exportación."""
    from django.core.paginator import Paginator
    from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum

    consulta, filtros, error, usuarios = _filtrar_movimientos(request)
    if error:
        messages.warning(request, error)
    if request.GET.get("formato") == "csv":
        return _movimientos_csv(consulta.order_by("creado_en", "id"), filtros)
    valor = ExpressionWrapper(F("cantidad") * F("costo_unitario"), output_field=DecimalField(max_digits=18, decimal_places=4))
    resumen = {fila["tipo"]: fila for fila in consulta.values("tipo").annotate(n=Count("id"), valor=Sum(valor))}
    tarjetas = [
        {"tipo": clave, "etiqueta": etiqueta, "n": resumen.get(clave, {}).get("n", 0),
         "valor": (resumen.get(clave, {}).get("valor") or Decimal("0")).quantize(Decimal("0.01"))}
        for clave, etiqueta in MovimientoStock.Tipo.choices
    ]
    pagina = Paginator(consulta.order_by("-creado_en", "-id"), POR_PAGINA).get_page(request.GET.get("pagina"))
    parametros = request.GET.copy()
    parametros.pop("pagina", None)
    return render(request, "inventario/movimientos.html", {
        "pagina": pagina, "filtros": filtros, "tarjetas": tarjetas, "total": pagina.paginator.count,
        "insumos": Insumo.objects.order_by("nombre"), "usuarios": usuarios,
        "tipos": MovimientoStock.Tipo.choices, "motivos": MovimientoStock.Motivo.choices,
        "parametros": parametros.urlencode(), "auditoria": auditar_inventario(),
    })


def _movimientos_csv(consulta, filtros):
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="movimientos_{filtros["desde"]:%Y%m%d}_{filtros["hasta"]:%Y%m%d}.csv"'
    respuesta.write("\ufeff")  # Excel reconoce los acentos
    escritor = csv.writer(respuesta, delimiter=";")
    escritor.writerow(["Fecha", "Hora", "Insumo", "Unidad", "Tipo", "Motivo", "Variación", "Existencia anterior",
                       "Existencia resultante", "Costo unitario USD", "Valor USD", "Documento", "Usuario", "Nota"])
    for m in consulta.iterator():
        local = timezone.localtime(m.creado_en)
        escritor.writerow([
            local.strftime("%d/%m/%Y"), local.strftime("%H:%M"), _celda(m.insumo.nombre), m.insumo.abreviatura,
            m.get_tipo_display(), m.get_motivo_display() if m.motivo else "", num(m.variacion),
            num(m.stock_anterior) if m.stock_anterior is not None else "", num(m.stock_resultante),
            num(m.costo_unitario, 4), num(m.valor_usd, 2), _celda(m.documento),
            m.usuario.username if m.usuario else "Sistema", _celda(m.nota),
        ])
    return respuesta


def _celda(texto: str) -> str:
    """Evita que Excel ejecute fórmulas escritas en una nota (=, +, -, @)."""
    texto = texto or ""
    return "'" + texto if texto[:1] in ("=", "+", "-", "@") else texto


@requiere_admin
def kardex(request, insumo_id):
    """Kárdex del insumo: existencia inicial, cada entrada y salida con su saldo, y existencia final."""
    from datetime import timedelta

    from django.shortcuts import get_object_or_404

    insumo = get_object_or_404(Insumo.objects.select_related("proveedor"), pk=insumo_id)
    desde, hasta, inicio, fin, error = _rango_fechas(request, por_defecto_dias=30)
    if error:
        messages.warning(request, error)
    datos = calcular_kardex(insumo, inicio, fin)
    hoy = timezone.localdate()
    cuadra = None
    if hasta >= hoy:  # al día de hoy el saldo del kárdex debe ser el stock registrado
        cuadra = datos["final"] == insumo.stock_actual
    if request.GET.get("formato") == "csv":
        respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
        respuesta["Content-Disposition"] = f'attachment; filename="kardex_{insumo.pk}_{desde:%Y%m%d}_{hasta:%Y%m%d}.csv"'
        respuesta.write("\ufeff")
        escritor = csv.writer(respuesta, delimiter=";")
        escritor.writerow([f"Kárdex de {insumo.nombre} ({insumo.abreviatura})", f"{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}"])
        escritor.writerow(["Fecha", "Tipo", "Documento", "Detalle", "Entrada", "Salida", "Saldo", "Costo unitario USD", "Usuario"])
        escritor.writerow([f"{desde:%d/%m/%Y}", "Existencia inicial", "", "", "", "", num(datos["inicial"]), "", ""])
        for f in datos["filas"]:
            m = f["mov"]
            escritor.writerow([timezone.localtime(m.creado_en).strftime("%d/%m/%Y %H:%M"), m.get_tipo_display(), _celda(m.documento),
                               _celda(m.nota), num(f["entrada"]) if f["entrada"] else "", num(f["salida"]) if f["salida"] else "",
                               num(f["saldo"]), num(m.costo_unitario, 4), m.usuario.username if m.usuario else "Sistema"])
        escritor.writerow(["", "Totales", "", "", num(datos["entradas"]), num(datos["salidas"]), num(datos["final"]), "", ""])
        return respuesta
    return render(request, "inventario/kardex.html", {
        "insumo": insumo, "desde": desde, "hasta": hasta, "k": datos, "cuadra": cuadra,
        "insumos": Insumo.objects.order_by("nombre"),
        "atajos": {"7 días": (hoy - timedelta(days=6), hoy), "30 días": (hoy - timedelta(days=29), hoy),
                   "Este mes": (hoy.replace(day=1), hoy)},
    })
