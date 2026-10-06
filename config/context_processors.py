from config.permisos import GRUPO_CAJA, GRUPO_COCINA, GRUPO_MESERO, en_grupo, es_admin


def parcial(request):
    return {"parcial": getattr(request, "parcial", False)}


def roles(request):
    user = request.user
    if not user.is_authenticated:
        return {"es_admin": False, "es_mesero": False, "es_cocinero": False, "es_cajero": False}
    return {
        "es_admin": es_admin(user),
        "es_mesero": en_grupo(user, GRUPO_MESERO),
        "es_cocinero": en_grupo(user, GRUPO_COCINA),
        "es_cajero": en_grupo(user, GRUPO_CAJA),
    }


def alertas_inventario(request):
    """RF-04: insumos en nivel crítico visibles para administración, caja y cocina."""
    user = request.user
    if not user.is_authenticated:
        return {}
    if not (en_grupo(user, GRUPO_CAJA) or en_grupo(user, GRUPO_COCINA)):
        return {}
    from inventario.servicios import nombres_insumos_criticos

    return {"insumos_criticos": nombres_insumos_criticos()}
