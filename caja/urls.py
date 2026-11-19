from django.urls import path

from . import views

app_name = "caja"

urlpatterns = [
    path("", views.cuentas, name="cuentas"),
    path("para-llevar/", views.llevar, name="llevar"),
    path("pedido/<int:pedido_id>/facturar/", views.emitir_factura, name="emitir"),
    path("factura/<int:factura_id>/", views.factura_detalle, name="factura"),
    path("factura/<int:factura_id>/pago/", views.registrar_pago, name="pago"),
    path("factura/<int:factura_id>/anular/", views.anular_factura, name="anular_factura"),
    path("factura/<int:factura_id>/propina/", views.registrar_propina, name="propina"),
    path("factura/<int:factura_id>/pago-cliente/", views.revisar_pago_cliente, name="pago_cliente"),
    path("factura/<int:factura_id>/cliente/", views.datos_cliente, name="datos_cliente"),
    path("factura/<int:factura_id>/correo/", views.enviar_correo, name="enviar_correo"),
    path("en-linea/", views.en_linea, name="en_linea"),
    path("en-linea/parcial/", views.en_linea_parcial, name="en_linea_parcial"),
    path("en-linea/<int:pedido_id>/", views.en_linea_accion, name="en_linea_accion"),
    path("en-linea/<int:pedido_id>/comprobante/", views.comprobante, name="comprobante"),
    path("factura/<int:factura_id>/descuento/", views.descuento, name="descuento"),
    path("propinas/", views.propinas, name="propinas"),
    path("propinas/<int:mesero_id>/pagar/", views.pagar_propinas, name="pagar_propinas"),
    path("tasa/bcv/", views.tasa_bcv, name="tasa_bcv"),
    path("factura/<int:factura_id>/asignar/", views.asignar_platillo, name="asignar"),
    path("factura/<int:factura_id>/asignar/quitar/", views.quitar_asignacion, name="quitar_asignacion"),
    path("tasa/", views.tasa_cambio, name="tasa"),
    path("tasa/guardar/", views.guardar_tasa, name="guardar_tasa"),
    path("cierre/", views.cierre, name="cierre"),
    path("cierre/generar/", views.generar_cierre, name="generar_cierre"),
    path("cierre/historial/", views.historial, name="historial"),
    path("cierre/<int:cierre_id>/", views.cierre_detalle, name="cierre_detalle"),
    path("cierre/<int:cierre_id>/csv/", views.cierre_csv, name="cierre_csv"),
    path("cierre/<int:cierre_id>/correo/", views.cierre_correo, name="cierre_correo"),
    path("clientes/", views.clientes, name="clientes"),
    path("clientes/buscar/", views.cliente_buscar, name="cliente_buscar"),
    path("clientes/<int:cliente_id>/", views.cliente_editar, name="cliente_editar"),
    path("reporte/", views.reporte_dia, name="reporte"),
]
