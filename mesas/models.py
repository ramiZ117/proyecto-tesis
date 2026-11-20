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

    class Forma(models.TextChoices):
        REDONDA = "RED", "Redonda"
        CUADRADA = "CUA", "Cuadrada"
        RECTANGULAR = "REC", "Rectangular (ocupa dos columnas)"
        ARCO = "ARC", "Media luna"
        BARRA = "BAR", "Barra"

    numero = models.PositiveIntegerField(unique=True)
    capacidad = models.PositiveSmallIntegerField(default=4)
    forma = models.CharField(max_length=3, choices=Forma.choices, default=Forma.ARCO)
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

    @property
    def ancho(self) -> int:
        """Columnas que ocupa en el plano (la rectangular ocupa dos)."""
        return 2 if self.forma == self.Forma.RECTANGULAR else 1

    def celdas(self) -> set[tuple[int, int]]:
        return {(self.pos_x + i, self.pos_y) for i in range(self.ancho)}

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
    en_linea = models.BooleanField(
        default=False, help_text="Pedido para retirar hecho y pagado desde el menú en línea."
    )
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
    @transaction.atomic
    def agregar_promocion(self, promocion, veces: int = 1, total_local=None) -> list:
        """Agrega un combo: una línea por unidad de cada plato, con el precio del combo repartido exacto.

        Cocina e inventario las ven como platos normales; el total de las líneas es el precio del combo.
        """
        from caja.models import TasaCambio

        self._exigir_abierto()
        if veces < 1 or veces > 20:
            raise ValidationError("Cantidad de promociones no válida.")
        items = list(promocion.items.select_related("producto"))
        if not promocion.activa or not items:
            raise ValidationError("Esa promoción no está disponible.")
        if any(not i.producto.disponible for i in items):
            raise ValidationError("Algún plato de la promoción está fuera de la carta.")
        total_combo = total_local if total_local is not None else TasaCambio.vigente().a_local(promocion.precio_usd)
        unidades = [i.producto for i in items for _ in range(i.cantidad)]
        normal = sum((p.precio_usd for p in unidades), Decimal("0"))
        creados = []
        for _ in range(veces):
            grupo = (self.detalles.aggregate(m=models.Max("promo_grupo"))["m"] or 0) + 1
            asignado = Decimal("0.00")
            for n, producto in enumerate(unidades):
                if n == len(unidades) - 1:
                    precio = total_combo - asignado  # la última línea absorbe el redondeo
                else:
                    parte = producto.precio_usd / normal if normal else Decimal(1) / len(unidades)
                    precio = (total_combo * parte).quantize(Decimal("0.01"))
                asignado += precio
                creados.append(DetallePedido.objects.create(
                    pedido=self, producto=producto, cantidad=1, precio_unitario=max(precio, Decimal("0.00")),
                    observaciones=f"Promo: {promocion.nombre}"[:250], promocion=promocion, promo_grupo=grupo,
                ))
        if self.mesa and self.mesa.estado != Mesa.Estado.OCUPADA:
            self.mesa.ocupar()
        return creados

    def _exigir_abierto(self) -> None:
        if self.estado in {self.Estado.CERRADO, self.Estado.ANULADO}:
            raise ValidationError("Este pedido ya está cerrado o anulado.")
        if self.factura_activa:
            raise ValidationError("La cuenta ya fue facturada; no se puede modificar el pedido.")

    @property
    def se_cobra_antes(self) -> bool:
        """Para llevar del mostrador: primero se cobra y, al quedar pagado, se envía solo a cocina.

        (Los pedidos en línea ya llegan pagados y los acepta la caja; las mesas se cobran al final.)
        """
        return self.es_para_llevar and not self.en_linea

    @transaction.atomic
    def enviar_a_cocina(self) -> None:
        """Envía a cocina solo los platillos nuevos (RF-02: envío inmediato)."""
        if self.se_cobra_antes:
            raise ValidationError("Los pedidos para llevar se cobran primero: al cobrarlos se envían solos a cocina.")
        self._exigir_abierto()
        self._enviar_nuevos()

    def _enviar_nuevos(self) -> None:
        if not self.detalles_vigentes.exists():
            raise ValidationError("No se puede enviar a cocina un pedido sin platillos.")
        nuevos = self.sin_enviar
        if not nuevos.exists():
            raise ValidationError("No hay platillos nuevos para enviar a cocina.")
        ahora = timezone.now()
        ids = list(nuevos.values_list("pk", flat=True))
        nuevos.update(enviado=True, enviado_en=ahora)
        # Las bebidas de nevera no pasan por cocina: quedan listas para que el mesero las lleve.
        DetallePedido.objects.filter(pk__in=ids, producto__bebida="NEV").update(
            estado=DetallePedido.Estado.LISTO, preparacion_en=ahora, listo_en=ahora
        )
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
        if self.es_para_llevar:
            raise ValidationError("Los pedidos para llevar se cobran directo en caja.")
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
        ).exclude(estado=DetallePedido.Estado.LISTO, producto__bebida="NEV")  # la bebida sin entregar vuelve a la nevera
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
        from cocina.models import AvisoCocina

        AvisoCocina.por_anulacion(self, list(self.detalles.select_related("producto__categoria")), todo=True)
        self.detalles.exclude(estado=DetallePedido.Estado.ANULADO).update(
            estado=DetallePedido.Estado.ANULADO, actualizado_en=timezone.now()
        )
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
        cerrar = False
        if nuevo == self.Estado.ENTREGADO and self.es_para_llevar:
            factura = self.factura_activa
            cerrar = factura is not None and factura.estado == "PAG"  # pagado y entregado: listo
        if cerrar:
            self.estado, self.cerrado_en = self.Estado.CERRADO, timezone.now()
            self.save(update_fields=["estado", "cerrado_en", "actualizado_en"])
            return
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
    preparacion_en = models.DateTimeField(null=True, blank=True, help_text="Momento en que cocina empezó a prepararlo.")
    listo_en = models.DateTimeField(null=True, blank=True, help_text="Momento en que cocina lo marcó listo.")
    insumos_descontados = models.BooleanField(default=False)
    promocion = models.ForeignKey(
        "inventario.Promocion", on_delete=models.SET_NULL, null=True, blank=True, related_name="detalles_pedido",
        help_text="Combo al que pertenece la línea (su precio es la parte del precio del combo).",
    )
    promo_grupo = models.PositiveIntegerField(null=True, blank=True, help_text="Agrupa las líneas de un mismo combo.")
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
        if self.promo_grupo:
            raise ValidationError("Es parte de una promoción: quita la promoción completa o agrégala otra vez.")
        if self.enviado:
            raise ValidationError("Ese platillo ya se envió a cocina; agrega uno nuevo o quítalo.")
        nueva = self.cantidad + delta
        if nueva <= 0:
            self.delete()
            return
        self.cantidad = nueva
        self.save(update_fields=["cantidad", "actualizado_en"])

    @property
    def se_puede_quitar(self) -> bool:
        """Lo que cocina no empezó; una bebida de nevera se puede quitar mientras no se haya entregado."""
        if self.estado == self.Estado.PENDIENTE:
            return True
        return self.estado == self.Estado.LISTO and self.producto.bebida == "NEV"

    def cancelar(self) -> None:
        """Quita el platillo mientras cocina no lo haya empezado (una promoción se quita completa)."""
        self.pedido._exigir_abierto()
        if self.promo_grupo:
            grupo = list(self.pedido.detalles.filter(promo_grupo=self.promo_grupo).exclude(estado=self.Estado.ANULADO))
            if not all(d.se_puede_quitar for d in grupo):
                raise ValidationError("Cocina ya empezó parte de esta promoción: no se puede quitar.")
            with transaction.atomic():
                from cocina.models import AvisoCocina

                AvisoCocina.por_anulacion(self.pedido, grupo)
                for d in grupo:
                    if d.enviado:
                        d.estado = self.Estado.ANULADO
                        d.save(update_fields=["estado", "actualizado_en"])
                    else:
                        d.delete()
                self.pedido.sincronizar_estado_desde_detalles()
            return
        if not self.se_puede_quitar:
            raise ValidationError("Solo se pueden quitar platillos que cocina aún no empezó a preparar.")
        if not self.enviado:
            self.delete()
            return
        from cocina.models import AvisoCocina

        AvisoCocina.por_anulacion(self.pedido, [self])
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
            self.preparacion_en = timezone.now()
            self.save(update_fields=["estado", "preparacion_en", "actualizado_en"])
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


