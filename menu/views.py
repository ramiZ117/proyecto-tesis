"""Menú digital público: los clientes lo abren con un enlace o un QR, sin registrarse."""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from caja.models import SolicitudCuenta, TasaCambio
from gestion.models import AjustesLocal
from config.formato import num
from inventario.models import Categoria, Producto, Promocion
from inventario.servicios import disponibilidad_menu
from menu.asistente import MAX_MENSAJE, proveedor_activo, responder
from menu.mesa import es_valido
from mesas.models import DetallePedido, Mesa, Pedido, SolicitudCliente

registro = logging.getLogger("el_amir")


MAX_LINEAS_SOLICITUD = 30
MAX_CANTIDAD_LINEA = 20
SOLICITUDES_POR_HORA = 20


@ensure_csrf_cookie
def carta(request):
    from caja.tasa_bcv import revisar_en_segundo_plano

    revisar_en_segundo_plano()
    tasa = TasaCambio.objects.filter(activa=True).order_by("-vigente_desde").first()
    porciones = disponibilidad_menu()
    categorias = list(
        Categoria.objects.filter(activa=True, productos__disponible=True).distinct().order_by("orden", "nombre")
        .prefetch_related(Prefetch("productos", queryset=Producto.objects.filter(disponible=True)
                                   .prefetch_related("recetas__insumo").order_by("-destacado", "nombre")))
    )
    destacados = []
    platos_json = {}
    for categoria in categorias:
        for p in categoria.productos.all():
            p.precio_bs = tasa.a_local(p.precio_usd) if tasa else None
            p.agotado = porciones.get(p.pk) == 0
            p.ingredientes = [r.insumo.nombre for r in p.recetas.all()]
            if p.destacado and not p.agotado:
                destacados.append(p)
            platos_json[p.pk] = {
                "nombre": p.nombre, "categoria": categoria.nombre, "descripcion": p.descripcion,
                "precio_usd": num(p.precio_usd, 2), "precio_bs": num(p.precio_bs, 2) if p.precio_bs is not None else "",
                "foto": p.foto.url if p.foto else "", "credito": p.foto_credito,
                "etiquetas": p.etiquetas, "ingredientes": p.ingredientes, "agotado": p.agotado,
                "usd": str(p.precio_usd),
                "opciones": p.lista_opciones, "minutos": p.minutos_preparacion,
            }
    promociones = []
    promos_json = {}
    for promo in Promocion.vigentes():
        items = list(promo.items.all())
        if not promo.disponible:
            continue
        promo.agotada = any(porciones.get(i.producto_id) is not None and porciones[i.producto_id] < i.cantidad for i in items)
        promo.precio_bs = tasa.a_local(promo.precio_usd) if tasa else None
        promo.fotos = [i.producto.foto.url for i in items if i.producto.foto][:3]
        promociones.append(promo)
        promos_json[promo.pk] = {
            "nombre": promo.nombre, "precio_usd": num(promo.precio_usd, 2),
            "precio_bs": num(promo.precio_bs, 2) if promo.precio_bs is not None else "",
            "incluye": [f"{i.cantidad} × {i.producto.nombre}" for i in items], "agotada": promo.agotada,
            "usd": str(promo.precio_usd),
            "minutos": max((i.producto.minutos_preparacion for i in items), default=10),
        }
    mesa = None
    pedir = False
    codigo = request.GET.get("c", "")
    try:
        mesa = Mesa.objects.filter(numero=int(request.GET.get("mesa", "")), activa=True).first()
    except ValueError:
        pass
    if mesa is not None and es_valido(mesa.numero, codigo):
        pedir = True
    ajustes = AjustesLocal.actual()
    # mesa: QR de una mesa (pedido al mesero y cuenta) · retiro: enlace general (pide, paga y retira)
    modo = "mesa" if pedir else ("retiro" if ajustes.retiro_activo else "")
    return render(request, "menu/carta.html", {
        "categorias": categorias, "destacados": destacados, "tasa": tasa, "mesa": mesa, "platos_json": platos_json,
        "promociones": promociones, "promos_json": promos_json,
        "pedir": pedir,
        "modo": modo,
        "ajustes": ajustes,
        "config_menu": {
            "asistente": reverse("menu:asistente"), "solicitar": reverse("menu:solicitar"),
            "estado": reverse("menu:estado_solicitud"), "mesa": mesa.numero if pedir else None,
            "cuenta": reverse("menu:pedir_cuenta"), "reportar": reverse("menu:reportar_pago"),
            "metodos": ajustes.metodos_cliente() if pedir else [],
            "modo": modo, "metodos_retiro": ajustes.metodos_retiro() if modo == "retiro" else [],
            "cotizar": reverse("menu:retiro_cotizar"), "retiro": reverse("menu:retiro_crear"),
            "carga": _carga_cocina(),
            "codigo": codigo if pedir else "", "tasa": str(tasa.valor) if tasa else "",
        },
        "ia": proveedor_activo(), "max_mensaje": MAX_MENSAJE, "local": _datos_local(),
    })


