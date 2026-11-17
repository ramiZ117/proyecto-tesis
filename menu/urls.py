from django.urls import path

from . import views

app_name = "menu"

urlpatterns = [
    path("", views.carta, name="carta"),
    path("asistente/", views.asistente, name="asistente"),
    path("solicitar/", views.solicitar, name="solicitar"),
    path("solicitud/estado/", views.estado_solicitud, name="estado_solicitud"),
    path("cuenta/", views.pedir_cuenta, name="pedir_cuenta"),
    path("retiro/cotizar/", views.retiro_cotizar, name="retiro_cotizar"),
    path("retiro/", views.retiro_crear, name="retiro_crear"),
    path("retiro/<str:token>/", views.retiro_estado, name="retiro_estado"),
    path("cuenta/reportar/", views.reportar_pago, name="reportar_pago"),
    path("contacto.vcf", views.contacto, name="contacto"),
]
