from django.contrib import admin

from config.admin_base import SoloLecturaAdmin, SoloLecturaInline

from .models import DetallePedido, Mesa, Pedido


class DetallePedidoInline(SoloLecturaInline):
    model = DetallePedido


@admin.register(Mesa)
class MesaAdmin(admin.ModelAdmin):
    """Alta del plano del salón. El estado lo cambia la operación (abrir, cobrar, limpiar)."""

    list_display = ("numero", "capacidad", "estado", "activa", "pos_x", "pos_y")
    list_filter = ("estado", "activa")
    readonly_fields = ("estado",)


@admin.register(Pedido)
class PedidoAdmin(SoloLecturaAdmin):
    list_display = ("id", "mesa", "mesero", "estado", "creado_en")
    list_filter = ("estado",)
    inlines = [DetallePedidoInline]