def _datos_local() -> dict:
    telefono = settings.RESTAURANTE_TELEFONO
    digitos = "".join(c for c in telefono if c.isdigit())
    internacional = "+58" + digitos.lstrip("0") if digitos.startswith("0") else ("+" + digitos if digitos else "")
    whatsapp = "".join(c for c in settings.RESTAURANTE_WHATSAPP if c.isdigit())
    instagram = settings.RESTAURANTE_INSTAGRAM.lstrip("@")
    return {
        "nombre": settings.RESTAURANTE_NOMBRE,
        "telefono": telefono,
        "telefono_enlace": internacional,
        "whatsapp": f"https://wa.me/{whatsapp}" if whatsapp else "",
        "instagram": instagram,
        "instagram_url": f"https://www.instagram.com/{instagram}/" if instagram else "",
        "direccion": settings.RESTAURANTE_DIRECCION,
    }


def contacto(request):
    """Tarjeta de contacto (vCard) para guardar el número del local en la agenda del teléfono."""
    local = _datos_local()

    def escapar(texto: str) -> str:
        return texto.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", " ")

    lineas = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{escapar(local['nombre'])}", f"ORG:{escapar(local['nombre'])}"]
    if local["telefono_enlace"]:
        lineas.append(f"TEL;TYPE=WORK,VOICE:{local['telefono_enlace']}")
    if local["direccion"]:
        lineas.append(f"ADR;TYPE=WORK:;;{escapar(local['direccion'])};;;;Venezuela")
    if local["instagram_url"]:
        lineas.append(f"URL:{local['instagram_url']}")
    lineas.append(f"NOTE:Menú digital: {request.build_absolute_uri(reverse('menu:carta'))}")
    lineas.append("END:VCARD")
    respuesta = HttpResponse("\r\n".join(lineas) + "\r\n", content_type="text/vcard; charset=utf-8")
    respuesta["Content-Disposition"] = 'attachment; filename="el-amir.vcf"'
    return respuesta


def _ip(request) -> str:
    return request.META.get("REMOTE_ADDR", "") or "desconocida"


@require_POST
def asistente(request):
    """Responde una pregunta sobre la carta. Público, con límite de mensajes por hora y por dirección IP."""
    try:
        datos = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Mensaje no válido."}, status=400)
    if not isinstance(datos, dict):
        return JsonResponse({"error": "Mensaje no válido."}, status=400)
    mensaje = datos.get("mensaje")
    if not isinstance(mensaje, str) or not mensaje.strip():
        return JsonResponse({"error": "Escribe tu pregunta."}, status=400)
    if len(mensaje) > MAX_MENSAJE:
        return JsonResponse({"error": f"La pregunta es muy larga (máximo {MAX_MENSAJE} caracteres)."}, status=400)
    historial = datos.get("historial") if isinstance(datos.get("historial"), list) else []
    plato_id = datos.get("plato") if isinstance(datos.get("plato"), int) else None

    clave = f"menu:ia:{_ip(request)}"
    usados = cache.get(clave, 0)
    if usados >= settings.IA_MENSAJES_POR_HORA:
        return JsonResponse(
            {"error": "Has hecho muchas preguntas seguidas. Espera un rato o pregúntale a tu mesonero."}, status=429
        )
    cache.set(clave, usados + 1, 3600)

    resultado = responder(mensaje, historial, plato_id)
    return JsonResponse(resultado)


