from django.urls import path

from . import views

app_name = "cocina"

urlpatterns = [
    path("", views.kds, name="kds"),
    path("parcial/", views.kds_parcial, name="kds_parcial"),
    path("shawarma/", views.shawarma, name="shawarma"),
    path("shawarma/parcial/", views.shawarma_parcial, name="shawarma_parcial"),
    path("aviso/<int:aviso_id>/", views.atender_aviso, name="aviso"),
    path("item/<int:detalle_id>/", views.cambiar_estado, name="estado"),
    path("pedido/<int:pedido_id>/", views.avanzar_pedido, name="avanzar"),
]
