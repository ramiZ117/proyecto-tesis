from decimal import Decimal

from django.contrib import admin

from .models import Categoria, Insumo, MovimientoStock, Producto, Receta


class RecetaInline(admin.TabularInline):
    model = Receta
    extra = 1


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activa")
    list_filter = ("activa",)


@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "grupo", "unidad", "stock_actual", "stock_minimo", "activo")
    list_filter = ("activo", "unidad", "grupo")
    search_fields = ("nombre",)

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
                cantidad=abs(obj.stock_actual - anterior) or Decimal("0.001"),
                stock_resultante=obj.stock_actual,
                nota=f"Ajuste manual por {request.user.username}: {anterior} → {obj.stock_actual}",
            )


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "precio_usd", "disponible")
    list_filter = ("categoria", "disponible")
    search_fields = ("nombre",)
    inlines = [RecetaInline]


@admin.register(MovimientoStock)
class MovimientoStockAdmin(admin.ModelAdmin):
    list_display = ("creado_en", "tipo", "insumo", "cantidad", "stock_resultante")
    list_filter = ("tipo",)
    readonly_fields = ("creado_en",)