def _leer_items(crudos) -> list[dict] | None:
    """Valida el carrito que manda el teléfono. Devuelve los ítems limpios o None si algo no cuadra."""
    if not isinstance(crudos, list) or len(crudos) > MAX_LINEAS_SOLICITUD:
        return None
    limpios = []
    for item in crudos:
        if not isinstance(item, dict):
            return None
        tipo, ident, cantidad = item.get("tipo"), item.get("id"), item.get("cantidad")
        if tipo not in ("producto", "promocion") or not isinstance(ident, int) or isinstance(ident, bool):
            return None
        if not isinstance(cantidad, int) or isinstance(cantidad, bool) or not 1 <= cantidad <= MAX_CANTIDAD_LINEA:
            return None
        nota = item.get("nota") or ""
        if not isinstance(nota, str):
            return None
        limpios.append({"tipo": tipo, "id": ident, "cantidad": cantidad, "nota": " ".join(nota.split())[:200]})
    productos = set(Producto.objects.filter(pk__in=[i["id"] for i in limpios if i["tipo"] == "producto"], disponible=True)
                    .values_list("pk", flat=True))
    promos = set(Promocion.vigentes().filter(pk__in=[i["id"] for i in limpios if i["tipo"] == "promocion"])
                 .values_list("pk", flat=True))
    for i in limpios:
        if i["id"] not in (productos if i["tipo"] == "producto" else promos):
            return None
    return limpios


def _combinar(actuales: list[dict], nuevos: list[dict]) -> list[dict]:
    """Suma lo que pide otro teléfono de la misma mesa a la solicitud que aún no se atiende."""
    resultado = [dict(i) for i in actuales]
    for nuevo in nuevos:
        igual = next((i for i in resultado if i["tipo"] == nuevo["tipo"] and i["id"] == nuevo["id"]
                      and (i.get("nota") or "") == nuevo["nota"]), None)
        if igual:
            igual["cantidad"] = min(igual["cantidad"] + nuevo["cantidad"], MAX_CANTIDAD_LINEA)
        else:
            resultado.append(nuevo)
    return resultado[:MAX_LINEAS_SOLICITUD]


@require_POST
def solicitar(request):
    """«Solicitar mesero» desde el menú de la mesa, con o sin pedido armado.

    No envía nada a cocina: avisa al salón y deja el pedido listo para que el mesero lo
    confirme con el cliente y lo cargue en la comanda.
    """
    try:
        datos = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Solicitud no válida."}, status=400)
    if not isinstance(datos, dict):
        return JsonResponse({"error": "Solicitud no válida."}, status=400)
    numero = datos.get("mesa")
    if not es_valido(numero, datos.get("codigo")):
        return JsonResponse({"error": "Escanea el código QR de tu mesa para llamar al mesero."}, status=403)
    mesa = Mesa.objects.filter(numero=numero, activa=True).first()
    if mesa is None:
        return JsonResponse({"error": "Esta mesa no está en servicio. Llama al mesero con la mano 🙂"}, status=404)
    items = _leer_items(datos.get("items", []))
    if items is None:
        return JsonResponse({"error": "Algún plato de tu pedido ya no está disponible. Actualiza la página e intenta de nuevo."}, status=400)
    nota = datos.get("nota") if isinstance(datos.get("nota"), str) else ""
    nota = " ".join(nota.split())[:200]

    clave = f"menu:solicitud:{_ip(request)}"
    usados = cache.get(clave, 0)
    if usados >= SOLICITUDES_POR_HORA:
        return JsonResponse({"error": "Ya avisamos a tu mesero; enseguida te atiende."}, status=429)
    cache.set(clave, usados + 1, 3600)

    try:
        with transaction.atomic():
            pendiente = (SolicitudCliente.objects.select_for_update()
                         .filter(mesa=mesa, estado=SolicitudCliente.Estado.PENDIENTE).first())
            if pendiente:
                pendiente.items = _combinar(pendiente.items, items)
                if nota:
                    pendiente.nota = (f"{pendiente.nota} · {nota}" if pendiente.nota and nota not in pendiente.nota else nota)[:200]
                pendiente.save(update_fields=["items", "nota", "actualizada_en"])
            else:
                SolicitudCliente.objects.create(mesa=mesa, items=items, nota=nota)
    except IntegrityError:
        return JsonResponse({"error": "Tu mesa acaba de llamar al mesero desde otro teléfono. Intenta de nuevo."}, status=409)

    registro.info("Mesa %s solicita mesero (%s líneas)", mesa.numero, len(items))
    if items:
        mensaje = "¡Listo! Tu mesero viene a confirmar el pedido contigo antes de enviarlo a la cocina."
    else:
        mensaje = "¡Listo! Avisamos a tu mesero: viene en camino."
    return JsonResponse({"ok": True, "mensaje": mensaje})


