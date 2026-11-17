from django.urls import path

from . import views

app_name = "inventario"

urlpatterns = [
    path("", views.stock, name="stock"),
    path("entrada/", views.entrada, name="entrada"),
    path("movimiento/", views.movimiento, name="movimiento"),
    path("compra.csv", views.compra_csv, name="compra_csv"),
]
