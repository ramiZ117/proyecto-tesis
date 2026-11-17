from django.urls import path

from . import views

app_name = "mesas"

urlpatterns = [
    path("", views.mapa, name="mapa"),
    path("parcial/", views.mapa_parcial, name="mapa_parcial"),
    path("abrir/<int:mesa_id>/", views.abrir_pedido, name="abrir"),
    path("llevar/nuevo/", views.nuevo_para_llevar, name="llevar_nuevo"),
    path("comanda/<int:pedido_id>/", views.comanda, name="comanda"),
    path("comanda/<int:pedido_id>/agregar/", views.agregar_item, name="agregar"),
    path("comanda/<int:pedido_id>/enviar/", views.enviar_cocina, name="enviar"),
    path("comanda/<int:pedido_id>/promocion/", views.agregar_promocion, name="agregar_promocion"),
    path("solicitud/<int:solicitud_id>/cargar/", views.cargar_solicitud, name="cargar_solicitud"),
    path("solicitud/<int:solicitud_id>/descartar/", views.descartar_solicitud, name="descartar_solicitud"),
    path("comanda/<int:pedido_id>/cuenta/", views.solicitar_cuenta, name="cuenta"),
    path("comanda/<int:pedido_id>/anular/", views.anular_pedido, name="anular"),
    path("comanda/<int:pedido_id>/nota/", views.nota_pedido, name="nota"),
    path("comanda/<int:pedido_id>/mover/", views.mover_pedido, name="mover"),
    path("item/<int:detalle_id>/quitar/", views.quitar_item, name="quitar"),
    path("item/<int:detalle_id>/cantidad/", views.cambiar_cantidad, name="cantidad"),
    path("item/<int:detalle_id>/personalizar/", views.personalizar_item, name="personalizar"),
    path("item/<int:detalle_id>/entregar/", views.entregar_item, name="entregar"),
    path("limpiar/<int:mesa_id>/", views.marcar_limpia, name="limpiar"),
]