def estado_solicitud(request):
    """El teléfono pregunta si el mesero ya atendió la mesa (para quitar el aviso de espera)."""
    numero, codigo = request.GET.get("mesa"), request.GET.get("c")
    if not es_valido(numero, codigo):
        return JsonResponse({"error": "Código de mesa no válido."}, status=403)
    pendiente = SolicitudCliente.objects.filter(mesa__numero=numero, estado=SolicitudCliente.Estado.PENDIENTE).exists()
    mesa = Mesa.objects.filter(numero=numero, activa=True).first()
    pedido = _pedido_de_mesa(mesa)
    return JsonResponse({"pendiente": pendiente, "cuenta": _estado_cuenta(mesa, request.GET.get("pedido")),
                         "cocina_min": _minutos_pedido(pedido) if pedido else None})


# ---- la cuenta desde la mesa ------------------------------------------------------------
def _pedido_de_mesa(mesa):
    return (Pedido.objects.filter(mesa=mesa, tipo=Pedido.Tipo.MESA)
            .exclude(estado__in=[Pedido.Estado.CERRADO, Pedido.Estado.ANULADO]).first()) if mesa else None


def _estado_cuenta(mesa, pedido_previo=None) -> dict:
    """Lo que el teléfono necesita para el botón «Pedir la cuenta» y para seguir el pago."""
    pedido = _pedido_de_mesa(mesa)
    if pedido is None:
        # ¿La cuenta que este teléfono pidió ya se cobró?
        pagada = False
        if mesa and str(pedido_previo or "").isdigit():
            pagada = Pedido.objects.filter(pk=int(pedido_previo), mesa=mesa, estado=Pedido.Estado.CERRADO).exists()
        return {"disponible": False, "pagada": pagada}
    # Se puede pedir la cuenta en cuanto el pedido está en cocina (aunque falten platos: p. ej. para llevar).
    en_cocina = pedido.detalles.filter(enviado=True).exclude(estado=DetallePedido.Estado.ANULADO).exists()
    pendientes = sum(pedido.detalles.filter(enviado=True, estado__in=[DetallePedido.Estado.PENDIENTE,
                     DetallePedido.Estado.EN_PREPARACION, DetallePedido.Estado.LISTO]).values_list("cantidad", flat=True))
    factura = pedido.factura_activa
    tasa = Decimal(factura.tasa_cambio) if factura else getattr(TasaCambio.objects.filter(activa=True).first(), "valor", None)
    total_bs = factura.saldo_local if factura else pedido.subtotal
    datos = {
        "disponible": en_cocina, "pagada": False, "pedido": pedido.pk,
        "pendientes": pendientes, "pendientes_min": _minutos_pedido(pedido) if pendientes else None,
        "total_bs": num(total_bs, 2), "total_usd": num((total_bs / tasa).quantize(Decimal("0.01"), rounding="ROUND_CEILING"), 2) if tasa else "",
        "solicitud": None,
    }
    solicitud = SolicitudCuenta.activa_de(pedido)
    if solicitud is not None:
        ajustes = AjustesLocal.actual()
        datos["solicitud"] = {
            "metodo": solicitud.metodo, "nombre": solicitud.get_metodo_display(), "estado": solicitud.estado,
            "digital": solicitud.es_digital, "en_usd": solicitud.en_usd,
            "monto": str(solicitud.monto), "propina": str(solicitud.propina), "referencia": solicitud.referencia,
            "datos": [{"etiqueta": e, "valor": v} for e, v in ajustes.datos_de_pago(solicitud.metodo)],
            "igtf": num(settings.IGTF_PORCENTAJE) if solicitud.metodo in settings.IGTF_METODOS else "",
            "monto_igtf": str((solicitud.monto_usd * (1 + settings.IGTF_PORCENTAJE / 100)).quantize(Decimal("0.01"), rounding="ROUND_CEILING"))
            if solicitud.metodo in settings.IGTF_METODOS else "",
        }
    return datos


