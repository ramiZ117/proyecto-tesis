from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from config import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", views.LoginSeguro.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.inicio, name="inicio"),
    path("usuarios/", views.usuarios, name="usuarios"),
    path("usuarios/nuevo/", views.usuario_nuevo, name="usuario_nuevo"),
    path("usuarios/<int:user_id>/", views.usuario_editar, name="usuario_editar"),
    path("usuarios/<int:user_id>/alternar/", views.usuario_alternar, name="usuario_alternar"),
    path("mesas/", include("mesas.urls")),
    path("cocina/", include("cocina.urls")),
    path("caja/", include("caja.urls")),
    path("inventario/", include("inventario.urls")),
]
