from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from mesas.models import Mesa, Pedido

CENTAVO = Decimal("0.01")


def _redondear(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


class TasaCambio(models.Model):
    """Tasa vigente: 1 USD = `valor` unidades de moneda local."""

    valor = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )
    vigente_desde = models.DateTimeField(default=timezone.now)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tasas_cambio",
    )
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "tasa de cambio"
        verbose_name_plural = "tasas de cambio"
        ordering = ["-vigente_desde"]

    def __str__(self) -> str:
        return f"1 USD = {self.valor} moneda local"

    @classmethod
    def vigente(cls) -> "TasaCambio":
        tasa = cls.objects.filter(activa=True).order_by("-vigente_desde").first()
        if tasa is None:
            raise ValidationError("No hay una tasa de cambio activa configurada.")
        return tasa

    def a_usd(self, monto_local: Decimal) -> Decimal:
        return _redondear(monto_local / self.valor)

    def a_local(self, monto_usd: Decimal) -> Decimal:
        return _redondear(monto_usd * self.valor)


class Factura(models.Model):
    class Estado(models.TextChoices):
        ABIERTA = "ABI", "Abierta"
        PAGADA = "PAG", "Pagada"
        ANULADA = "ANU", "Anulada"

    pedido = models.ForeignKey(Pedido, on_delete=models.PROTECT, related_name="facturas")
    cajero = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="facturas_emitidas",
    )
    numero = models.CharField(max_length=20, unique=True)
    estado = models.CharField(max_length=3, choices=Estado.choices, default=Estado.ABIERTA)
    tasa_cambio = models.DecimalField(max_digits=14, decimal_places=4)
    subtotal_local = models.DecimalField(max_digits=12, decimal_places=2)
    total_local = models.DecimalField(max_digits=12, decimal_places=2)
    total_usd = models.DecimalField(max_digits=12, decimal_places=2)
    emitida_en = models.DateTimeField(auto_now_add=True)
    pagada_en = models.DateTimeField(null=True, blank=True)
    anulada_en = models.DateTimeField(null=True, blank=True)
    motivo_anulacion = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "factura"
        verbose_name_plural = "facturas"
        ordering = ["-emitida_en"]
        constraints = [
            models.UniqueConstraint(
                fields=["pedido"],
                condition=~Q(estado="ANU"),
                name="uniq_factura_vigente_por_pedido",
            ),
        ]

    def __str__(self) -> str:
        return f"Factura {self.numero}"

    @property
    def total_pagado_local(self) -> Decimal:
        return sum((p.monto_local for p in self.pagos.all()), Decimal("0.00"))

    @property
    def saldo_local(self) -> Decimal:
        return (self.total_local - self.total_pagado_local).quantize(CENTAVO)

    @property
    def saldo_usd(self) -> Decimal:
        return _redondear(self.saldo_local / self.tasa_cambio)

    @classmethod
    @transaction.atomic
    def emitir_desde_pedido(cls, pedido: Pedido, cajero) -> "Factura":
        pedido = Pedido.objects.select_for_update().select_related("mesa").get(pk=pedido.pk)
        if pedido.estado in {Pedido.Estado.ANULADO, Pedido.Estado.CERRADO}:
            raise ValidationError("No se puede facturar un pedido cerrado o anulado.")
        if pedido.factura_activa:
            raise ValidationError("Este pedido ya tiene una factura vigente.")
        vigentes = pedido.detalles_vigentes
        if not vigentes.exists():
            raise ValidationError("El pedido no tiene platillos; no hay nada que facturar.")
        if vigentes.filter(enviado=False).exists():
            raise ValidationError("Hay platillos sin enviar a cocina; el mesero debe enviarlos antes de facturar.")
        total_local = _redondear(pedido.subtotal)
        if total_local <= 0:
            raise ValidationError("El total del pedido es cero; no se puede facturar.")
        tasa = TasaCambio.vigente()
        factura = cls.objects.create(
            pedido=pedido,
            cajero=cajero,
            numero=cls._siguiente_numero(),
            tasa_cambio=tasa.valor,
            subtotal_local=total_local,
            total_local=total_local,
            total_usd=tasa.a_usd(total_local),
        )
        if pedido.mesa.estado != Mesa.Estado.POR_FACTURAR:
            pedido.mesa.marcar_por_facturar()
        return factura

    @classmethod
    def _siguiente_numero(cls) -> str:
        ultimo = cls.objects.order_by("-id").values_list("numero", flat=True).first()
        if not ultimo:
            return "F-000001"
        return f"F-{int(ultimo.split('-')[-1]) + 1:06d}"

    @transaction.atomic
    def cobrar(self, *, metodo: str, monto: Decimal, referencia: str = "", pagador: str = ""):
        """Registra un pago (total o parcial) y cierra la cuenta si queda saldada."""
        factura = Factura.objects.select_for_update().select_related("pedido__mesa").get(pk=self.pk)
        if factura.estado != self.Estado.ABIERTA:
            raise ValidationError("Esta factura ya no admite pagos.")
        pago = Pago(
            factura=factura,
            metodo=metodo,
            monto_recibido=monto,
            monto_local=Decimal("0.00"),
            referencia=referencia.strip(),
            pagador=pagador.strip(),
        )
        pago.clean()
        pago.save()
        if factura.saldo_local <= Decimal("0.00"):
            factura.registrar_pago_completo_y_cerrar()
        return pago, factura

    @transaction.atomic
    def registrar_pago_completo_y_cerrar(self) -> None:
        if self.saldo_local > Decimal("0.00"):
            raise ValidationError("La factura aún tiene saldo pendiente.")
        # RF-04: la venta queda procesada, así que se descuenta el inventario de todo lo consumido.
        self.pedido.descontar_inventario()
        ahora = timezone.now()
        self.estado = self.Estado.PAGADA
        self.pagada_en = ahora
        self.save(update_fields=["estado", "pagada_en"])
        self.pedido.estado = Pedido.Estado.CERRADO
        self.pedido.cerrado_en = ahora
        self.pedido.save(update_fields=["estado", "cerrado_en", "actualizado_en"])
        self.pedido.mesa.marcar_por_limpiar()

    @transaction.atomic
    def anular(self, motivo: str) -> None:
        """Anula una factura abierta sin pagos; la mesa vuelve a estar ocupada y editable."""
        factura = Factura.objects.select_for_update().select_related("pedido__mesa").get(pk=self.pk)
        if factura.estado != self.Estado.ABIERTA:
            raise ValidationError("Solo se pueden anular facturas abiertas.")
        if factura.pagos.exists():
            raise ValidationError("La factura ya tiene pagos registrados y no puede anularse.")
        if not motivo.strip():
            raise ValidationError("Indica el motivo de la anulación.")
        factura.estado = self.Estado.ANULADA
        factura.anulada_en = timezone.now()
        factura.motivo_anulacion = motivo.strip()[:200]
        factura.save(update_fields=["estado", "anulada_en", "motivo_anulacion"])
        if factura.pedido.mesa.estado == Mesa.Estado.POR_FACTURAR:
            factura.pedido.mesa.ocupar()
        self.estado = factura.estado

    @transaction.atomic
    def anular_con_devolucion(self, motivo: str, usuario) -> list:
        """Anula una factura que ya tiene pagos (parciales o totales) y devuelve cada pago.

        - Factura pagada: la venta queda anulada; el pedido sigue cerrado y la mesa no se toca.
          El inventario ya descontado no se repone (la comida se preparó y se sirvió).
        - Factura abierta con pagos parciales: se devuelven los pagos y el pedido vuelve a quedar
          editable y facturable.
        Devuelve la lista de `Devolucion` creadas.
        """
        factura = Factura.objects.select_for_update().select_related("pedido__mesa").get(pk=self.pk)
        if factura.estado == self.Estado.ANULADA:
            raise ValidationError("Esta factura ya está anulada.")
        if not motivo.strip():
            raise ValidationError("Indica el motivo de la devolución.")
        pagos = list(factura.pagos.select_related("factura"))
        if not pagos:
            factura.anular(motivo)
            self.estado = factura.estado
            return []

        estaba_pagada = factura.estado == self.Estado.PAGADA
        devoluciones = [Devolucion.desde_pago(pago, motivo, usuario) for pago in pagos]
        factura.estado = self.Estado.ANULADA
        factura.anulada_en = timezone.now()
        factura.motivo_anulacion = motivo.strip()[:200]
        factura.save(update_fields=["estado", "anulada_en", "motivo_anulacion"])
        if not estaba_pagada and factura.pedido.mesa.estado == Mesa.Estado.POR_FACTURAR:
            factura.pedido.mesa.ocupar()
        self.estado = factura.estado
        return devoluciones

    # ---- cuenta dividida por platillo -----------------------------------------
    @transaction.atomic
    def asignar(self, detalle_id: int, pagador: str, cantidad: int | None = None) -> "Asignacion":
        """Asigna `cantidad` unidades de un platillo a un comensal (por defecto, lo que quede sin asignar)."""
        factura = Factura.objects.select_for_update().select_related("pedido").get(pk=self.pk)
        if factura.estado != self.Estado.ABIERTA:
            raise ValidationError("Solo se puede dividir una factura abierta.")
        pagador = " ".join((pagador or "").split())[:80]
        if not pagador:
            raise ValidationError("Indica el nombre del comensal.")
        detalle = factura.pedido.detalles_vigentes.select_related("producto").filter(pk=detalle_id).first()
        if detalle is None:
            raise ValidationError("Ese platillo no pertenece a esta cuenta.")
        asignadas = sum(a.cantidad for a in factura.asignaciones.filter(detalle=detalle))
        libres = detalle.cantidad - asignadas
        if cantidad is None:
            cantidad = libres
        if libres <= 0:
            raise ValidationError(f"{detalle.producto.nombre} ya está asignado completo.")
        if cantidad < 1:
            raise ValidationError("La cantidad debe ser al menos 1.")
        if cantidad > libres:
            raise ValidationError(f"De {detalle.producto.nombre} solo quedan {libres} sin asignar.")
        existente = factura.asignaciones.filter(detalle=detalle, pagador__iexact=pagador).first()
        if existente:
            existente.cantidad += cantidad
            existente.save(update_fields=["cantidad"])
            return existente
        return Asignacion.objects.create(factura=factura, detalle=detalle, pagador=pagador, cantidad=cantidad)

    @transaction.atomic
    def quitar_asignacion(self, asignacion_id: int | None = None) -> int:
        """Quita una asignación (o todas si no se indica). Devuelve cuántas quitó."""
        factura = Factura.objects.select_for_update().get(pk=self.pk)
        if factura.estado != self.Estado.ABIERTA:
            raise ValidationError("Solo se puede dividir una factura abierta.")
        asignaciones = factura.asignaciones.all()
        if asignacion_id is not None:
            asignaciones = asignaciones.filter(pk=asignacion_id)
        cantidad = asignaciones.count()
        asignaciones.delete()
        return cantidad

    def resumen_por_pagador(self) -> dict:
        """Lo que debe y lleva pagado cada comensal, más lo que aún no se asignó a nadie."""
        personas: dict[str, dict] = {}
        asignado_por_detalle: dict[int, int] = {}
        for a in self.asignaciones.select_related("detalle__producto"):
            fila = personas.setdefault(
                a.pagador.casefold(),
                {"nombre": a.pagador, "items": [], "total_local": Decimal("0.00"), "pagado_local": Decimal("0.00")},
            )
            fila["items"].append(a)
            fila["total_local"] += a.subtotal
            asignado_por_detalle[a.detalle_id] = asignado_por_detalle.get(a.detalle_id, 0) + a.cantidad
        for pago in self.pagos.all():
            fila = personas.get(" ".join(pago.pagador.split()).casefold()) if pago.pagador else None
            if fila is not None:
                fila["pagado_local"] += pago.monto_local
        for fila in personas.values():
            fila["pendiente_local"] = max(fila["total_local"] - fila["pagado_local"], Decimal("0.00"))
            fila["total_usd"] = _redondear(fila["total_local"] / self.tasa_cambio)
            fila["pendiente_usd"] = _redondear(fila["pendiente_local"] / self.tasa_cambio)
        sin_asignar = Decimal("0.00")
        libres = []
        for d in self.pedido.detalles_vigentes.select_related("producto"):
            restante = d.cantidad - asignado_por_detalle.get(d.pk, 0)
            if restante > 0:
                sin_asignar += d.precio_unitario * restante
                libres.append({"detalle": d, "restante": restante})
        return {
            "personas": sorted(personas.values(), key=lambda f: f["nombre"].casefold()),
            "libres": libres,
            "sin_asignar_local": _redondear(sin_asignar),
            "hay_asignaciones": bool(personas),
        }


