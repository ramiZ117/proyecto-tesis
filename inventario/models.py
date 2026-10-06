from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class Categoria(models.Model):
    nombre = models.CharField(max_length=80, unique=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "categoría"
        verbose_name_plural = "categorías"
        ordering = ["nombre"]

    def __str__(self) -> str:
        return self.nombre


class Insumo(models.Model):
    class Unidad(models.TextChoices):
        UNIDAD = "UN", "Unidad"
        GRAMO = "G", "Gramo"
        KILOGRAMO = "KG", "Kilogramo"
        MILILITRO = "ML", "Mililitro"
        LITRO = "L", "Litro"

    nombre = models.CharField(max_length=120, unique=True)
    grupo = models.CharField(
        max_length=3,
        choices=[
            ("CAR", "Carnes"),
            ("BEB", "Bebidas"),
            ("VEG", "Vegetales"),
        ],
        default="VEG",
        help_text="Clasificación de inventario.",
    )
    unidad = models.CharField(max_length=4, choices=Unidad.choices, default=Unidad.UNIDAD)
    stock_actual = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    stock_minimo = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "insumo"
        verbose_name_plural = "insumos"
        ordering = ["nombre"]

    def __str__(self) -> str:
        return f"{self.nombre} ({self.stock_actual} {self.get_unidad_display()})"

    @property
    def stock_bajo(self) -> bool:
        return self.stock_actual <= self.stock_minimo


class Producto(models.Model):
    categoria = models.ForeignKey(
        Categoria,
        on_delete=models.PROTECT,
        related_name="productos",
    )
    nombre = models.CharField(max_length=120)
    descripcion = models.TextField(blank=True)
    precio_usd = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Precio de venta en USD. La moneda local se calcula con la tasa vigente.",
    )
    disponible = models.BooleanField(default=True)

    class Meta:
        verbose_name = "producto"
        verbose_name_plural = "productos"
        ordering = ["categoria__nombre", "nombre"]
        constraints = [
            models.UniqueConstraint(fields=["categoria", "nombre"], name="uniq_producto_categoria_nombre"),
        ]

    def __str__(self) -> str:
        return self.nombre

    @property
    def precio_local(self) -> Decimal:
        """Equivalente en moneda local con la tasa vigente (siempre actualizado)."""
        from config.dinero import precio_local_vigente

        return precio_local_vigente(self.precio_usd)


class Receta(models.Model):
    """Lista de insumos que consume un platillo (BOM)."""

    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name="recetas",
    )
    insumo = models.ForeignKey(
        Insumo,
        on_delete=models.PROTECT,
        related_name="recetas",
    )
    cantidad = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
        help_text="Cantidad de insumo por 1 unidad de producto.",
    )

    class Meta:
        verbose_name = "receta"
        verbose_name_plural = "recetas"
        constraints = [
            models.UniqueConstraint(fields=["producto", "insumo"], name="uniq_receta_producto_insumo"),
        ]

    def __str__(self) -> str:
        return f"{self.producto} → {self.cantidad} {self.insumo}"


class MovimientoStock(models.Model):
    class Tipo(models.TextChoices):
        ENTRADA = "ENT", "Entrada"
        SALIDA = "SAL", "Salida (venta)"
        AJUSTE = "AJU", "Ajuste"

    insumo = models.ForeignKey(Insumo, on_delete=models.PROTECT, related_name="movimientos")
    tipo = models.CharField(max_length=3, choices=Tipo.choices)
    cantidad = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    stock_resultante = models.DecimalField(max_digits=12, decimal_places=3)
    detalle_pedido = models.ForeignKey(
        "mesas.DetallePedido",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_stock",
    )
    nota = models.CharField(max_length=200, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "movimiento de stock"
        verbose_name_plural = "movimientos de stock"
        ordering = ["-creado_en"]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} {self.cantidad} {self.insumo.nombre}"