def _leer_mesa(datos):
    """(mesa, error JsonResponse) a partir del número y el código del QR."""
    numero = datos.get("mesa")
    if not es_valido(numero, datos.get("codigo")):
        return None, JsonResponse({"error": "Escanea el código QR de tu mesa."}, status=403)
    mesa = Mesa.objects.filter(numero=numero, activa=True).first()
    if mesa is None:
        return None, JsonResponse({"error": "Esta mesa no está en servicio."}, status=404)
    return mesa, None


def _json(request):
    try:
        datos = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return datos if isinstance(datos, dict) else None


def _limitar(request, accion: str, por_hora: int) -> bool:
    clave = f"menu:{accion}:{_ip(request)}"
    usados = cache.get(clave, 0)
    if usados >= por_hora:
        return False
    cache.set(clave, usados + 1, 3600)
    return True


@require_POST
def pedir_cuenta(request):
    """El cliente pide la cuenta y elige cómo pagar. La mesa pasa a «Por facturar» en caja."""
    datos = _json(request)
    if datos is None:
        return JsonResponse({"error": "Solicitud no válida."}, status=400)
    mesa, error = _leer_mesa(datos)
    if error:
        return error
    metodo = datos.get("metodo")
    permitidos = {m["clave"] for m in AjustesLocal.actual().metodos_cliente()}
    if metodo not in permitidos:
        return JsonResponse({"error": "Elige uno de los métodos de pago de la lista."}, status=400)
    if not _limitar(request, "cuenta", 30):
        return JsonResponse({"error": "Demasiados intentos. Pide la cuenta a tu mesero."}, status=429)
    pedido = _pedido_de_mesa(mesa)
    if pedido is None or not pedido.detalles.filter(enviado=True).exclude(estado=DetallePedido.Estado.ANULADO).exists():
        return JsonResponse({"error": "Podrás pedir la cuenta cuando tu pedido esté en la cocina."}, status=409)
    datos_factura = None
    if datos.get("factura"):
        from caja.models import datos_cliente_validos

        datos_factura, error_factura = datos_cliente_validos(datos.get("factura"))
        if error_factura:
            return JsonResponse({"error": error_factura}, status=400)
    if datos.get("para_llevar") is True and "Lo pendiente, para llevar" not in pedido.notas:
        # Llega a cocina (nota de la comanda en el ticket) y al mesero.
        pedido.notas = (f"{pedido.notas} · " if pedido.notas else "") + "Lo pendiente, para llevar"
        pedido.notas = pedido.notas[:250]
        pedido.save(update_fields=["notas", "actualizado_en"])
    try:
        SolicitudCuenta.pedir(pedido, metodo, datos_factura)
    except ValidationError as exc:
        texto = " ".join(exc.messages)
        if "sin enviar" in texto:
            texto = "Tu mesero tiene platos por confirmar. Llámalo y te trae la cuenta."
        return JsonResponse({"error": texto}, status=409)
    registro.info("Mesa %s pide la cuenta (%s)", mesa.numero, metodo)
    return JsonResponse({"ok": True, "cuenta": _estado_cuenta(mesa)})