class Pago(models.Model):
    class Moneda(models.TextChoices):
        LOCAL = "LOC", "Moneda local (Bs.)"
        USD = "USD", "Dólar estadounidense"

    class Metodo(models.TextChoices):
        EFECTIVO = "EFE", "Efectivo (Bs.)"
        PAGO_MOVIL = "PMO", "Pago móvil (Bs.)"
        DIVISA = "DIV", "Divisa (USD en efectivo)"
        TARJETA = "TAR", "Tarjeta (Bs.)"

    METODOS_CON_REFERENCIA = {Metodo.PAGO_MOVIL, Metodo.TARJETA}

    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name="pagos")
    moneda = models.CharField(max_length=3, choices=Moneda.choices)
    metodo = models.CharField(max_length=3, choices=Metodo.choices, default=Metodo.EFECTIVO)
    referencia = models.CharField(
        max_length=60,
        blank=True,
        help_text="Referencia del pago móvil o voucher de la tarjeta.",
    )
    pagador = models.CharField(max_length=80, blank=True, help_text="Comensal que paga (cuenta dividida).")
    monto_recibido = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="En Bs. o en USD según el método.",
    )
    monto_local = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Equivalente en moneda local aplicado a la factura.",
    )
    vuelto_local = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    registrado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "pago"
        verbose_name_plural = "pagos"
        ordering = ["registrado_en"]

    def __str__(self) -> str:
        return f"{self.get_metodo_display()} {self.monto_recibido} → Factura {self.factura.numero}"

    def _calcular(self):
        """(moneda, equivalente_local, aplicado_local, vuelto_local) sin modificar el objeto."""
        tasa = Decimal(self.factura.tasa_cambio)
        saldo = self.factura.saldo_local
        moneda = self.Moneda.USD if self.metodo == self.Metodo.DIVISA else self.Moneda.LOCAL
        if moneda == self.Moneda.USD:
            equivalente = _redondear(self.monto_recibido * tasa)
            # Pagar exactamente el total en USD salda la cuenta aunque el redondeo difiera un centavo.
            cubre = saldo > 0 and self.monto_recibido >= _redondear(saldo / tasa)
        else:
            equivalente = _redondear(self.monto_recibido)
            cubre = equivalente >= saldo
        aplicado = saldo if cubre else min(equivalente, saldo)
        vuelto = max(equivalente - saldo, Decimal("0.00"))
        return moneda, equivalente, aplicado, vuelto

    def clean(self):
        if self.pk:
            return
        if self.metodo not in self.Metodo.values:
            raise ValidationError("Método de pago no válido.")
        if self.monto_recibido is None or self.monto_recibido <= 0:
            raise ValidationError("Ingresa un monto mayor a cero.")
        if self.factura.saldo_local <= 0:
            raise ValidationError("La factura no tiene saldo pendiente.")
        if self.metodo in self.METODOS_CON_REFERENCIA:
            if not self.referencia.strip():
                raise ValidationError("Indica la referencia del pago móvil o el voucher de la tarjeta.")
            if self._calcular()[3] > 0:
                raise ValidationError("El monto excede el saldo; con este método no se entrega vuelto.")

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.factura.saldo_local <= 0:
                raise ValidationError("La factura no tiene saldo pendiente.")
            self.moneda, _, self.monto_local, self.vuelto_local = self._calcular()
        super().save(*args, **kwargs)


