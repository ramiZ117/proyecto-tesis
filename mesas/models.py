from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Q
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

    class Tipo(models.TextChoices):
        MESA = "MES", "En mesa"
        LLEVAR = "LLE", "Para llevar"

    tipo = models.CharField(max_length=3, choices=Tipo.choices, default=Tipo.MESA)
    mesa = models.ForeignKey(Mesa, on_delete=models.PROTECT, related_name="pedidos", null=True, blank=True)
    cliente = models.CharField(max_length=80, blank=True, help_text="Nombre del cliente (pedidos para llevar).")
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
        constraints = [
            # Regla de negocio 1: una mesa tiene como máximo una comanda que no esté cerrada ni anulada.
            models.UniqueConstraint(
                fields=["mesa"],
                condition=~Q(estado__in=["CER", "ANU"]),
                name="uniq_pedido_abierto_por_mesa",
            ),
            # Un pedido en mesa tiene mesa; uno para llevar no la tiene.
            models.CheckConstraint(
                condition=(Q(tipo="MES") & Q(mesa__isnull=False)) | (Q(tipo="LLE") & Q(mesa__isnull=True)),
                name="pedido_mesa_segun_tipo",
            ),
        ]

    def __str__(self) -> str:
        return f"Pedido #{self.pk} · {self.etiqueta}"

    # ---- identificación --------------------------------------------------
    @property
    def es_para_llevar(self) -> bool:
        return self.tipo == self.Tipo.LLEVAR

    @property
    def codigo(self) -> str:
        """L-015 para los pedidos para llevar."""
        return f"L-{self.pk:03d}" if self.pk else "L-nuevo"

    @property
    def etiqueta(self) -> str:
        if self.es_para_llevar:
            return f"Llevar {self.codigo}" + (f" · {self.cliente}" if self.cliente else "")
        return str(self.mesa) if self.mesa_id else "Sin mesa"

    @classmethod
    def crear_para_llevar(cls, cliente: str, mesero) -> "Pedido":
        cliente = " ".join((cliente or "").split())[:80]
        if not cliente:
            raise ValidationError("Indica el nombre del cliente del pedido para llevar.")
        return cls.objects.create(tipo=cls.Tipo.LLEVAR, cliente=cliente, mesero=mesero)

    # ---- apertura --------------------------------------------------------
    @classmethod
    def abrir_para(cls, mesa_id: int, mesero) -> tuple["Pedido", bool]:
        """Abre la comanda de una mesa o devuelve la que ya está abierta.

        Devuelve (pedido, creado). La transacción toma el candado de escritura de la base
        (SQLite en modo IMMEDIATE), así que dos meseros que confirman a la vez se atienden
        en fila; la restricción `uniq_pedido_abierto_por_mesa` respalda la regla aunque
        la operación llegara por otro camino.
        """
        with transaction.atomic():
            mesa = Mesa.objects.select_for_update().get(pk=mesa_id, activa=True)
            if mesa.estado == Mesa.Estado.POR_LIMPIAR:
                raise ValidationError("La mesa debe limpiarse antes de sentar un nuevo cliente.")
            pedido = mesa.pedido_activo()
            if pedido is not None:
                return pedido, False
            try:
                with transaction.atomic():
                    pedido = cls.objects.create(mesa=mesa, mesero=mesero)
            except IntegrityError:
                # Otro mesero la abrió en el mismo instante: se devuelve la suya.
                return mesa.pedido_activo(), False
            mesa.ocupar()
            return pedido, True

    @transaction.atomic
    def mover_a(self, mesa_destino_id: int) -> None:
        """Cambia a los comensales de mesa: la comanda pasa completa a una mesa disponible."""
        if self.es_para_llevar:
            raise ValidationError("Un pedido para llevar no tiene mesa.")
        if self.estado in {self.Estado.CERRADO, self.Estado.ANULADO}:
            raise ValidationError("Este pedido ya está cerrado o anulado.")
        destino = Mesa.objects.select_for_update().filter(pk=mesa_destino_id, activa=True).first()
        if destino is None or destino.pk == self.mesa_id:
            raise ValidationError("Elige otra mesa del salón.")
        if destino.estado != Mesa.Estado.DISPONIBLE or destino.pedido_activo() is not None:
            raise ValidationError(f"La {destino} no está disponible.")
        origen = self.mesa
        estado_origen = origen.estado
        self.mesa = destino
        self.save(update_fields=["mesa", "actualizado_en"])
        destino._cambiar_estado(estado_origen if estado_origen != Mesa.Estado.DISPONIBLE else Mesa.Estado.OCUPADA)
        origen.marcar_disponible()

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
        if self.mesa and self.mesa.estado != Mesa.Estado.OCUPADA:
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
        if self.es_para_llevar:
            raise ValidationError("Los pedidos para llevar se cobran directo en caja.")
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
        from inventario.models import MovimientoStock

        for detalle in list(en_marcha.select_related("producto")):
            detalle._descontar_insumos(
                f"Merma: pedido #{self.pk} anulado con platillo ya preparado",
                tipo=MovimientoStock.Tipo.MERMA,
                motivo=MovimientoStock.Motivo.ANULACION,
            )
        self.detalles.exclude(estado=DetallePedido.Estado.ANULADO).update(estado=DetallePedido.Estado.ANULADO)
        self.estado = self.Estado.ANULADO
        self.cerrado_en = timezone.now()
        self.save(update_fields=["estado", "cerrado_en", "actualizado_en"])
        if self.mesa:
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
    listo_en = models.DateTimeField(null=True, blank=True, help_text="Momento en que cocina lo marcó listo.")
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
    def cambiar_cantidad(self, delta: int) -> None:
        """Suma o resta unidades a un platillo aún sin enviar; en cero se quita de la comanda."""
        self.pedido._exigir_abierto()
        if self.enviado:
            raise ValidationError("Ese platillo ya se envió a cocina; agrega uno nuevo o quítalo.")
        nueva = self.cantidad + delta
        if nueva <= 0:
            self.delete()
            return
        self.cantidad = nueva
        self.save(update_fields=["cantidad", "actualizado_en"])

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
        self.listo_en = timezone.now()
        self.save(update_fields=["estado", "listo_en", "actualizado_en"])
        self.pedido.sincronizar_estado_desde_detalles()

    def marcar_entregado(self) -> None:
        if self.estado != self.Estado.LISTO:
            raise ValidationError("Solo se entregan platillos listos para servir.")
        self.estado = self.Estado.ENTREGADO
        self.save(update_fields=["estado", "actualizado_en"])
        self.pedido.sincronizar_estado_desde_detalles()

    @transaction.atomic
    def _descontar_insumos(self, nota: str = "", tipo: str = "SAL", motivo: str = "") -> None:
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
                tipo=tipo,
                motivo=motivo,
                costo_unitario=insumo.costo_unitario,
                cantidad=salida,
                stock_resultante=insumo.stock_actual,
                detalle_pedido=self,
                nota=f"{nota} · faltante {faltante:f}" if faltante > 0 else nota,
            )
        self.insumos_descontados = True
        self.save(update_fields=["insumos_descontados", "actualizado_en"])