@require_POST
def reportar_pago(request):
    """El cliente pagó por pago móvil, Binance o Zinli y escribe la referencia (y la propina, si la sumó)."""
    datos = _json(request)
    if datos is None:
        return JsonResponse({"error": "Solicitud no válida."}, status=400)
    mesa, error = _leer_mesa(datos)
    if error:
        return error
    if not _limitar(request, "reporte", 20):
        return JsonResponse({"error": "Demasiados intentos. Pasa por caja, por favor."}, status=429)
    pedido = _pedido_de_mesa(mesa)
    solicitud = SolicitudCuenta.activa_de(pedido) if pedido else None
    if solicitud is None:
        return JsonResponse({"error": "Primero pide la cuenta."}, status=409)
    referencia, origen, telefono = datos.get("referencia"), datos.get("origen") or "", datos.get("telefono") or ""
    try:
        propina = Decimal(str(datos.get("propina") or "0").replace(",", "."))
        if not propina.is_finite():
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return JsonResponse({"error": "La propina no es un monto válido."}, status=400)
    if not isinstance(referencia, str) or not isinstance(origen, str) or not isinstance(telefono, str):
        return JsonResponse({"error": "Escribe la referencia del pago."}, status=400)
    try:
        solicitud.reportar(referencia, origen, propina, telefono=telefono)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    registro.info("Mesa %s reporta pago %s ref %s", mesa.numero, solicitud.metodo, solicitud.referencia)
    return JsonResponse({"ok": True, "cuenta": _estado_cuenta(mesa)})


# ---- pedidos para retirar (enlace general del menú) -------------------------------------------
TELEFONO_VE = r"^(\+?58)?0?\d{3}-?\d{7}$"
RETIROS_POR_HORA = 10


def _cotizar(items: list[dict]) -> tuple[dict | None, str]:
    """Precio oficial del pedido (el cliente transfiere exactamente esto). Devuelve (cotización, error)."""
    from config.dinero import precio_local_vigente

    tasa = TasaCambio.objects.filter(activa=True).order_by("-vigente_desde").first()
    if tasa is None:
        return None, "En este momento no podemos tomar pedidos en línea. Llámanos, por favor."
    porciones = disponibilidad_menu()
    necesarias, lineas, total = {}, [], Decimal("0.00")
    productos = Producto.objects.in_bulk([i["id"] for i in items if i["tipo"] == "producto"])
    promos = {p.pk: p for p in Promocion.vigentes().filter(pk__in=[i["id"] for i in items if i["tipo"] == "promocion"])}
    for item in items:
        if item["tipo"] == "producto":
            producto = productos[item["id"]]
            unitario = precio_local_vigente(producto.precio_usd)
            nombre = producto.nombre
            necesarias[producto.pk] = necesarias.get(producto.pk, 0) + item["cantidad"]
        else:
            promo = promos[item["id"]]
            unitario = tasa.a_local(promo.precio_usd)
            nombre = f"Promo {promo.nombre}"
            for pi in promo.items.all():
                necesarias[pi.producto_id] = necesarias.get(pi.producto_id, 0) + pi.cantidad * item["cantidad"]
        total += unitario * item["cantidad"]
        lineas.append({**item, "nombre": nombre, "precio_local": str(unitario)})
    for pk, cantidad in necesarias.items():
        libres = porciones.get(pk)
        if libres is not None and libres < cantidad:
            nombre = Producto.objects.filter(pk=pk).values_list("nombre", flat=True).first() or "Un plato"
            return None, f"De {nombre} solo quedan {max(libres, 0)} porciones hoy. Ajusta tu pedido."
    if total <= 0:
        return None, "Tu pedido está vacío."
    total_usd = (total / tasa.valor).quantize(Decimal("0.01"), rounding="ROUND_CEILING")
    return {"lineas": lineas, "total_local": total, "total_usd": total_usd, "tasa": tasa.valor}, ""


def _datos_cotizacion(cotizacion: dict, metodo: str) -> dict:
    from caja.models import Pago

    en_usd = metodo in Pago.METODOS_USD
    monto = cotizacion["total_usd"] if en_usd else cotizacion["total_local"]
    return {
        "metodo": metodo, "nombre": Pago.Metodo(metodo).label, "en_usd": en_usd, "monto": str(monto),
        "total_bs": num(cotizacion["total_local"], 2), "total_usd": num(cotizacion["total_usd"], 2),
        "datos": [{"etiqueta": e, "valor": v} for e, v in AjustesLocal.actual().datos_de_pago(metodo)],
    }


def _metodo_retiro(metodo) -> bool:
    return metodo in {m["clave"] for m in AjustesLocal.actual().metodos_retiro()}