class SolicitudCliente(models.Model):
    """Pedido que arma el comensal en el menú digital y «Solicitar mesero».

    No va a cocina: el mesero que atiende la mesa lo revisa con el cliente, lo carga en la comanda
    (puede editarlo) y lo envía. Así la toma del pedido se reduce a confirmar.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "PEN", "Pendiente"
        CARGADA = "CAR", "Cargada a la comanda"
        DESCARTADA = "DES", "Descartada"

    mesa = models.ForeignKey(Mesa, on_delete=models.PROTECT, related_name="solicitudes")
    estado = models.CharField(max_length=3, choices=Estado.choices, default=Estado.PENDIENTE)
    items = models.JSONField(default=list, blank=True, help_text="[{tipo: producto|promocion, id, cantidad, nota}]")
    nota = models.CharField(max_length=200, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)
    atendida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="solicitudes_atendidas"
    )
    atendida_en = models.DateTimeField(null=True, blank=True)
    pedido = models.ForeignKey(Pedido, on_delete=models.SET_NULL, null=True, blank=True, related_name="solicitudes")

    class Meta:
        verbose_name = "solicitud del cliente"
        verbose_name_plural = "solicitudes de clientes"
        ordering = ["creada_en"]
        constraints = [
            models.UniqueConstraint(fields=["mesa"], condition=Q(estado="PEN"), name="uniq_solicitud_pendiente_por_mesa"),
        ]

    def __str__(self) -> str:
        return f"Solicitud de la {self.mesa}"

    @property
    def es_solo_llamado(self) -> bool:
        return not self.items

    def detalle(self) -> list[dict]:
        """Ítems con sus objetos (los que ya no existen o salieron de la carta se marcan)."""
        from inventario.models import Producto, Promocion

        productos = Producto.objects.in_bulk([i["id"] for i in self.items if i.get("tipo") == "producto"])
        promos = Promocion.objects.in_bulk([i["id"] for i in self.items if i.get("tipo") == "promocion"])
        filas = []
        for i in self.items:
            obj = (productos if i.get("tipo") == "producto" else promos).get(i.get("id"))
            if obj is None:
                valido = False
            elif i.get("tipo") == "producto":
                valido = obj.disponible
            else:
                valido = Promocion.vigentes().filter(pk=obj.pk).exists() and obj.disponible
            filas.append({**i, "obj": obj, "valido": valido})
        return filas

    @transaction.atomic
    def cargar_en(self, pedido: "Pedido", usuario) -> tuple[int, list[str]]:
        """Pasa los ítems a la comanda como platos sin enviar. Devuelve (agregados, avisos)."""
        from config.dinero import precio_local_vigente
        from inventario.servicios import disponibilidad_menu

        solicitud = SolicitudCliente.objects.select_for_update().get(pk=self.pk)
        if solicitud.estado != self.Estado.PENDIENTE:
            raise ValidationError("Esta solicitud ya fue atendida.")
        if pedido.mesa_id != solicitud.mesa_id:
            raise ValidationError("La solicitud es de otra mesa.")
        pedido._exigir_abierto()
        agregados, avisos = 0, []
        porciones = disponibilidad_menu()
        for fila in solicitud.detalle():
            obj, cantidad = fila["obj"], int(fila.get("cantidad") or 1)
            if not fila["valido"]:
                avisos.append(f"{getattr(obj, 'nombre', 'Un plato')} ya no está disponible y no se cargó.")
                continue
            if fila["tipo"] == "promocion":
                pedido.agregar_promocion(obj, cantidad)
                agregados += cantidad
                continue
            libres = porciones.get(obj.pk)
            if libres is not None and libres < cantidad:
                if libres <= 0:
                    avisos.append(f"{obj.nombre} está agotado y no se cargó.")
                    continue
                avisos.append(f"De {obj.nombre} solo quedaban {libres}: se cargaron {libres}.")
                cantidad = libres
            DetallePedido.objects.create(
                pedido=pedido, producto=obj, cantidad=cantidad, precio_unitario=precio_local_vigente(obj.precio_usd),
                observaciones=(fila.get("nota") or "")[:250],
            )
            porciones[obj.pk] = (libres - cantidad) if libres is not None else None
            agregados += cantidad
        if solicitud.nota:
            pedido.notas = (f"{pedido.notas} · " if pedido.notas else "") + f"Cliente: {solicitud.nota}"
            pedido.notas = pedido.notas[:250]
            pedido.save(update_fields=["notas", "actualizado_en"])
        solicitud.estado = self.Estado.CARGADA
        solicitud.atendida_por = usuario
        solicitud.atendida_en = timezone.now()
        solicitud.pedido = pedido
        solicitud.save(update_fields=["estado", "atendida_por", "atendida_en", "pedido", "actualizada_en"])
        return agregados, avisos

    @transaction.atomic
    def descartar(self, usuario) -> None:
        solicitud = SolicitudCliente.objects.select_for_update().get(pk=self.pk)
        if solicitud.estado != self.Estado.PENDIENTE:
            raise ValidationError("Esta solicitud ya fue atendida.")
        solicitud.estado = self.Estado.DESCARTADA
        solicitud.atendida_por = usuario
        solicitud.atendida_en = timezone.now()
        solicitud.save(update_fields=["estado", "atendida_por", "atendida_en", "actualizada_en"])


def _almacen_privado():
    """Comprobantes de pago: fuera de /media/ (no tienen dirección pública); solo caja los abre."""
    import os

    from django.core.files.storage import FileSystemStorage

    class AlmacenPrivado(FileSystemStorage):
        @property
        def base_location(self):  # se lee en cada uso para respetar la configuración vigente
            return str(settings.PRIVADO_ROOT)

        @property
        def location(self):
            return os.path.abspath(self.base_location)

    return AlmacenPrivado()


class PedidoEnLinea(models.Model):
    """Pedido para retirar en el local, hecho desde el menú en línea (enlace general, sin QR de mesa).

    El cliente paga por pago móvil, Binance o Zinli y sube el comprobante. Caja verifica el pago y lo
    manda a cocina; cuando cocina lo marca listo, caja avisa al cliente para que lo retire.
    """

    class Estado(models.TextChoices):
        POR_VERIFICAR = "VER", "Pago por verificar"
        RECHAZADO = "REC", "Pago rechazado"
        ACEPTADO = "ACE", "Pago verificado"
        ENTREGADO = "ENT", "Entregado"

    token = models.CharField(max_length=32, unique=True, editable=False)
    estado = models.CharField(max_length=3, choices=Estado.choices, default=Estado.POR_VERIFICAR)
    cliente_nombre = models.CharField(max_length=80)
    cliente_telefono = models.CharField(max_length=20)
    cliente_correo = models.EmailField(blank=True)
    datos_factura = models.JSONField(default=dict, blank=True, help_text="Datos fiscales si pidió factura personalizada.")
    items = models.JSONField(default=list)
    nota = models.CharField(max_length=200, blank=True)
    metodo = models.CharField(max_length=3)
    tasa = models.DecimalField(max_digits=14, decimal_places=4)
    monto_local = models.DecimalField(max_digits=12, decimal_places=2)
    monto_usd = models.DecimalField(max_digits=12, decimal_places=2)
    referencia = models.CharField(max_length=60)
    comprobante = models.FileField(upload_to="comprobantes/", storage=_almacen_privado)
    creado_en = models.DateTimeField(auto_now_add=True)
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pedidos_en_linea_revisados"
    )
    revisado_en = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.CharField(max_length=200, blank=True)
    avisado_en = models.DateTimeField(null=True, blank=True)
    entregado_en = models.DateTimeField(null=True, blank=True)
    pedido = models.OneToOneField(Pedido, on_delete=models.SET_NULL, null=True, blank=True, related_name="en_linea_origen")

    class Meta:
        verbose_name = "pedido en línea"
        verbose_name_plural = "pedidos en línea"
        ordering = ["creado_en"]

    def __str__(self) -> str:
        return f"{self.codigo} · {self.cliente_nombre}"

    def save(self, *args, **kwargs):
        if not self.token:
            import secrets

            self.token = secrets.token_hex(16)
        super().save(*args, **kwargs)

    @property
    def codigo(self) -> str:
        return f"R-{self.pk:04d}" if self.pk else "R-nuevo"

    @property
    def en_usd(self) -> bool:
        from caja.models import Pago

        return self.metodo in Pago.METODOS_USD

    @property
    def monto(self):
        return self.monto_usd if self.en_usd else self.monto_local

    @property
    def metodo_nombre(self) -> str:
        from caja.models import Pago

        return Pago.Metodo(self.metodo).label if self.metodo in Pago.Metodo.values else self.metodo

    @property
    def etapa(self) -> str:
        """Para el cliente y la caja: verificar, rechazado, cocina, listo, entregado."""
        if self.estado == self.Estado.POR_VERIFICAR:
            return "verificar"
        if self.estado == self.Estado.RECHAZADO:
            return "rechazado"
        if self.estado == self.Estado.ENTREGADO:
            return "entregado"
        if self.pedido and self.pedido.estado in (Pedido.Estado.LISTO, Pedido.Estado.ENTREGADO):
            return "listo"
        return "cocina"

    def lineas(self) -> list[dict]:
        """Ítems con nombre para mostrarlos (los datos se guardaron al hacer el pedido)."""
        return [{"cantidad": i["cantidad"], "nombre": i.get("nombre", "?"), "nota": i.get("nota", "")} for i in self.items]

    @transaction.atomic
    def aceptar(self, cajero):
        """Caja verificó el pago: se crea la comanda, va a cocina y se factura y cobra con la tasa del pedido."""
        from caja.models import Factura, Pago
        from inventario.models import Producto, Promocion
        from inventario.servicios import disponibilidad_menu

        actual = PedidoEnLinea.objects.select_for_update().get(pk=self.pk)
        if actual.estado != self.Estado.POR_VERIFICAR:
            raise ValidationError("Este pedido ya fue revisado.")
        porciones = disponibilidad_menu()
        necesarias = {}
        for item in actual.items:
            if item["tipo"] == "producto":
                necesarias[item["id"]] = necesarias.get(item["id"], 0) + item["cantidad"]
            else:
                promo = Promocion.objects.prefetch_related("items").filter(pk=item["id"]).first()
                for pi in promo.items.all() if promo else []:
                    necesarias[pi.producto_id] = necesarias.get(pi.producto_id, 0) + pi.cantidad * item["cantidad"]
        faltan = [pk for pk, cant in necesarias.items() if porciones.get(pk) is not None and porciones[pk] < cant]
        if faltan:
            nombres = ", ".join(Producto.objects.filter(pk__in=faltan).values_list("nombre", flat=True))
            raise ValidationError(f"No alcanza el inventario para: {nombres}. Llama al cliente o rechaza el pedido.")

        pedido = Pedido.objects.create(
            tipo=Pedido.Tipo.LLEVAR, cliente=f"{actual.cliente_nombre} (en línea)"[:80], mesero=cajero, en_linea=True,
            notas=actual.nota[:250],
        )
        for item in actual.items:
            if item["tipo"] == "producto":
                DetallePedido.objects.create(
                    pedido=pedido, producto_id=item["id"], cantidad=item["cantidad"],
                    precio_unitario=Decimal(item["precio_local"]), observaciones=(item.get("nota") or "")[:250],
                )
            else:
                promo = Promocion.objects.get(pk=item["id"])
                pedido.agregar_promocion(promo, item["cantidad"], total_local=Decimal(item["precio_local"]))
        pedido.enviar_a_cocina()
        factura = Factura.emitir_desde_pedido(pedido, cajero, tasa=actual.tasa)
        if actual.datos_factura:
            factura.guardar_datos_cliente(actual.datos_factura)
        if actual.cliente_correo and not factura.cliente_correo:
            factura.cliente_correo = actual.cliente_correo
            factura.save(update_fields=["cliente_correo"])
        factura.cobrar(metodo=actual.metodo, monto=actual.monto, referencia=actual.referencia,
                       pagador=actual.cliente_nombre)
        factura.refresh_from_db()
        if factura.estado != Factura.Estado.PAGADA:
            raise ValidationError("El pago no cubre el total del pedido; revísalo con el cliente.")
        actual.estado, actual.revisado_por, actual.revisado_en, actual.pedido = (
            self.Estado.ACEPTADO, cajero, timezone.now(), pedido)
        actual.save(update_fields=["estado", "revisado_por", "revisado_en", "pedido"])
        self.refresh_from_db()
        return factura

    @transaction.atomic
    def rechazar(self, cajero, motivo: str) -> None:
        actual = PedidoEnLinea.objects.select_for_update().get(pk=self.pk)
        if actual.estado != self.Estado.POR_VERIFICAR:
            raise ValidationError("Este pedido ya fue revisado.")
        motivo = " ".join((motivo or "").split())[:200]
        if not motivo:
            raise ValidationError("Indica el motivo del rechazo; el cliente lo verá.")
        actual.estado, actual.motivo_rechazo = self.Estado.RECHAZADO, motivo
        actual.revisado_por, actual.revisado_en = cajero, timezone.now()
        actual.save(update_fields=["estado", "motivo_rechazo", "revisado_por", "revisado_en"])
        self.refresh_from_db()

    @transaction.atomic
    def entregar(self) -> None:
        """El cliente retiró su pedido: se cierra la comanda."""
        actual = PedidoEnLinea.objects.select_for_update().select_related("pedido").get(pk=self.pk)
        if actual.estado != self.Estado.ACEPTADO or actual.etapa != "listo":
            raise ValidationError("Solo se entregan pedidos que cocina marcó como listos.")
        pedido = actual.pedido
        pedido.detalles.filter(estado=DetallePedido.Estado.LISTO).update(estado=DetallePedido.Estado.ENTREGADO)
        pedido.estado, pedido.cerrado_en = Pedido.Estado.CERRADO, timezone.now()
        pedido.save(update_fields=["estado", "cerrado_en", "actualizado_en"])
        actual.estado, actual.entregado_en = self.Estado.ENTREGADO, timezone.now()
        actual.save(update_fields=["estado", "entregado_en"])
        self.refresh_from_db()

