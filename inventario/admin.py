from decimal import Decimal

from django.contrib import admin
from django.core.exceptions import ValidationError
from django import forms

from config.admin_base import SoloLecturaAdmin

from .models import Categoria, Insumo, MovimientoStock, Producto, Proveedor, Receta


class RecetaInline(admin.TabularInline):
    model = Receta
    extra = 1
    autocomplete_fields = ["insumo"]


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "estacion", "orden", "activa")
    list_editable = ("estacion", "orden", "activa")
    list_filter = ("activa", "estacion")


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ("nombre", "contacto", "telefono", "activo")
    list_filter = ("activo",)
    search_fields = ("nombre", "contacto")


class InsumoForm(forms.ModelForm):
    class Meta:
        model = Insumo
        fields = "__all__"

    def clean(self):
        datos = super().clean()
        minimo = datos.get("stock_minimo") or Decimal("0")
        reposicion = datos.get("punto_reposicion") or Decimal("0")
        maximo = datos.get("stock_maximo") or Decimal("0")
        if reposicion and reposicion < minimo:
            raise ValidationError("El punto de reposición no puede ser menor que el stock mínimo.")
        if maximo and maximo < reposicion:
            raise ValidationError("El stock máximo no puede ser menor que el punto de reposición.")
        return datos


@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    form = InsumoForm
    list_display = ("nombre", "grupo", "unidad", "stock_actual", "stock_minimo", "punto_reposicion", "stock_maximo",
                    "costo_unitario", "proveedor", "activo")
    list_filter = ("activo", "grupo", "proveedor")
    search_fields = ("nombre",)
    fieldsets = (
        (None, {"fields": ("nombre", "grupo", "unidad", "proveedor", "costo_unitario", "activo")}),
        ("Niveles de stock", {"fields": ("stock_actual", "stock_minimo", "punto_reposicion", "stock_maximo")}),
    )

    def save_model(self, request, obj, form, change):
        """Cada cambio manual de stock queda registrado como ajuste auditable."""
        anterior = None
        if change and "stock_actual" in form.changed_data:
            anterior = Insumo.objects.get(pk=obj.pk).stock_actual
        super().save_model(request, obj, form, change)
        if anterior is not None and anterior != obj.stock_actual:
            MovimientoStock.objects.create(
                insumo=obj,
                tipo=MovimientoStock.Tipo.AJUSTE,
                motivo=MovimientoStock.Motivo.CONTEO,
                cantidad=abs(obj.stock_actual - anterior) or Decimal("0.001"),
                stock_resultante=obj.stock_actual,
                costo_unitario=obj.costo_unitario,
                usuario=request.user,
                nota=f"Ajuste manual por {request.user.username}: {anterior} → {obj.stock_actual}",
            )


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "precio_usd", "disponible")
    list_editable = ("precio_usd", "disponible")
    list_filter = ("categoria", "disponible")
    search_fields = ("nombre",)
    inlines = [RecetaInline]


@admin.register(MovimientoStock)
class MovimientoStockAdmin(SoloLecturaAdmin):
    """Historial de almacén: nace de entradas, ventas, mermas y ajustes; no se edita a mano."""

    list_display = ("creado_en", "tipo", "motivo", "insumo", "cantidad", "stock_resultante", "usuario", "nota")
    list_filter = ("tipo", "motivo", "insumo")
