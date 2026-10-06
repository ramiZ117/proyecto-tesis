from django.contrib import admin

from .models import Asignacion, CierreCaja, Devolucion, Factura, Pago, TasaCambio


class PagoInline(admin.TabularInline):
    model = Pago
    extra = 0
    readonly_fields = ("moneda", "monto_local", "vuelto_local", "registrado_en")
    can_delete = False


class DevolucionInline(admin.TabularInline):
    model = Devolucion
    extra = 0
    can_delete = False
    readonly_fields = ("pago", "metodo", "moneda", "monto", "monto_local", "motivo", "registrado_por", "registrado_en")

    def has_add_permission(self, request, obj=None):
        return False


class AsignacionInline(admin.TabularInline):
    model = Asignacion
    extra = 0


@admin.register(TasaCambio)
class TasaCambioAdmin(admin.ModelAdmin):
    list_display = ("valor", "vigente_desde", "activa", "creada_por")
    list_filter = ("activa",)


@admin.register(Factura)
class FacturaAdmin(admin.ModelAdmin):
    list_display = ("numero", "pedido", "estado", "total_local", "total_usd", "emitida_en", "pagada_en")
    list_filter = ("estado",)
    inlines = [PagoInline, DevolucionInline, AsignacionInline]


@admin.register(CierreCaja)
class CierreCajaAdmin(admin.ModelAdmin):
    list_display = ("fecha", "cajero", "cantidad_facturas", "total_local", "total_usd", "hasta")
    list_filter = ("fecha",)
