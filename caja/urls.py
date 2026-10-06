from django.urls import path

from . import views

app_name = "caja"

urlpatterns = [
    path("", views.cuentas, name="cuentas"),
    path("pedido/<int:pedido_id>/facturar/", views.emitir_factura, name="emitir"),
    path("factura/<int:factura_id>/", views.factura_detalle, name="factura"),
    path("factura/<int:factura_id>/pago/", views.registrar_pago, name="pago"),
    path("factura/<int:factura_id>/anular/", views.anular_factura, name="anular_factura"),
    path("factura/<int:factura_id>/asignar/", views.asignar_platillo, name="asignar"),
    path("factura/<int:factura_id>/asignar/quitar/", views.quitar_asignacion, name="quitar_asignacion"),
    path("tasa/", views.tasa_cambio, name="tasa"),
    path("tasa/guardar/", views.guardar_tasa, name="guardar_tasa"),
    path("cierre/", views.cierre, name="cierre"),
    path("cierre/generar/", views.generar_cierre, name="generar_cierre"),
    path("cierre/historial/", views.historial, name="historial"),
    path("cierre/<int:cierre_id>/", views.cierre_detalle, name="cierre_detalle"),
    path("cierre/<int:cierre_id>/csv/", views.cierre_csv, name="cierre_csv"),
    path("reporte/", views.reporte_dia, name="reporte"),
]