@require_POST
def retiro_cotizar(request):
    datos = _json(request)
    if datos is None:
        return JsonResponse({"error": "Solicitud no válida."}, status=400)
    if not _metodo_retiro(datos.get("metodo")):
        return JsonResponse({"error": "Elige pago móvil, Binance o Zinli."}, status=400)
    items = _leer_items(datos.get("items"))
    if not items:
        return JsonResponse({"error": "Algún plato ya no está disponible o tu pedido está vacío. Actualiza la página."}, status=400)
    cotizacion, error = _cotizar(items)
    if error:
        return JsonResponse({"error": error}, status=409)
    return JsonResponse(_datos_cotizacion(cotizacion, datos["metodo"]))


def _comprobante(archivo):
    """Valida el comprobante: imagen (se re-codifica sin metadatos) o PDF. Devuelve (ContentFile, error)."""
    import uuid
    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image, ImageOps, UnidentifiedImageError

    if archivo is None:
        return None, "Sube la captura o el comprobante del pago."
    if archivo.size > settings.COMPROBANTE_MAX_BYTES:
        return None, "El comprobante pesa más de 6 MB."
    inicio = archivo.read(5)
    archivo.seek(0)
    if inicio == b"%PDF-":
        return ContentFile(archivo.read(), name=f"{uuid.uuid4().hex}.pdf"), ""
    try:
        imagen = Image.open(archivo)
        imagen.verify()
        archivo.seek(0)
        imagen = ImageOps.exif_transpose(Image.open(archivo)).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None, "El comprobante debe ser una imagen (JPG, PNG o WEBP) o un PDF."
    if min(imagen.size) < 200:
        return None, "La imagen del comprobante es muy pequeña para leerla."
    imagen.thumbnail((2000, 2000))
    salida = BytesIO()
    imagen.save(salida, format="JPEG", quality=85)
    return ContentFile(salida.getvalue(), name=f"{uuid.uuid4().hex}.jpg"), ""


@require_POST
def retiro_crear(request):
    """Crea el pedido para retirar: datos del cliente, pago digital con referencia y comprobante."""
    import re

    from django.core.validators import validate_email

    from caja.models import datos_cliente_validos
    from mesas.models import PedidoEnLinea

    try:
        datos = json.loads(request.POST.get("datos") or "{}")
    except ValueError:
        datos = None
    if not isinstance(datos, dict):
        return JsonResponse({"error": "Solicitud no válida."}, status=400)

    nombre = " ".join(str(datos.get("nombre") or "").split())
    telefono = str(datos.get("telefono") or "").strip().replace(" ", "")
    correo = str(datos.get("correo") or "").strip()
    nota = " ".join(str(datos.get("nota") or "").split())[:200]
    referencia = " ".join(str(datos.get("referencia") or "").split())
    metodo = datos.get("metodo")
    if not 2 <= len(nombre) <= 80 or not re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' .-]+", nombre):
        return JsonResponse({"error": "Escribe tu nombre (solo letras)."}, status=400)
    if not re.fullmatch(TELEFONO_VE, telefono):
        return JsonResponse({"error": "Escribe un teléfono válido, por ejemplo 0414-1234567."}, status=400)
    if correo:
        try:
            validate_email(correo)
        except ValidationError:
            return JsonResponse({"error": "El correo no es válido."}, status=400)
    if not _metodo_retiro(metodo):
        return JsonResponse({"error": "Elige pago móvil, Binance o Zinli."}, status=400)
    if not re.fullmatch(r"[A-Za-z0-9#\-./ ]{4,60}", referencia):
        return JsonResponse({"error": "Escribe la referencia del pago (al menos 4 caracteres, letras y números)."}, status=400)
    datos_factura = {}
    if datos.get("factura"):
        datos_factura, error = datos_cliente_validos({**(datos.get("factura") if isinstance(datos.get("factura"), dict) else {}),
                                                      "telefono": telefono, "correo": correo})
        if error:
            return JsonResponse({"error": error}, status=400)
    items = _leer_items(datos.get("items"))
    if not items:
        return JsonResponse({"error": "Algún plato ya no está disponible o tu pedido está vacío. Actualiza la página."}, status=400)
    cotizacion, error = _cotizar(items)
    if error:
        return JsonResponse({"error": error}, status=409)
    oferta = _datos_cotizacion(cotizacion, metodo)
    if str(datos.get("monto")) != oferta["monto"]:
        return JsonResponse({"error": "El monto cambió (se actualizó la tasa o los precios). Revisa el nuevo monto antes de pagar.",
                             "cotizacion": oferta}, status=409)
    archivo, error = _comprobante(request.FILES.get("comprobante"))
    if error:
        return JsonResponse({"error": error}, status=400)
    if not _limitar(request, "retiro", RETIROS_POR_HORA):
        return JsonResponse({"error": "Recibimos varios pedidos desde tu conexión. Llámanos para confirmarlos."}, status=429)
    pedido = PedidoEnLinea(
        cliente_nombre=nombre, cliente_telefono=telefono, cliente_correo=correo[:254], datos_factura=datos_factura,
        items=cotizacion["lineas"], nota=nota, metodo=metodo, tasa=cotizacion["tasa"],
        monto_local=cotizacion["total_local"], monto_usd=cotizacion["total_usd"], referencia=referencia[:60],
    )
    pedido.comprobante.save(archivo.name, archivo, save=False)
    pedido.save()
    registro.info("Pedido en línea %s de %s (%s ref %s)", pedido.codigo, nombre, metodo, referencia)
    return JsonResponse({"ok": True, "codigo": pedido.codigo, "url": reverse("menu:retiro_estado", args=[pedido.token])})


