from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError

GRUPO_ADMIN = "Administradores"
GRUPO_MESERO = "Meseros"
GRUPO_COCINA = "Cocina"
GRUPO_CAJA = "Caja"

ROLES = [
    (GRUPO_ADMIN, "Administrador"),
    (GRUPO_CAJA, "Cajero"),
    (GRUPO_MESERO, "Mesonero"),
    (GRUPO_COCINA, "Cocinero"),
]


def nombres_grupos(user) -> set[str]:
    """Grupos del usuario, consultados una sola vez por request."""
    if not hasattr(user, "_grupos_nombres"):
        user._grupos_nombres = set(user.groups.values_list("name", flat=True))
    return user._grupos_nombres


def es_admin(user) -> bool:
    if not user.is_authenticated:
        return False
    return user.is_superuser or GRUPO_ADMIN in nombres_grupos(user)


def en_grupo(user, *grupos: str) -> bool:
    """El administrador accede a todo; `is_staff` por sí solo ya NO da acceso."""
    if not user.is_authenticated:
        return False
    if es_admin(user):
        return True
    return bool(nombres_grupos(user) & set(grupos))


def requiere_grupo(*grupos: str):
    def decorador(vista):
        @login_required
        @wraps(vista)
        def interna(request, *args, **kwargs):
            if not en_grupo(request.user, *grupos):
                raise PermissionDenied
            return vista(request, *args, **kwargs)

        return interna

    return decorador


def requiere_admin(vista):
    @login_required
    @wraps(vista)
    def interna(request, *args, **kwargs):
        if not es_admin(request.user):
            raise PermissionDenied
        return vista(request, *args, **kwargs)

    return interna


def texto_validacion(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        partes = []
        for msgs in exc.message_dict.values():
            partes.extend(str(m) for m in msgs)
        return "; ".join(partes)
    if getattr(exc, "messages", None):
        return "; ".join(str(m) for m in exc.messages)
    return str(exc)
