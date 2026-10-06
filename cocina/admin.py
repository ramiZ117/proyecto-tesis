from django.contrib import admin

from .models import PedidoKDS, PlatilloKDS


@admin.register(PedidoKDS)
class PedidoKDSAdmin(admin.ModelAdmin):
    list_display = ("id", "mesa", "estado", "enviado_cocina_en")
    list_filter = ("estado",)


@admin.register(PlatilloKDS)
class PlatilloKDSAdmin(admin.ModelAdmin):
    list_display = ("id", "pedido", "producto", "cantidad", "estado")
    list_filter = ("estado",)
