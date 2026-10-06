from django.urls import path

from . import views

app_name = "cocina"

urlpatterns = [
    path("", views.kds, name="kds"),
    path("parcial/", views.kds_parcial, name="kds_parcial"),
    path("item/<int:detalle_id>/", views.cambiar_estado, name="estado"),
]
