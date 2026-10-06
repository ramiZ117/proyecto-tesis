from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


class Mesa(models.Model):
    class Estado(models.TextChoices):
        DISPONIBLE = "DIS", "Disponible"
        OCUPADA = "OCU", "Ocupada"
        POR_FACTURAR = "FAC", "Por facturar"
        POR_LIMPIAR = "LIM", "Por limpiar"

    numero = models.PositiveIntegerField(unique=True)
    capacidad = models.PositiveSmallIntegerField(default=4)
    estado = models.CharField(max_length=3, choices=Estado.choices, default=Estado.DISPONIBLE)
    activa = models.BooleanField(default=True)
    pos_x = models.PositiveSmallIntegerField(default=0, help_text="Columna en el mapa de mesas (0 = primera).")
    pos_y = models.PositiveSmallIntegerField(default=0, help_text="Fila en el mapa de mesas (0 = primera).")

    class Meta:
        verbose_name = "mesa"
        verbose_name_plural = "mesas"
        ordering = ["numero"]

    def __str__(self) -> str:
        return f"Mesa {self.numero}"

    def _cambiar_estado(self, nuevo: str) -> None:
        self.estado = nuevo
        self.save(update_fields=["estado"])

    def ocupar(self) -> None:
        self._cambiar_estado(self.Estado.OCUPADA)

    def marcar_por_facturar(self) -> None:
        self._cambiar_estado(self.Estado.POR_FACTURAR)

    def marcar_por_limpiar(self) -> None:
        self._cambiar_estado(self.Estado.POR_LIMPIAR)

    def marcar_disponible(self) -> None:
        self._cambiar_estado(self.Estado.DISPONIBLE)

    def pedido_activo(self):
        return (
            self.pedidos.exclude(estado__in=[Pedido.Estado.CERRADO, Pedido.Estado.ANULADO])
            .select_related("mesero")
            .order_by("-creado_en")
            .first()
        )


