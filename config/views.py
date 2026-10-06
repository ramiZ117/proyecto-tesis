from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from config.forms import UsuarioForm
from config.permisos import (
    GRUPO_ADMIN,
    GRUPO_CAJA,
    GRUPO_COCINA,
    GRUPO_MESERO,
    ROLES,
    es_admin,
    nombres_grupos,
    requiere_admin,
)

MAX_INTENTOS = 5
SEGUNDOS_BLOQUEO = 300


class LoginSeguro(LoginView):
    """Login con bloqueo temporal tras varios intentos fallidos (usuario + IP)."""

    template_name = "registration/login.html"

    def _clave(self, request) -> str:
        usuario = (request.POST.get("username") or "").strip().lower()
        ip = request.META.get("REMOTE_ADDR", "")
        return f"login-fallos:{usuario}:{ip}"

    def post(self, request, *args, **kwargs):
        if cache.get(self._clave(request), 0) >= MAX_INTENTOS:
            form = self.get_form()
            form.is_bound = True
            form.add_error(
                None,
                f"Demasiados intentos fallidos. Espera {SEGUNDOS_BLOQUEO // 60} minutos para volver a intentar.",
            )
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        clave = self._clave(self.request)
        cache.set(clave, cache.get(clave, 0) + 1, SEGUNDOS_BLOQUEO)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._clave(self.request))
        return super().form_valid(form)


@login_required
def inicio(request):
    user = request.user
    if es_admin(user):
        return render(request, "inicio.html")
    grupos = nombres_grupos(user)
    if grupos == {GRUPO_COCINA}:
        return redirect("cocina:kds")
    if grupos == {GRUPO_CAJA}:
        return redirect("caja:cuentas")
    if grupos == {GRUPO_MESERO}:
        return redirect("mesas:mapa")
    return render(request, "inicio.html")


def _rol_de(usuario: User) -> str:
    if usuario.is_superuser:
        return GRUPO_ADMIN
    grupos = nombres_grupos(usuario)
    for nombre, _ in ROLES:
        if nombre in grupos:
            return nombre
    return ""


def _aplicar_rol(usuario: User, rol: str) -> None:
    usuario.groups.set([Group.objects.get_or_create(name=rol)[0]])
    es_administrador = rol == GRUPO_ADMIN
    usuario.is_staff = es_administrador
    usuario.is_superuser = es_administrador
    usuario.save(update_fields=["is_staff", "is_superuser"])


@requiere_admin
def usuarios(request):
    lista = []
    etiquetas = dict(ROLES)
    for u in User.objects.order_by("username"):
        u._grupos_nombres = set(u.groups.values_list("name", flat=True))
        lista.append({"usuario": u, "rol": etiquetas.get(_rol_de(u), "Sin rol")})
    return render(request, "usuarios/lista.html", {"usuarios": lista})


@requiere_admin
def usuario_nuevo(request):
    form = UsuarioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        usuario = User(
            username=datos["username"],
            first_name=datos["first_name"],
            is_active=datos["is_active"],
        )
        usuario.set_password(datos["password"])
        usuario.save()
        _aplicar_rol(usuario, datos["rol"])
        messages.success(request, f"Usuario {usuario.username} creado.")
        return redirect("usuarios")
    return render(request, "usuarios/form.html", {"form": form, "titulo": "Nuevo usuario"})


@requiere_admin
def usuario_editar(request, user_id):
    usuario = get_object_or_404(User, pk=user_id)
    inicial = {
        "username": usuario.username,
        "first_name": usuario.first_name,
        "rol": _rol_de(usuario) or GRUPO_MESERO,
        "is_active": usuario.is_active,
    }
    form = UsuarioForm(request.POST or None, initial=inicial, usuario=usuario)
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        if usuario.pk == request.user.pk and (not datos["is_active"] or datos["rol"] != GRUPO_ADMIN):
            messages.error(request, "No puedes desactivarte ni quitarte el rol de administrador a ti mismo.")
            return render(request, "usuarios/form.html", {"form": form, "titulo": f"Editar {usuario.username}"})
        usuario.username = datos["username"]
        usuario.first_name = datos["first_name"]
        usuario.is_active = datos["is_active"]
        if datos["password"]:
            usuario.set_password(datos["password"])
        usuario.save()
        _aplicar_rol(usuario, datos["rol"])
        messages.success(request, f"Usuario {usuario.username} actualizado.")
        return redirect("usuarios")
    return render(request, "usuarios/form.html", {"form": form, "titulo": f"Editar {usuario.username}"})


@requiere_admin
@require_POST
def usuario_alternar(request, user_id):
    usuario = get_object_or_404(User, pk=user_id)
    if usuario.pk == request.user.pk:
        messages.error(request, "No puedes desactivar tu propio usuario.")
    else:
        usuario.is_active = not usuario.is_active
        usuario.save(update_fields=["is_active"])
        messages.success(request, f"Usuario {usuario.username} {'activado' if usuario.is_active else 'desactivado'}.")
    return redirect("usuarios")