class Devolucion(models.Model):
    """Reverso de un pago cuando se anula una factura ya cobrada (total o parcialmente)."""

    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name="devoluciones")
    pago = models.OneToOneField(Pago, on_delete=models.PROTECT, related_name="devolucion")
    metodo = models.CharField(max_length=3, choices=Pago.Metodo.choices)
    moneda = models.CharField(max_length=3, choices=Pago.Moneda.choices)
    monto = models.DecimalField(max_digits=12, decimal_places=2, help_text="Lo que se entrega al cliente, en su moneda.")
    monto_local = models.DecimalField(max_digits=12, decimal_places=2, help_text="Equivalente aplicado en moneda local.")
    referencia = models.CharField(max_length=60, blank=True)
    motivo = models.CharField(max_length=200)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="devoluciones_registradas"
    )
    registrado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "devolución"
        verbose_name_plural = "devoluciones"
        ordering = ["registrado_en"]

    def __str__(self) -> str:
        return f"Devolución {self.get_metodo_display()} {self.monto} · {self.factura.numero}"

    @classmethod
    def desde_pago(cls, pago: Pago, motivo: str, usuario) -> "Devolucion":
        """Se devuelve lo que realmente se aplicó a la cuenta (el vuelto ya se había entregado)."""
        if pago.moneda == Pago.Moneda.USD:
            monto = min(pago.monto_recibido, _redondear(pago.monto_local / pago.factura.tasa_cambio))
        else:
            monto = pago.monto_local
        return cls.objects.create(
            factura=pago.factura,
            pago=pago,
            metodo=pago.metodo,
            moneda=pago.moneda,
            monto=monto,
            monto_local=pago.monto_local,
            referencia=pago.referencia,
            motivo=motivo.strip()[:200],
            registrado_por=usuario,
        )


