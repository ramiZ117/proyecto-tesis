from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.db import models


class Categoria(models.Model):
    class Estacion(models.TextChoices):
        PARRILLA = "PAR", "Parrilla"
        FRIOS = "FRI", "Fríos"
        BEBIDAS = "BEB", "Bebidas"

    nombre = models.CharField(max_length=80, unique=True)
    activa = models.BooleanField(default=True)
    estacion = models.CharField(
        max_length=3,
        choices=Estacion.choices,
        default=Estacion.PARRILLA,
        help_text="Estación de cocina que prepara los platos de esta categoría (filtro del monitor).",
    )
    orden = models.PositiveSmallIntegerField(default=0, help_text="Orden en la carta (menor primero).")

    class Meta:
        verbose_name = "categoría"
        verbose_name_plural = "categorías"
        ordering = ["orden", "nombre"]

    def __str__(self) -> str:
        return self.nombre


class Proveedor(models.Model):
    nombre = models.CharField(max_length=120, unique=True)
    contacto = models.CharField(max_length=120, blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "proveedor"
        verbose_name_plural = "proveedores"
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

    class Grupo(models.TextChoices):
        CARNES = "CAR", "Carnes"
        GRANOS = "GRA", "Granos y legumbres"
        PANADERIA = "PAN", "Panadería"
        VEGETALES = "VEG", "Vegetales y hierbas"
        LACTEOS = "LAC", "Lácteos"
        ESPECIAS = "ESP", "Especias y salsas"
        BEBIDAS = "BEB", "Bebidas"
        OTROS = "OTR", "Otros"

    ABREVIATURA = {"UN": "und", "G": "g", "KG": "kg", "ML": "ml", "L": "L"}

    nombre = models.CharField(max_length=120, unique=True)
    grupo = models.CharField(max_length=3, choices=Grupo.choices, default=Grupo.OTROS, help_text="Clasificación de inventario.")
    unidad = models.CharField(max_length=4, choices=Unidad.choices, default=Unidad.UNIDAD)
    stock_actual = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal("0.000"), validators=[MinValueValidator(Decimal("0"))]
    )
    stock_minimo = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal("0.000"), validators=[MinValueValidator(Decimal("0"))],
        help_text="Inventario de seguridad: por debajo de este nivel el insumo está en estado crítico.",
    )
    punto_reposicion = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal("0.000"), validators=[MinValueValidator(Decimal("0"))],
        help_text="Al llegar a este nivel el insumo aparece en la lista de compra sugerida.",
    )
    stock_maximo = models.DecimalField(
        max_digits=12, decimal_places=3, default=Decimal("0.000"), validators=[MinValueValidator(Decimal("0"))],
        help_text="Nivel hasta el que conviene reponer (evita comprar de más perecederos).",
    )
    costo_unitario = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0.0000"), validators=[MinValueValidator(Decimal("0"))],
        help_text="Costo de compra en USD por unidad de medida.",
    )
    proveedor = models.ForeignKey(
        Proveedor, on_delete=models.SET_NULL, null=True, blank=True, related_name="insumos"
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "insumo"
        verbose_name_plural = "insumos"
        ordering = ["nombre"]

    def __str__(self) -> str:
        return f"{self.nombre} ({self.stock_actual} {self.get_unidad_display()})"

    @property
    def abreviatura(self) -> str:
        return self.ABREVIATURA.get(self.unidad, self.unidad)

    @property
    def stock_bajo(self) -> bool:
        return self.stock_actual <= self.stock_minimo


class Producto(models.Model):
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="productos")
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
    foto = models.ImageField(upload_to="platos/", blank=True, help_text="Foto del plato para el menú digital.")
    foto_credito = models.CharField(
        max_length=200, blank=True, help_text="Autor y licencia si la foto no es propia (se muestra en el menú)."
    )
    destacado = models.BooleanField(default=False, help_text="Aparece en «Recomendados de la casa».")
    opciones = models.TextField(
        blank=True,
        help_text="Personalizaciones que permite el plato, una por línea (ej.: Sin vegetales, Sin salsa de ajo).",
    )
    minutos_preparacion = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(1)],
        help_text="Tiempo promedio en cocina; con él se estima la espera del cliente.",
    )
    vegetariano = models.BooleanField(default=False)
    picante = models.BooleanField(default=False)
    sin_gluten = models.BooleanField(default=False)

    class Meta:
        verbose_name = "producto"
        verbose_name_plural = "productos"
        ordering = ["categoria__orden", "categoria__nombre", "nombre"]
        constraints = [
            models.UniqueConstraint(fields=["categoria", "nombre"], name="uniq_producto_categoria_nombre"),
        ]

    @property
    def lista_opciones(self) -> list[str]:
        vistas, lista = set(), []
        for linea in (self.opciones or "").splitlines():
            texto = " ".join(linea.split())[:40]
            if texto and texto.casefold() not in vistas:
                vistas.add(texto.casefold())
                lista.append(texto)
        return lista[:12]

    def __str__(self) -> str:
        return self.nombre

    @property
    def etiquetas(self) -> list[str]:
        return [n for n, activa in (("Recomendado", self.destacado), ("Vegetariano", self.vegetariano),
                                    ("Picante", self.picante), ("Sin gluten", self.sin_gluten)) if activa]

    @property
    def precio_local(self) -> Decimal:
        """Equivalente en moneda local con la tasa vigente (siempre actualizado)."""
        from config.dinero import precio_local_vigente

        return precio_local_vigente(self.precio_usd)