class Pedido(models.Model):
    class Estado(models.TextChoices):
        ABIERTO = "ABI", "Abierto"
        ENVIADO_COCINA = "ENV", "Enviado a cocina"
        EN_PREPARACION = "PRE", "En preparación"
        LISTO = "LIS", "Listo para servir"
        ENTREGADO = "ENT", "Entregado"
        CERRADO = "CER", "Cerrado"
        ANULADO = "ANU", "Anulado"

    mesa = models.ForeignKey(Mesa, on_delete=models.PROTECT, related_name="pedidos")
    mesero = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pedidos_tomados",
    )
    estado = models.CharField(max_length=3, choices=Estado.choices, default=Estado.ABIERTO)
    enviado_cocina_en = models.DateTimeField(null=True, blank=True)
    cerrado_en = models.DateTimeField(null=True, blank=True)
    notas = models.CharField(max_length=250, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "pedido"
        verbose_name_plural = "pedidos"
        ordering = ["-creado_en"]
        indexes = [
            models.Index(fields=["estado", "enviado_cocina_en"]),
            models.Index(fields=["mesa", "estado"]),
        ]

    def __str__(self) -> str:
        return f"Pedido #{self.pk} · {self.mesa}"

    # ---- consultas -------------------------------------------------------
    @property
    def detalles_vigentes(self):
        return self.detalles.exclude(estado=DetallePedido.Estado.ANULADO)

    @property
    def subtotal(self) -> Decimal:
        return sum((d.subtotal for d in self.detalles_vigentes), Decimal("0.00"))

    @property
    def sin_enviar(self):
        return self.detalles_vigentes.filter(enviado=False)

    @property
    def factura_activa(self):
        """Factura vigente del pedido (las anuladas no cuentan)."""
        return self.facturas.exclude(estado="ANU").first()

    # ---- acciones --------------------------------------------------------
    def _exigir_abierto(self) -> None:
        if self.estado in {self.Estado.CERRADO, self.Estado.ANULADO}:
            raise ValidationError("Este pedido ya está cerrado o anulado.")
        if self.factura_activa:
            raise ValidationError("La cuenta ya fue facturada; no se puede modificar el pedido.")

    @transaction.atomic
    def enviar_a_cocina(self) -> None:
        """Envía a cocina solo los platillos nuevos (RF-02: envío inmediato)."""
        self._exigir_abierto()
        if not self.detalles_vigentes.exists():
            raise ValidationError("No se puede enviar a cocina un pedido sin platillos.")
        nuevos = self.sin_enviar
        if not nuevos.exists():
            raise ValidationError("No hay platillos nuevos para enviar a cocina.")
        ahora = timezone.now()
        nuevos.update(enviado=True, enviado_en=ahora)
        if not self.enviado_cocina_en:
            self.enviado_cocina_en = ahora
            self.save(update_fields=["enviado_cocina_en", "actualizado_en"])
        self.sincronizar_estado_desde_detalles()
        if self.mesa.estado != Mesa.Estado.OCUPADA:
            self.mesa.ocupar()

    @transaction.atomic
    def solicitar_cuenta(self) -> None:
        """El mesero indica que la mesa pide la cuenta: la mesa pasa a "Por facturar"."""
        if self.estado in {self.Estado.CERRADO, self.Estado.ANULADO}:
            raise ValidationError("Este pedido ya está cerrado o anulado.")
        if not self.detalles_vigentes.exists():
            raise ValidationError("No hay platillos que cobrar en esta mesa.")
        if self.sin_enviar.exists():
            raise ValidationError("Hay platillos sin enviar a cocina. Envíalos antes de pedir la cuenta.")
        self.mesa.marcar_por_facturar()

    @transaction.atomic
    def anular(self, forzar: bool = False) -> None:
        """Anula el pedido. Si ya hay platillos en marcha solo un administrador puede (forzar)."""
        if self.estado in {self.Estado.CERRADO, self.Estado.ANULADO}:
            raise ValidationError("Este pedido ya está cerrado o anulado.")
        if self.factura_activa:
            raise ValidationError("El pedido tiene una factura vigente; anúlala primero en caja.")
        en_marcha = self.detalles.filter(
            estado__in=[DetallePedido.Estado.EN_PREPARACION, DetallePedido.Estado.LISTO, DetallePedido.Estado.ENTREGADO]
        )
        if en_marcha.exists() and not forzar:
            raise ValidationError(
                "Ya hay platillos en preparación o servidos. Solo un administrador puede anular este pedido."
            )
        # Lo que cocina ya preparó se consumió aunque no se cobre: queda registrado como merma.
        for detalle in list(en_marcha.select_related("producto")):
            detalle._descontar_insumos(f"Merma: pedido #{self.pk} anulado con platillo ya preparado")
        self.detalles.exclude(estado=DetallePedido.Estado.ANULADO).update(estado=DetallePedido.Estado.ANULADO)
        self.estado = self.Estado.ANULADO
        self.cerrado_en = timezone.now()
        self.save(update_fields=["estado", "cerrado_en", "actualizado_en"])
        self.mesa.marcar_disponible()

    @transaction.atomic
    def descontar_inventario(self) -> None:
        """RF-04: al procesar la venta (cuenta cobrada) descuenta las recetas de todos los platillos."""
        for detalle in self.detalles_vigentes.filter(insumos_descontados=False).select_related("producto"):
            detalle._descontar_insumos()

    def sincronizar_estado_desde_detalles(self) -> None:
        if self.estado in {self.Estado.CERRADO, self.Estado.ANULADO}:
            return
        vigentes = list(self.detalles_vigentes)
        if not vigentes:
            return
        enviados = [d for d in vigentes if d.enviado]
        todos_enviados = len(enviados) == len(vigentes)
        Det = DetallePedido.Estado
        if todos_enviados and all(d.estado == Det.ENTREGADO for d in vigentes):
            nuevo = self.Estado.ENTREGADO
        elif todos_enviados and all(d.estado in {Det.LISTO, Det.ENTREGADO} for d in vigentes):
            nuevo = self.Estado.LISTO
        elif any(d.estado == Det.EN_PREPARACION for d in enviados):
            nuevo = self.Estado.EN_PREPARACION
        elif enviados:
            nuevo = self.Estado.ENVIADO_COCINA
        else:
            nuevo = self.Estado.ABIERTO
        self.estado = nuevo
        self.save(update_fields=["estado", "actualizado_en"])


class DetallePedido(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "PEN", "Pendiente"
        EN_PREPARACION = "PRE", "En preparación"
        LISTO = "LIS", "Listo para servir"
        ENTREGADO = "ENT", "Entregado"
        ANULADO = "ANU", "Anulado"

    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="detalles")
    producto = models.ForeignKey("inventario.Producto", on_delete=models.PROTECT, related_name="detalles_pedido")
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    precio_unitario = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Precio congelado al momento de crear la comanda.",
    )
    observaciones = models.CharField(max_length=250, blank=True)
    estado = models.CharField(max_length=3, choices=Estado.choices, default=Estado.PENDIENTE)
    enviado = models.BooleanField(default=False, help_text="True cuando el mesero lo envió a cocina.")
    enviado_en = models.DateTimeField(null=True, blank=True)
    insumos_descontados = models.BooleanField(default=False)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "detalle de pedido"
        verbose_name_plural = "detalles de pedido"
        ordering = ["creado_en"]

    def __str__(self) -> str:
        return f"{self.cantidad} x {self.producto}"

    @property
    def subtotal(self) -> Decimal:
        return (self.precio_unitario * self.cantidad).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        if not self.precio_unitario:
            self.precio_unitario = self.producto.precio_local
        super().save(*args, **kwargs)

    # ---- transiciones de estado (sin retrocesos) -------------------------
    def cancelar(self) -> None:
        """Quita el platillo mientras cocina no lo haya empezado."""
        self.pedido._exigir_abierto()
        if self.estado != self.Estado.PENDIENTE:
            raise ValidationError("Solo se pueden quitar platillos que cocina aún no empezó a preparar.")
        if not self.enviado:
            self.delete()
            return
        self.estado = self.Estado.ANULADO
        self.save(update_fields=["estado", "actualizado_en"])
        self.pedido.sincronizar_estado_desde_detalles()

    def marcar_en_preparacion(self) -> None:
        if not self.enviado:
            raise ValidationError("Este platillo todavía no fue enviado a cocina.")
        if self.estado != self.Estado.PENDIENTE:
            raise ValidationError("Solo se puede preparar un platillo pendiente.")
        # El inventario NO se descuenta aquí: se descuenta al cobrar la cuenta (ver Pedido.descontar_inventario).
        # Mientras tanto los insumos quedan "comprometidos" y no se ofrecen en el menú.
        with transaction.atomic():
            self.estado = self.Estado.EN_PREPARACION
            self.save(update_fields=["estado", "actualizado_en"])
            self.pedido.sincronizar_estado_desde_detalles()

    def marcar_listo(self) -> None:
        if self.estado != self.Estado.EN_PREPARACION:
            raise ValidationError("El platillo debe estar en preparación para marcarlo listo.")
        self.estado = self.Estado.LISTO
        self.save(update_fields=["estado", "actualizado_en"])
        self.pedido.sincronizar_estado_desde_detalles()

    def marcar_entregado(self) -> None:
        if self.estado != self.Estado.LISTO:
            raise ValidationError("Solo se entregan platillos listos para servir.")
        self.estado = self.Estado.ENTREGADO
        self.save(update_fields=["estado", "actualizado_en"])
        self.pedido.sincronizar_estado_desde_detalles()

    @transaction.atomic
    def _descontar_insumos(self, nota: str = "") -> None:
        """Descuenta del stock la receta de este platillo (una sola vez) y deja el movimiento.

        Nunca bloquea una venta: si el stock registrado no alcanza (la comida ya se sirvió),
        se descuenta lo que haya, el stock queda en cero y la nota deja constancia del faltante.
        """
        from inventario.models import Insumo, MovimientoStock, Receta

        if self.insumos_descontados:
            return

        nota = nota or f"Venta pedido #{self.pedido_id}"
        recetas = list(Receta.objects.filter(producto=self.producto).order_by("insumo_id"))
        insumos = {
            i.pk: i
            for i in Insumo.objects.select_for_update().filter(pk__in=[r.insumo_id for r in recetas]).order_by("pk")
        }

        for receta in recetas:
            requerido = receta.cantidad * self.cantidad
            insumo = insumos[receta.insumo_id]
            salida = min(requerido, insumo.stock_actual)
            faltante = requerido - salida
            insumo.stock_actual -= salida
            insumo.save(update_fields=["stock_actual"])
            MovimientoStock.objects.create(
                insumo=insumo,
                tipo=MovimientoStock.Tipo.SALIDA,
                cantidad=salida,
                stock_resultante=insumo.stock_actual,
                detalle_pedido=self,
                nota=f"{nota} · faltante {faltante:f}" if faltante > 0 else nota,
            )
        self.insumos_descontados = True
        self.save(update_fields=["insumos_descontados", "actualizado_en"])
