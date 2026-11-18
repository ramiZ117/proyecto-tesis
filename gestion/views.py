"""Administración del restaurante: carta, recetas, insumos, proveedores, mesas y menú digital.

Reemplaza al panel técnico de Django con pantallas propias, con el mismo diseño del sistema.
Solo el administrador entra a este módulo.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from config.formato import num
from config.permisos import requiere_admin
from gestion.forms import (
    AjustesLocalForm, CategoriaForm, GastoExtraordinarioForm, GastoFijoForm, InsumoForm, MesaForm, ProductoForm, PromocionForm, PromocionItemFormSet, ProveedorForm, RecetaFormSet,
)
from inventario.models import Categoria, Insumo, Producto, Promocion, Proveedor, Receta
from inventario.servicios import disponibilidad_menu, registrar_entrada
from mesas.models import Mesa


# ---- carta ------------------------------------------------------------------------------
@requiere_admin
def carta(request):
    categorias = list(
        Categoria.objects.order_by("-activa", "orden", "nombre").prefetch_related(
            Prefetch("productos", queryset=Producto.objects.prefetch_related("recetas__insumo").order_by("nombre"))
        )
    )
    porciones = disponibilidad_menu()
    for categoria in categorias:
        for p in categoria.productos.all():
            p.porciones = porciones.get(p.pk)
            costo = sum((r.cantidad * r.insumo.costo_unitario for r in p.recetas.all()), Decimal("0"))
            p.costo = costo.quantize(Decimal("0.01"))
            p.margen = int(round((p.precio_usd - costo) / p.precio_usd * 100)) if p.precio_usd and p.recetas.all() else None
    return render(request, "gestion/carta.html", {
        "categorias": categorias,
        "total_platos": sum(len(c.productos.all()) for c in categorias),
        "sin_foto": Producto.objects.filter(disponible=True, foto="").count(),
    })


@requiere_admin
def plato(request, producto_id=None):
    producto = get_object_or_404(Producto, pk=producto_id) if producto_id else Producto()
    form = ProductoForm(request.POST or None, request.FILES or None, instance=producto)
    receta = RecetaFormSet(request.POST or None, instance=producto, prefix="receta")
    if request.method == "POST":
        if form.is_valid() and receta.is_valid():
            with transaction.atomic():
                producto = form.save()
                receta.instance = producto
                receta.save()
            messages.success(request, f"{producto.nombre} guardado.")
            return redirect("gestion:carta")
        messages.error(request, "Revisa los campos marcados.")
    return render(request, "gestion/plato.html", {"form": form, "receta": receta, "producto": producto})


@requiere_admin
@require_POST
def plato_disponible(request, producto_id):
    producto = get_object_or_404(Producto, pk=producto_id)
    producto.disponible = not producto.disponible
    producto.save(update_fields=["disponible"])
    estado = "vuelve a la carta" if producto.disponible else "sale de la carta"
    messages.success(request, f"{producto.nombre} {estado}.")
    return redirect(reverse("gestion:carta") + f"#cat-{producto.categoria_id}")


@requiere_admin
def categoria(request, categoria_id=None):
    instancia = get_object_or_404(Categoria, pk=categoria_id) if categoria_id else Categoria()
    form = CategoriaForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Categoría {form.instance.nombre} guardada.")
        return redirect("gestion:carta")
    return render(request, "gestion/form_simple.html", {
        "form": form, "titulo": f"Categoría {instancia.nombre}" if instancia.pk else "Nueva categoría",
        "volver": reverse("gestion:carta"), "subtitulo": "Las categorías ordenan la carta y deciden la estación de cocina",
    })


# ---- insumos ----------------------------------------------------------------------------
@requiere_admin
def insumos(request):
    lista = Insumo.objects.select_related("proveedor").order_by("-activo", "nombre")
    return render(request, "gestion/insumos.html", {"insumos": lista})


@requiere_admin
def insumo(request, insumo_id=None):
    instancia = get_object_or_404(Insumo, pk=insumo_id) if insumo_id else Insumo()
    form = InsumoForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            nuevo = instancia.pk is None
            guardado = form.save()
            inicial = form.cleaned_data.get("stock_inicial") if nuevo else None
            if inicial:
                registrar_entrada(guardado.pk, inicial, "Stock inicial", request.user)
        messages.success(request, f"Insumo {guardado.nombre} guardado.")
        return redirect("gestion:insumos")
    return render(request, "gestion/form_simple.html", {
        "form": form, "titulo": instancia.nombre if instancia.pk else "Nuevo insumo", "volver": reverse("gestion:insumos"),
        "subtitulo": f"Stock actual: {num(instancia.stock_actual)} {instancia.abreviatura} · cambia con entradas, mermas o ajustes en Inventario"
        if instancia.pk else "Ficha del insumo y sus niveles de stock",
    })


# ---- proveedores ------------------------------------------------------------------------
@requiere_admin
def proveedores(request):
    lista = Proveedor.objects.annotate(n_insumos=Count("insumos", filter=Q(insumos__activo=True))).order_by("-activo", "nombre")
    return render(request, "gestion/proveedores.html", {"proveedores": lista})


@requiere_admin
def proveedor(request, proveedor_id=None):
    instancia = get_object_or_404(Proveedor, pk=proveedor_id) if proveedor_id else Proveedor()
    form = ProveedorForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Proveedor {form.instance.nombre} guardado.")
        return redirect("gestion:proveedores")
    return render(request, "gestion/form_simple.html", {
        "form": form, "titulo": instancia.nombre if instancia.pk else "Nuevo proveedor",
        "volver": reverse("gestion:proveedores"), "subtitulo": "Datos de contacto para la lista de compra",
    })


# ---- mesas ------------------------------------------------------------------------------
@requiere_admin
def mesas(request):
    todas = list(Mesa.objects.order_by("-activa", "numero"))
    activas = [m for m in todas if m.activa]
    columnas = max((m.pos_x + m.ancho for m in activas), default=1)
    return render(request, "gestion/mesas.html", {
        "mesas": todas, "activas": activas, "columnas": max(columnas, 1),
        "capacidad_total": sum(m.capacidad for m in activas),
    })


@requiere_admin
def mesa(request, mesa_id=None):
    instancia = get_object_or_404(Mesa, pk=mesa_id) if mesa_id else Mesa()
    if not instancia.pk and request.method == "GET":
        ultima = Mesa.objects.order_by("-numero").first()
        instancia.numero = (ultima.numero + 1) if ultima else 1
        instancia.pos_x = instancia.pos_y = 0
    form = MesaForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{form.instance} guardada.")
        return redirect("gestion:mesas")
    if not instancia.pk and not form.is_bound:
        form.initial.update({"pos_x": 1, "pos_y": 1})
    return render(request, "gestion/form_simple.html", {
        "form": form, "titulo": str(instancia) if instancia.pk else "Nueva mesa", "volver": reverse("gestion:mesas"),
        "subtitulo": "Retira una mesa desmarcando «En servicio»: el historial de sus comandas se conserva",
    })


# ---- menú digital -----------------------------------------------------------------------
def _qr_svg(texto: str) -> str:
    import qrcode
    import qrcode.image.svg

    imagen = qrcode.make(texto, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    return imagen.to_string(encoding="unicode")


def url_menu(request, mesa=None) -> str:
    base = settings.MENU_URL_PUBLICA or request.build_absolute_uri("/").rstrip("/")
    from menu.mesa import codigo

    return f"{base}{reverse('menu:carta')}" + (f"?mesa={mesa}&c={codigo(mesa)}" if mesa else "")


@requiere_admin
def menu_digital(request):
    from menu.asistente import proveedor_activo

    enlace = url_menu(request)
    mesas_qr = [{"mesa": m, "url": url_menu(request, m.numero), "svg": _qr_svg(url_menu(request, m.numero))}
                for m in Mesa.objects.filter(activa=True).order_by("numero")]
    return render(request, "gestion/menu_digital.html", {
        "enlace": enlace, "qr_general": _qr_svg(enlace), "mesas_qr": mesas_qr,
        "ia": proveedor_activo(), "url_configurada": bool(settings.MENU_URL_PUBLICA),
        "platos": Producto.objects.filter(disponible=True).count(),
        "sin_foto": Producto.objects.filter(disponible=True, foto="").count(),
    })


# ---- promociones ------------------------------------------------------------------------
@requiere_admin
def promociones(request):
    lista = list(Promocion.objects.prefetch_related("items__producto").order_by("-activa", "orden", "nombre"))
    hoy = timezone.localdate()
    for promo in lista:
        promo.vigente_hoy = promo.activa and (not promo.desde or promo.desde <= hoy) and (not promo.hasta or promo.hasta >= hoy)
    return render(request, "gestion/promociones.html", {"promociones": lista})


@requiere_admin
def promocion(request, promocion_id=None):
    instancia = get_object_or_404(Promocion, pk=promocion_id) if promocion_id else Promocion()
    form = PromocionForm(request.POST or None, request.FILES or None, instance=instancia)
    items = PromocionItemFormSet(request.POST or None, instance=instancia, prefix="items")
    if request.method == "POST":
        if form.is_valid() and items.is_valid():
            with transaction.atomic():
                guardada = form.save()
                items.instance = guardada
                items.save()
            if guardada.precio_usd >= guardada.precio_normal_usd:
                messages.warning(request, f"{guardada.nombre} guardada, pero su precio no es menor que pedir los platos por separado (${guardada.precio_normal_usd}).")
            else:
                messages.success(request, f"Promoción {guardada.nombre} guardada: el cliente ahorra ${guardada.ahorro_usd}.")
            return redirect("gestion:promociones")
        messages.error(request, "Revisa los campos marcados.")
    return render(request, "gestion/promocion.html", {"form": form, "items": items, "promo": instancia})


@requiere_admin
@require_POST
def promocion_activa(request, promocion_id):
    promo = get_object_or_404(Promocion, pk=promocion_id)
    promo.activa = not promo.activa
    promo.save(update_fields=["activa"])
    messages.success(request, f"{promo.nombre} {'está visible' if promo.activa else 'quedó oculta'}.")
    return redirect("gestion:promociones")



# ---- datos de cobro y delivery ----------------------------------------------------------
@requiere_admin
def ajustes(request):
    from gestion.models import AjustesLocal

    instancia = AjustesLocal.actual()
    form = AjustesLocalForm(request.POST or None, instance=instancia)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "Datos guardados: el menú digital ya los muestra.")
            return redirect("gestion:ajustes")
        messages.error(request, "Revisa los campos marcados.")
    return render(request, "gestion/ajustes.html", {"form": form, "ajustes": instancia})



# ---- finanzas: ganancia real, inversión y gastos ---------------------------------------------
MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]


def _mes_pedido(request):
    hoy = timezone.localdate()
    texto = request.GET.get("mes", "")
    try:
        anio, mes = (int(x) for x in texto.split("-"))
        if not (2000 <= anio <= hoy.year and 1 <= mes <= 12) or (anio, mes) > (hoy.year, hoy.month):
            raise ValueError
    except ValueError:
        anio, mes = hoy.year, hoy.month
    return anio, mes


@requiere_admin
def finanzas(request):
    import csv

    from django.http import HttpResponse

    from gestion.finanzas import inversion_actual, rango_mes, rentabilidad_por_plato, resultado_mes
    from gestion.models import GastoExtraordinario, GastoFijo

    anio, mes = _mes_pedido(request)
    resultado = resultado_mes(anio, mes)
    _, _, desde, hasta = rango_mes(anio, mes)
    platos = rentabilidad_por_plato(desde, hasta)
    nombre_mes = f"{MESES[mes]} {anio}"
    if request.GET.get("formato") == "csv":
        respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
        respuesta["Content-Disposition"] = f'attachment; filename="resultado_{anio}_{mes:02d}.csv"'
        respuesta.write("\ufeff")
        e = csv.writer(respuesta, delimiter=";")
        e.writerow([f"Estado de resultados · {nombre_mes}", "USD"])
        for etiqueta, clave in (("Ventas sin IVA", "ventas"), ("Costo de lo vendido", "costo_ventas"),
                                ("Ganancia bruta", "ganancia_bruta"), ("Mermas", "mermas"),
                                ("Gastos fijos", "total_fijos"), ("Gastos extraordinarios (cuota del mes)", "total_extraordinarios"),
                                ("Ganancia neta", "ganancia_neta")):
            e.writerow([etiqueta, num(resultado[clave], 2)])
        e.writerow([])
        e.writerow(["Plato", "Unidades", "Ventas sin IVA", "Costo", "Ganancia", "Margen %"])
        for f in platos:
            e.writerow([f["producto"].nombre, f["unidades"], num(f["ventas"], 2), num(f["costo"], 2), num(f["ganancia"], 2),
                        f["margen"] if f["margen"] is not None else ""])
        return respuesta
    hoy = timezone.localdate()
    anterior = (anio, mes - 1) if mes > 1 else (anio - 1, 12)
    siguiente = (anio, mes + 1) if mes < 12 else (anio + 1, 1)
    return render(request, "gestion/finanzas.html", {
        "r": resultado, "platos": platos, "inversion": inversion_actual(), "nombre_mes": nombre_mes,
        "mes_valor": f"{anio}-{mes:02d}", "mes_max": f"{hoy.year}-{hoy.month:02d}",
        "anterior": f"{anterior[0]}-{anterior[1]:02d}",
        "siguiente": f"{siguiente[0]}-{siguiente[1]:02d}" if siguiente <= (hoy.year, hoy.month) else "",
        "gastos_fijos": GastoFijo.objects.all(), "gastos_extra": GastoExtraordinario.objects.select_related("registrado_por")[:30],
    })


@requiere_admin
def gasto_fijo(request, gasto_id=None):
    from gestion.models import GastoFijo

    instancia = get_object_or_404(GastoFijo, pk=gasto_id) if gasto_id else GastoFijo()
    form = GastoFijoForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Gasto fijo «{form.instance.nombre}» guardado.")
        return redirect(reverse("gestion:finanzas") + "#gastos-fijos")
    return render(request, "gestion/form_simple.html", {
        "form": form, "titulo": instancia.nombre if instancia.pk else "Nuevo gasto fijo",
        "volver": reverse("gestion:finanzas"),
        "subtitulo": "Se descuenta cada mes de su vigencia. Para dejar de pagarlo, pon la fecha «Hasta».",
    })


@requiere_admin
def gasto_extraordinario(request, gasto_id=None):
    from gestion.models import GastoExtraordinario

    instancia = get_object_or_404(GastoExtraordinario, pk=gasto_id) if gasto_id else GastoExtraordinario()
    form = GastoExtraordinarioForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        gasto = form.save(commit=False)
        if not gasto.pk:
            gasto.registrado_por = request.user
        gasto.save()
        messages.success(request, f"Gasto «{gasto.descripcion}» registrado.")
        return redirect(reverse("gestion:finanzas") + "#gastos-extra")
    return render(request, "gestion/form_simple.html", {
        "form": form, "titulo": instancia.descripcion if instancia.pk else "Nuevo gasto extraordinario",
        "volver": reverse("gestion:finanzas"),
        "subtitulo": "Compras fuertes o puntuales: máquinas, reparaciones, remodelación…",
    })


@requiere_admin
@require_POST
def gasto_eliminar(request, tipo, gasto_id):
    from gestion.models import GastoExtraordinario, GastoFijo

    modelo = GastoFijo if tipo == "fijo" else GastoExtraordinario
    gasto = get_object_or_404(modelo, pk=gasto_id)
    nombre = str(gasto)
    gasto.delete()
    messages.success(request, f"Se eliminó «{nombre}».")
    return redirect("gestion:finanzas")
