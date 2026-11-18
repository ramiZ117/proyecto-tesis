from django.urls import path

from . import views

app_name = "gestion"

urlpatterns = [
    path("carta/", views.carta, name="carta"),
    path("carta/plato/nuevo/", views.plato, name="plato_nuevo"),
    path("carta/plato/<int:producto_id>/", views.plato, name="plato"),
    path("carta/plato/<int:producto_id>/disponible/", views.plato_disponible, name="plato_disponible"),
    path("carta/categoria/nueva/", views.categoria, name="categoria_nueva"),
    path("carta/categoria/<int:categoria_id>/", views.categoria, name="categoria"),
    path("insumos/", views.insumos, name="insumos"),
    path("insumos/nuevo/", views.insumo, name="insumo_nuevo"),
    path("insumos/<int:insumo_id>/", views.insumo, name="insumo"),
    path("proveedores/", views.proveedores, name="proveedores"),
    path("proveedores/nuevo/", views.proveedor, name="proveedor_nuevo"),
    path("proveedores/<int:proveedor_id>/", views.proveedor, name="proveedor"),
    path("mesas/", views.mesas, name="mesas"),
    path("mesas/nueva/", views.mesa, name="mesa_nueva"),
    path("mesas/<int:mesa_id>/", views.mesa, name="mesa"),
    path("promociones/", views.promociones, name="promociones"),
    path("promociones/nueva/", views.promocion, name="promocion_nueva"),
    path("promociones/<int:promocion_id>/", views.promocion, name="promocion"),
    path("promociones/<int:promocion_id>/activa/", views.promocion_activa, name="promocion_activa"),
    path("menu-digital/", views.menu_digital, name="menu_digital"),
    path("cobros-y-delivery/", views.ajustes, name="ajustes"),
    path("finanzas/", views.finanzas, name="finanzas"),
    path("finanzas/gasto-fijo/nuevo/", views.gasto_fijo, name="gasto_fijo_nuevo"),
    path("finanzas/gasto-fijo/<int:gasto_id>/", views.gasto_fijo, name="gasto_fijo"),
    path("finanzas/gasto/nuevo/", views.gasto_extraordinario, name="gasto_extra_nuevo"),
    path("finanzas/gasto/<int:gasto_id>/", views.gasto_extraordinario, name="gasto_extra"),
    path("finanzas/<str:tipo>/<int:gasto_id>/eliminar/", views.gasto_eliminar, name="gasto_eliminar"),
]
