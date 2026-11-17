from django.contrib import admin

from config.admin_base import SoloLecturaAdmin

from .models import PedidoKDS, PlatilloKDS


@admin.register(PedidoKDS)
class PedidoKDSAdmin(SoloLecturaAdmin):
    list_display = ("id", "mesa", "estado", "enviado_cocina_en")
    list_filter = ("estado",)


@admin.register(PlatilloKDS)
class PlatilloKDSAdmin(SoloLecturaAdmin):
    list_display = ("id", "pedido", "producto", "cantidad", "estado")
    list_filter = ("estado",)
