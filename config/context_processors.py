from config.permisos import GRUPO_CAJA, GRUPO_COCINA, GRUPO_MESERO, GRUPO_SHAWARMA, ROLES, en_grupo, es_admin, nombres_grupos


def _es_fragmento(request) -> bool:
    """Fragmentos que la pantalla pide cada 5 s (mapa, cocina): no muestran menú ni alertas."""
    vista = getattr(getattr(request, "resolver_match", None), "url_name", "") or ""
    return vista.endswith("parcial")


def parcial(request):
    from django.conf import settings

    # `version_app` va en los enlaces al CSS y JS: al cambiar de versión, los navegadores
    # del local descargan los archivos nuevos en vez de usar la copia vieja de su caché.
    return {"parcial": getattr(request, "parcial", False), "version_app": settings.VERSION_APP}


def roles(request):
    user = request.user
    if not user.is_authenticated:
        return {"es_admin": False, "es_mesero": False, "es_cocinero": False, "es_cajero": False, "es_shawarmero": False}
    admin = es_admin(user)
    if admin:
        rol_actual = "Administrador"
    else:
        grupos = nombres_grupos(user)
        rol_actual = next((etiqueta for nombre, etiqueta in ROLES if nombre in grupos), "Sin rol")
    return {
        "es_admin": admin,
        "es_mesero": en_grupo(user, GRUPO_MESERO),
        "es_cocinero": en_grupo(user, GRUPO_COCINA),
        "es_shawarmero": en_grupo(user, GRUPO_SHAWARMA),
        "es_cajero": en_grupo(user, GRUPO_CAJA),
        "rol_actual": rol_actual,
    }


def tasa(request):
    """Tasa vigente para el recuadro del menú lateral (sala y caja la usan a diario)."""
    if _es_fragmento(request) or not request.user.is_authenticated:
        return {}
    if not (en_grupo(request.user, GRUPO_CAJA) or en_grupo(request.user, GRUPO_MESERO)):
        return {}
    from caja.models import TasaCambio
    from caja.tasa_bcv import revisar_en_segundo_plano

    revisar_en_segundo_plano()  # consulta el BCV como mucho cada TASA_BCV_HORAS, sin hacer esperar la página
    return {"tasa_vigente": TasaCambio.objects.filter(activa=True).order_by("-vigente_desde").first()}


def alertas_inventario(request):
    """RF-04: insumos en nivel crítico visibles para administración, caja y cocina."""
    user = request.user
    if not user.is_authenticated or _es_fragmento(request):
        return {}
    if not (en_grupo(user, GRUPO_CAJA) or en_grupo(user, GRUPO_COCINA)):
        return {}
    from inventario.servicios import nombres_insumos_criticos

    return {"insumos_criticos": nombres_insumos_criticos()}


def pedidos_en_linea(request):
    """Contador en el menú de caja: pagos por verificar y pedidos listos para avisar."""
    user = request.user
    if not user.is_authenticated or _es_fragmento(request) or not en_grupo(user, GRUPO_CAJA):
        return {}
    from mesas.models import PedidoEnLinea

    activos = PedidoEnLinea.objects.filter(estado__in=[PedidoEnLinea.Estado.POR_VERIFICAR, PedidoEnLinea.Estado.ACEPTADO]).select_related("pedido")
    pendientes = sum(1 for p in activos if p.etapa in ("verificar", "listo"))
    return {"en_linea_pendientes": pendientes}
