from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from django.views.static import serve

from config import views

urlpatterns = [
    path("login/", views.LoginSeguro.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.inicio, name="inicio"),
    path("panel/", views.panel, name="panel"),
    path("usuarios/", views.usuarios, name="usuarios"),
    path("usuarios/nuevo/", views.usuario_nuevo, name="usuario_nuevo"),
    path("usuarios/<int:user_id>/", views.usuario_editar, name="usuario_editar"),
    path("usuarios/<int:user_id>/alternar/", views.usuario_alternar, name="usuario_alternar"),
    path("mesas/", include("mesas.urls")),
    path("cocina/", include("cocina.urls")),
    path("caja/", include("caja.urls")),
    path("inventario/", include("inventario.urls")),
    path("gestion/", include("gestion.urls")),
    # Menú digital para clientes: público, sin iniciar sesión (enlace o QR).
    path("menu/", include("menu.urls")),
    # Fotos de los platos. Un solo local con pocos archivos: Django los sirve directamente.
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]