class Receta(models.Model):
    """Lista de insumos que consume un platillo (BOM)."""

    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="recetas")
    insumo = models.ForeignKey(Insumo, on_delete=models.PROTECT, related_name="recetas")
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
        MERMA = "MER", "Merma"
        AJUSTE = "AJU", "Ajuste"

    class Motivo(models.TextChoices):
        VENCIDO = "VEN", "Vencido"
        DANADO = "DAN", "Dañado"
        ERROR = "ERR", "Error de preparación"
        ANULACION = "ANU", "Comanda anulada ya preparada"
        CONTEO = "CON", "Conteo físico"
        OTRO = "OTR", "Otro"

    insumo = models.ForeignKey(Insumo, on_delete=models.PROTECT, related_name="movimientos")
    tipo = models.CharField(max_length=3, choices=Tipo.choices)
    motivo = models.CharField(max_length=3, choices=Motivo.choices, blank=True)
    cantidad = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    stock_anterior = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True, help_text="Existencia justo antes del movimiento."
    )
    stock_resultante = models.DecimalField(max_digits=12, decimal_places=3)
    documento = models.CharField(
        max_length=60, blank=True,
        help_text="Soporte del movimiento: factura o nota de entrega del proveedor, factura de venta, acta de conteo…",
    )
    costo_unitario = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0.0000"),
        help_text="Costo USD por unidad al momento del movimiento (valoriza mermas y compras).",
    )
    detalle_pedido = models.ForeignKey(
        "mesas.DetallePedido", on_delete=models.SET_NULL, null=True, blank=True, related_name="movimientos_stock"
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="movimientos_stock"
    )
    nota = models.CharField(max_length=200, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "movimiento de stock"
        verbose_name_plural = "movimientos de stock"
        ordering = ["-creado_en", "-id"]
        indexes = [
            models.Index(fields=["insumo", "creado_en"]),
            models.Index(fields=["tipo", "creado_en"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} {self.cantidad} {self.insumo.nombre}"

    # Registro de auditoría: un movimiento no se corrige ni se borra; se compensa con otro (p. ej. un ajuste).
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Los movimientos de inventario no se modifican: registra un ajuste por conteo.")
        if self.stock_anterior is None:
            if self.tipo == self.Tipo.ENTRADA:
                self.stock_anterior = self.stock_resultante - self.cantidad
            elif self.tipo in (self.Tipo.SALIDA, self.Tipo.MERMA):
                self.stock_anterior = self.stock_resultante + self.cantidad
            else:
                raise ValidationError("Un ajuste debe indicar la existencia anterior.")
        if not self.documento and self.detalle_pedido_id:
            pedido = self.detalle_pedido.pedido
            factura = pedido.factura_activa
            self.documento = f"Factura {factura.numero}" if factura else f"Pedido {pedido.etiqueta}"[:60]
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Los movimientos de inventario no se eliminan: forman parte de la auditoría.")

    @property
    def variacion(self) -> Decimal:
        """Cambio con signo: positivo si entró existencia, negativo si salió."""
        return self.stock_resultante - (self.stock_anterior if self.stock_anterior is not None else self.stock_resultante)

    @property
    def es_ingreso(self) -> bool:
        return self.variacion > 0

    @property
    def valor_usd(self) -> Decimal:
        return (self.cantidad * self.costo_unitario).quantize(Decimal("0.01"))

    @property
    def valor_usd(self) -> Decimal:
        return (self.cantidad * self.costo_unitario).quantize(Decimal("0.01"))


class Promocion(models.Model):
    """Combo con precio especial, p. ej. «2 shawarmas + 1 refresco por $14»."""

    nombre = models.CharField(max_length=120, unique=True)
    descripcion = models.CharField(max_length=300, blank=True)
    precio_usd = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Precio del combo en USD.",
    )
    foto = models.ImageField(upload_to="promociones/", blank=True)
    activa = models.BooleanField(default=True)
    desde = models.DateField(null=True, blank=True, help_text="Vacío: desde ya.")
    hasta = models.DateField(null=True, blank=True, help_text="Vacío: sin fecha de fin.")
    orden = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "promoción"
        verbose_name_plural = "promociones"
        ordering = ["orden", "nombre"]

    def __str__(self) -> str:
        return self.nombre

    @classmethod
    def vigentes(cls, fecha=None):
        from django.db.models import Q
        from django.utils import timezone

        fecha = fecha or timezone.localdate()
        return (
            cls.objects.filter(activa=True)
            .filter(Q(desde__isnull=True) | Q(desde__lte=fecha))
            .filter(Q(hasta__isnull=True) | Q(hasta__gte=fecha))
            .prefetch_related("items__producto")
        )

    @property
    def precio_normal_usd(self) -> Decimal:
        return sum((i.producto.precio_usd * i.cantidad for i in self.items.all()), Decimal("0.00"))

    @property
    def ahorro_usd(self) -> Decimal:
        return max(self.precio_normal_usd - self.precio_usd, Decimal("0.00"))

    @property
    def disponible(self) -> bool:
        items = list(self.items.all())
        return bool(items) and all(i.producto.disponible for i in items)


class PromocionItem(models.Model):
    promocion = models.ForeignKey(Promocion, on_delete=models.CASCADE, related_name="items")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name="en_promociones")
    cantidad = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "plato de la promoción"
        verbose_name_plural = "platos de la promoción"
        constraints = [models.UniqueConstraint(fields=["promocion", "producto"], name="uniq_promocion_producto")]

    def __str__(self) -> str:
        return f"{self.cantidad} × {self.producto}"