def _estado_retiro(pedido) -> dict:
    textos = {
        "verificar": ("Verificando tu pago", "Caja está revisando tu comprobante. En cuanto lo confirme, tu pedido pasa a la cocina."),
        "cocina": ("En preparación", "Tu pago fue verificado y tu pedido ya se está cocinando."),
        "listo": ("¡Listo para retirar!", "Pasa por el local y di tu código en caja."),
        "entregado": ("Entregado", "¡Gracias por tu compra! Buen provecho."),
        "rechazado": ("No pudimos confirmar tu pago", pedido.motivo_rechazo or "Comunícate con nosotros."),
    }
    titulo, detalle = textos[pedido.etapa]
    minutos = None
    if pedido.etapa == "cocina" and pedido.pedido:
        minutos = _minutos_pedido(pedido.pedido)
    elif pedido.etapa == "verificar":
        from cocina.tiempos import estimar_nuevo

        minutos = estimar_nuevo(_minutos_items(pedido.items))
    return {"etapa": pedido.etapa, "titulo": titulo, "detalle": detalle, "minutos": minutos}


def retiro_estado(request, token):
    from django.shortcuts import get_object_or_404

    from mesas.models import PedidoEnLinea

    pedido = get_object_or_404(PedidoEnLinea.objects.select_related("pedido"), token=token)
    estado = _estado_retiro(pedido)
    if request.GET.get("json"):
        return JsonResponse(estado)
    return render(request, "menu/retiro.html", {"pedido": pedido, "estado": estado, "local": _datos_local()})



# ---- tiempos estimados ---------------------------------------------------------------------------
def _carga_cocina() -> int:
    from cocina.tiempos import carga_actual

    return carga_actual()


def _minutos_pedido(pedido):
    from cocina.tiempos import estimar_pedido

    return estimar_pedido(pedido)


def _minutos_items(items: list[dict]) -> list[int]:
    # Las bebidas de nevera no pasan por cocina: no suman espera.
    productos = {p.pk: p for p in Producto.objects.filter(pk__in=[i["id"] for i in items if i["tipo"] == "producto"])}
    minutos = [productos[i["id"]].minutos_preparacion for i in items
               if i["tipo"] == "producto" and i["id"] in productos and productos[i["id"]].bebida != "NEV"]
    for promo in Promocion.objects.filter(pk__in=[i["id"] for i in items if i["tipo"] == "promocion"]).prefetch_related("items__producto"):
        minutos += [pi.producto.minutos_preparacion for pi in promo.items.all() if pi.producto.bebida != "NEV"]
    return minutos
