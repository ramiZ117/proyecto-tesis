from django.contrib import admin

from config.admin_base import SoloLecturaAdmin, SoloLecturaInline

from .models import Asignacion, CierreCaja, Devolucion, Factura, Pago, TasaCambio


class PagoInline(SoloLecturaInline):
    model = Pago


class DevolucionInline(SoloLecturaInline):
    model = Devolucion


class AsignacionInline(SoloLecturaInline):
    model = Asignacion


@admin.register(TasaCambio)
class TasaCambioAdmin(SoloLecturaAdmin):
    """La tasa se publica desde Caja → Tasa de cambio, que desactiva la anterior."""

    list_display = ("valor", "vigente_desde", "activa", "creada_por")
    list_filter = ("activa",)


@admin.register(Factura)
class FacturaAdmin(SoloLecturaAdmin):
    list_display = ("numero", "pedido", "estado", "total_local", "total_usd", "emitida_en", "pagada_en")
    list_filter = ("estado",)
    search_fields = ("numero",)
    inlines = [PagoInline, DevolucionInline, AsignacionInline]


@admin.register(CierreCaja)
class CierreCajaAdmin(SoloLecturaAdmin):
    list_display = ("fecha", "cajero", "secuencia", "cantidad_facturas", "total_local", "total_usd", "hasta")
    list_filter = ("fecha", "cajero")
