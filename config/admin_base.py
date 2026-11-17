"""Piezas comunes del panel de administración."""

from django.contrib import admin


class SoloLecturaMixin:
    """Registro de consulta: el panel muestra los datos, pero no permite crear, editar ni borrar.

    Se usa en las tablas transaccionales (facturas, pagos, devoluciones, cierres, movimientos de
    stock, comandas). Esos registros solo cambian a través de los métodos de dominio que aplican
    las reglas de negocio (Factura.cobrar, Pedido.anular, CierreCaja.generar…); editarlos a mano
    desde el panel rompería la auditoría.
    """

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SoloLecturaAdmin(SoloLecturaMixin, admin.ModelAdmin):
    pass


class SoloLecturaInline(SoloLecturaMixin, admin.TabularInline):
    extra = 0
