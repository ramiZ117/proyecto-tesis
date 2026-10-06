from django.contrib import admin

from .models import DetallePedido, Mesa, Pedido


class DetallePedidoInline(admin.TabularInline):
    model = DetallePedido
    extra = 1


@admin.register(Mesa)
class MesaAdmin(admin.ModelAdmin):
    list_display = ("numero", "capacidad", "estado", "activa")
    list_filter = ("estado", "activa")


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ("id", "mesa", "mesero", "estado", "creado_en")
    list_filter = ("estado",)
    inlines = [DetallePedidoInline]