class Asignacion(models.Model):
    """Platillos (o parte de ellos) que paga cada comensal en una cuenta dividida."""

    factura = models.ForeignKey(Factura, on_delete=models.CASCADE, related_name="asignaciones")
    detalle = models.ForeignKey("mesas.DetallePedido", on_delete=models.PROTECT, related_name="asignaciones")
    pagador = models.CharField(max_length=80)
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "asignación de cuenta"
        verbose_name_plural = "asignaciones de cuenta"
        ordering = ["pagador", "detalle_id"]

    def __str__(self) -> str:
        return f"{self.pagador}: {self.cantidad} × {self.detalle.producto.nombre}"

    @property
    def subtotal(self) -> Decimal:
        return (self.detalle.precio_unitario * self.cantidad).quantize(CENTAVO)


class CierreCaja(models.Model):
    """Cierre de un cajero: cubre todos los cobros desde su cierre anterior."""

    fecha = models.DateField()
    cajero = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cierres_caja",
    )
    tasa_cambio = models.DecimalField(max_digits=14, decimal_places=4)
    total_local = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_usd = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    cantidad_facturas = models.PositiveIntegerField(default=0)
    facturas_anuladas = models.PositiveIntegerField(default=0)
    desde = models.DateTimeField(null=True, blank=True)
    hasta = models.DateTimeField(default=timezone.now)
    desglose = models.JSONField(default=dict, blank=True)
    cerrado_en = models.DateTimeField(auto_now_add=True)
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "cierre de caja"
        verbose_name_plural = "cierres de caja"
        ordering = ["-hasta"]

    def __str__(self) -> str:
        return f"Cierre {self.fecha} · {self.cajero}"

    @classmethod
    def ultimo_de(cls, cajero):
        return cls.objects.filter(cajero=cajero).order_by("-hasta").first()

    @classmethod
    def calcular(cls, cajero=None, desde=None, hasta=None) -> dict:
        """Totales de cobros y ventas en (desde, hasta]. `cajero=None` consolida a todos."""
        hasta = hasta or timezone.now()
        pagos = Pago.objects.select_related("factura").filter(registrado_en__lte=hasta)
        devoluciones = Devolucion.objects.select_related("factura", "pago").filter(registrado_en__lte=hasta)
        # Una factura cobrada y luego devuelta se sigue contando en el período en que se cobró.
        facturas = Factura.objects.select_related("pedido__mesa", "cajero").filter(
            estado__in=[Factura.Estado.PAGADA, Factura.Estado.ANULADA], pagada_en__isnull=False, pagada_en__lte=hasta
        )
        anuladas = Factura.objects.filter(estado=Factura.Estado.ANULADA, anulada_en__lte=hasta)
        if desde:
            pagos = pagos.filter(registrado_en__gt=desde)
            devoluciones = devoluciones.filter(registrado_en__gt=desde)
            facturas = facturas.filter(pagada_en__gt=desde)
            anuladas = anuladas.filter(anulada_en__gt=desde)
        if cajero is not None:
            # Una devolución sale de la caja de quien cobró la factura original.
            pagos = pagos.filter(factura__cajero=cajero)
            devoluciones = devoluciones.filter(factura__cajero=cajero)
            facturas = facturas.filter(cajero=cajero)
            anuladas = anuladas.filter(cajero=cajero)

        acumulado: dict[tuple[str, str], dict] = {}

        def linea_de(metodo, moneda):
            return acumulado.setdefault(
                (metodo, moneda),
                {
                    "metodo": metodo,
                    "metodo_label": Pago.Metodo(metodo).label,
                    "moneda": moneda,
                    "cantidad": 0,
                    "recibido": Decimal("0.00"),
                    "aplicado_local": Decimal("0.00"),
                    "vuelto_local": Decimal("0.00"),
                    "devuelto": Decimal("0.00"),
                    "devuelto_local": Decimal("0.00"),
                },
            )

        total_local = Decimal("0.00")
        total_usd = Decimal("0.00")
        vuelto_total = Decimal("0.00")
        efectivo_bs = Decimal("0.00")
        divisas_usd = Decimal("0.00")
        devuelto_total_local = Decimal("0.00")
        cantidad_pagos = 0
        for pago in pagos:
            cantidad_pagos += 1
            linea = linea_de(pago.metodo, pago.moneda)
            linea["cantidad"] += 1
            linea["recibido"] += pago.monto_recibido
            linea["aplicado_local"] += pago.monto_local
            linea["vuelto_local"] += pago.vuelto_local
            total_local += pago.monto_local
            total_usd += _redondear(pago.monto_local / pago.factura.tasa_cambio)
            vuelto_total += pago.vuelto_local
            if pago.metodo == Pago.Metodo.EFECTIVO:
                efectivo_bs += pago.monto_recibido
            elif pago.metodo == Pago.Metodo.DIVISA:
                divisas_usd += pago.monto_recibido

        cantidad_devoluciones = 0
        for dev in devoluciones:
            cantidad_devoluciones += 1
            linea = linea_de(dev.metodo, dev.moneda)
            linea["devuelto"] += dev.monto
            linea["devuelto_local"] += dev.monto_local
            total_local -= dev.monto_local
            total_usd -= _redondear(dev.monto_local / dev.factura.tasa_cambio)
            devuelto_total_local += dev.monto_local
            if dev.metodo == Pago.Metodo.EFECTIVO:
                efectivo_bs -= dev.monto
            elif dev.metodo == Pago.Metodo.DIVISA:
                divisas_usd -= dev.monto

        lineas = sorted(acumulado.values(), key=lambda x: x["metodo_label"])
        facturas = list(facturas.order_by("pagada_en"))
        return {
            "desde": desde,
            "hasta": hasta,
            "lineas": lineas,
            "facturas": facturas,
            "cantidad_facturas": len(facturas),
            "facturas_anuladas": anuladas.count(),
            "cantidad_pagos": cantidad_pagos,
            "cantidad_devoluciones": cantidad_devoluciones,
            "devuelto_local": devuelto_total_local,
            "total_local": total_local,
            "total_usd": total_usd,
            "vuelto_local": vuelto_total,
            "efectivo_bs_neto": efectivo_bs - vuelto_total,
            "divisas_usd": divisas_usd,
        }

    @classmethod
    @transaction.atomic
    def generar(cls, cajero) -> "CierreCaja":
        hasta = timezone.now()
        ultimo = cls.ultimo_de(cajero)
        datos = cls.calcular(cajero, ultimo.hasta if ultimo else None, hasta)
        if not (
            datos["cantidad_pagos"]
            or datos["cantidad_facturas"]
            or datos["facturas_anuladas"]
            or datos["cantidad_devoluciones"]
        ):
            raise ValidationError("No hay movimientos nuevos desde el último cierre.")
        tasa = TasaCambio.vigente()
        return cls.objects.create(
            fecha=timezone.localdate(),
            cajero=cajero,
            tasa_cambio=tasa.valor,
            total_local=datos["total_local"],
            total_usd=datos["total_usd"],
            cantidad_facturas=datos["cantidad_facturas"],
            facturas_anuladas=datos["facturas_anuladas"],
            desde=datos["desde"],
            hasta=hasta,
            desglose={
                "lineas": [
                    {k: (str(v) if isinstance(v, Decimal) else v) for k, v in linea.items()}
                    for linea in datos["lineas"]
                ],
                "vuelto_local": str(datos["vuelto_local"]),
                "efectivo_bs_neto": str(datos["efectivo_bs_neto"]),
                "divisas_usd": str(datos["divisas_usd"]),
                "cantidad_pagos": datos["cantidad_pagos"],
                "cantidad_devoluciones": datos["cantidad_devoluciones"],
                "devuelto_local": str(datos["devuelto_local"]),
            },
        )
