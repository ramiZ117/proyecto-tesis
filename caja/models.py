from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

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
    class Fuente(models.TextChoices):
        MANUAL = "MAN", "Manual"
        BCV = "BCV", "BCV (automática)"

    vigente_desde = models.DateTimeField(default=timezone.now)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tasas_cambio",
        null=True,
        blank=True,
        help_text="Vacío cuando la tasa la publicó el sistema desde el BCV.",
    )
    activa = models.BooleanField(default=True)
    fuente = models.CharField(max_length=3, choices=Fuente.choices, default=Fuente.MANUAL)
    fecha_valor = models.DateField(null=True, blank=True, help_text="Fecha valor publicada por el BCV.")

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


class ClienteFiscal(models.Model):
    """Cliente que pidió factura con sus datos: se guarda para no volver a escribirlos en su próxima visita."""

    documento = models.CharField(max_length=20, unique=True, help_text="Cédula o RIF (V-12345678, J-123456789…).")
    nombre = models.CharField(max_length=120, help_text="Nombre o razón social.")
    direccion = models.CharField(max_length=200, help_text="Dirección fiscal.")
    telefono = models.CharField(max_length=20, blank=True)
    correo = models.EmailField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["nombre"]

    def __str__(self) -> str:
        return f"{self.nombre} · {self.documento}"

    CAMPOS = ("nombre", "documento", "direccion", "telefono", "correo")

    @classmethod
    def recordar(cls, datos: dict) -> "ClienteFiscal":
        """Guarda o actualiza el cliente con los datos ya validados (`datos_cliente_validos`)."""
        cliente, _ = cls.objects.update_or_create(
            documento=datos["documento"],
            defaults={c: datos.get(c, "") for c in cls.CAMPOS if c != "documento"},
        )
        return cliente

    def como_datos(self) -> dict:
        return {c: getattr(self, c) for c in self.CAMPOS}


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
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    base_imponible_local = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    iva_local = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    # Factura personalizada: vacíos = consumidor final (cliente genérico).
    cliente_nombre = models.CharField(max_length=120, blank=True)
    cliente_documento = models.CharField(max_length=20, blank=True, help_text="Cédula o RIF.")
    cliente_direccion = models.CharField(max_length=200, blank=True)
    cliente_telefono = models.CharField(max_length=20, blank=True)
    cliente_correo = models.EmailField(blank=True, help_text="Adonde se envía la factura.")
    correo_enviado_en = models.DateTimeField(null=True, blank=True)
    correo_error = models.CharField(max_length=200, blank=True)
    descuento_local = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    descuento_motivo = models.CharField(max_length=200, blank=True)
    descuento_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="descuentos_aplicados"
    )

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

    @property
    def personalizada(self) -> bool:
        return bool(self.cliente_documento)

    @property
    def igtf_local(self) -> Decimal:
        return sum((p.igtf_local for p in self.pagos.all()), Decimal("0.00"))

    @property
    def total_con_igtf_local(self) -> Decimal:
        return self.total_local + self.igtf_local

    def calcular_impuestos(self) -> None:
        """Los precios incluyen IVA: separa base imponible e impuesto del total (después del descuento)."""
        porcentaje = Decimal(settings.IVA_PORCENTAJE)
        self.iva_porcentaje = porcentaje
        self.base_imponible_local = _redondear(self.total_local / (1 + porcentaje / 100))
        self.iva_local = self.total_local - self.base_imponible_local

    def guardar_datos_cliente(self, datos: dict | None) -> None:
        """Datos de factura personalizada ya validados (ver `datos_cliente_validos`); None = consumidor final."""
        datos = datos or {}
        for campo in ("nombre", "documento", "direccion", "telefono", "correo"):
            setattr(self, f"cliente_{campo}", datos.get(campo, ""))
        self.save(update_fields=["cliente_nombre", "cliente_documento", "cliente_direccion", "cliente_telefono",
                                 "cliente_correo"])
        if datos.get("documento"):
            ClienteFiscal.recordar(datos)  # queda guardado para la próxima visita

    @classmethod
    @transaction.atomic
    def emitir_desde_pedido(cls, pedido: Pedido, cajero, tasa=None) -> "Factura":
        """Emite la factura. `tasa` (valor) permite respetar la de un pedido en línea ya pagado."""
        pedido = Pedido.objects.select_for_update().select_related("mesa").get(pk=pedido.pk)
        if pedido.estado in {Pedido.Estado.ANULADO, Pedido.Estado.CERRADO}:
            raise ValidationError("No se puede facturar un pedido cerrado o anulado.")
        if pedido.factura_activa:
            raise ValidationError("Este pedido ya tiene una factura vigente.")
        vigentes = pedido.detalles_vigentes
        if not vigentes.exists():
            raise ValidationError("El pedido no tiene platillos; no hay nada que facturar.")
        if vigentes.filter(enviado=False).exists() and not pedido.se_cobra_antes:
            raise ValidationError("Hay platillos sin enviar a cocina; el mesero debe enviarlos antes de facturar.")
        total_local = _redondear(pedido.subtotal)
        if total_local <= 0:
            raise ValidationError("El total del pedido es cero; no se puede facturar.")
        tasa = TasaCambio(valor=Decimal(tasa)) if tasa is not None else TasaCambio.vigente()
        factura = None
        ultimo_error = None
        numero = cls._siguiente_numero()
        for _ in range(cls.INTENTOS_NUMERO):
            try:
                # Punto de guardado: si el número choca, solo se deshace este intento.
                with transaction.atomic():
                    factura = cls.objects.create(
                        pedido=pedido,
                        cajero=cajero,
                        numero=numero,
                        tasa_cambio=tasa.valor,
                        subtotal_local=total_local,
                        total_local=total_local,
                        total_usd=tasa.a_usd(total_local),
                    )
                break
            except IntegrityError as exc:
                if pedido.factura_activa:
                    raise ValidationError("Este pedido ya tiene una factura vigente.")
                ultimo_error = exc
                # Otro cajero tomó ese correlativo (o ya existía): se avanza al siguiente libre.
                numero = cls._siguiente_numero(despues_de=numero)
        if factura is None:
            import logging

            logging.getLogger("el_amir").error("No se pudo emitir la factura del pedido %s: %s", pedido.pk, ultimo_error)
            raise ValidationError(
                f"No se pudo asignar el número de factura. Intenta de nuevo; si sigue, actualiza el sistema "
                f"(git pull y python manage.py migrate). Detalle: {ultimo_error}"
            )
        factura.calcular_impuestos()
        factura.save(update_fields=["iva_porcentaje", "base_imponible_local", "iva_local"])
        cuenta = SolicitudCuenta.activa_de(pedido)
        if cuenta is not None and cuenta.datos_cliente:
            factura.guardar_datos_cliente(cuenta.datos_cliente)
        if pedido.mesa and pedido.mesa.estado != Mesa.Estado.POR_FACTURAR:
            pedido.mesa.marcar_por_facturar()
        return factura

    INTENTOS_NUMERO = 5

    @classmethod
    def _siguiente_numero(cls, despues_de: str | None = None) -> str:
        """Correlativo F-000001, F-000002…: uno más que el MAYOR número emitido (las anuladas también cuentan).

        Se toma el mayor y no el de la última fila, para que datos importados o corregidos a mano no
        traben la numeración. Números con otro formato se ignoran.
        """
        import re

        mayor = 0
        for numero in cls.objects.filter(numero__startswith="F-").values_list("numero", flat=True):
            hallado = re.fullmatch(r"F-(\d+)", numero)
            if hallado:
                mayor = max(mayor, int(hallado.group(1)))
        if despues_de:
            hallado = re.fullmatch(r"F-(\d+)", despues_de)
            if hallado:
                mayor = max(mayor, int(hallado.group(1)))
        return f"F-{mayor + 1:06d}"

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
        if pago.metodo == Pago.Metodo.CASHEA:
            FinanciamientoCashea.desde_pago(pago)  # queda por cobrar hasta que Cashea pague el miércoles
        if factura.saldo_local <= Decimal("0.00"):
            factura.registrar_pago_completo_y_cerrar()
        return pago, factura

    MAX_PAGOS_MIXTOS = 5

    @transaction.atomic
    def cobrar_mixto(self, pagos: list[dict], pagador: str = ""):
        """Pago mixto: varios métodos en una sola operación (todo o nada).

        `pagos` = [{"metodo", "monto", "referencia"}, …]. Se aplican primero los métodos sin vuelto
        (pago móvil, tarjeta) y al final el efectivo y la divisa, de modo que el vuelto, si lo hay,
        salga del último pago en efectivo. Si un pago sobra o es inválido, no se registra ninguno.
        """
        if not pagos:
            raise ValidationError("Indica al menos un pago.")
        if len(pagos) > self.MAX_PAGOS_MIXTOS:
            raise ValidationError(f"Un pago mixto admite hasta {self.MAX_PAGOS_MIXTOS} métodos.")
        con_vuelto = {Pago.Metodo.EFECTIVO, Pago.Metodo.DIVISA}
        orden = sorted(enumerate(pagos), key=lambda par: (par[1]["metodo"] in con_vuelto, par[0]))
        registrados, factura = [], self
        for posicion, (_, p) in enumerate(orden, start=1):
            actual = Factura.objects.get(pk=self.pk)
            if actual.estado != self.Estado.ABIERTA:
                raise ValidationError(
                    f"Sobra un pago: con los primeros {posicion - 1} ya se cubrió el total. Revisa los montos."
                )
            pago, factura = factura.cobrar(metodo=p["metodo"], monto=p["monto"], referencia=p.get("referencia", ""),
                                           pagador=pagador)
            registrados.append(pago)
        return registrados, factura

    # ---- descuentos --------------------------------------------------------------------------
    @transaction.atomic
    def aplicar_descuento(self, *, tipo: str, valor: Decimal, motivo: str, usuario) -> Decimal:
        """Descuento sobre la cuenta (porcentaje, dólares o bolívares). Solo antes de registrar pagos."""
        factura = Factura.objects.select_for_update().get(pk=self.pk)
        if factura.estado != self.Estado.ABIERTA:
            raise ValidationError("Solo se aplican descuentos a facturas abiertas.")
        if factura.pagos.exists():
            raise ValidationError("La factura ya tiene pagos: el descuento se aplica antes de cobrar.")
        motivo = " ".join((motivo or "").split())[:200]
        if not motivo:
            raise ValidationError("Indica el motivo del descuento (cortesía, cliente frecuente, error en el pedido…).")
        if valor is None or valor <= 0:
            raise ValidationError("El descuento debe ser mayor a cero.")
        if tipo == "porcentaje":
            if valor > 100:
                raise ValidationError("El porcentaje no puede pasar de 100.")
            descuento = _redondear(factura.subtotal_local * valor / 100)
        elif tipo == "usd":
            descuento = _redondear(valor * factura.tasa_cambio)
        elif tipo == "bs":
            descuento = _redondear(valor)
        else:
            raise ValidationError("Tipo de descuento no válido.")
        if descuento > factura.subtotal_local:
            raise ValidationError("El descuento no puede superar el total de la cuenta.")
        factura.descuento_local = descuento
        factura.descuento_motivo = motivo
        factura.descuento_por = usuario
        factura.total_local = factura.subtotal_local - descuento
        factura.total_usd = _redondear(factura.total_local / factura.tasa_cambio)
        factura.calcular_impuestos()
        factura.save(update_fields=["descuento_local", "descuento_motivo", "descuento_por", "total_local", "total_usd",
                                    "iva_porcentaje", "base_imponible_local", "iva_local"])
        if factura.total_local <= 0:
            factura.registrar_pago_completo_y_cerrar()  # cortesía total: la cuenta queda saldada
        return descuento

    @transaction.atomic
    def quitar_descuento(self) -> None:
        factura = Factura.objects.select_for_update().get(pk=self.pk)
        if factura.estado != self.Estado.ABIERTA or factura.pagos.exists():
            raise ValidationError("Solo se quita el descuento de una factura abierta y sin pagos.")
        factura.descuento_local = Decimal("0.00")
        factura.descuento_motivo = ""
        factura.descuento_por = None
        factura.total_local = factura.subtotal_local
        factura.total_usd = _redondear(factura.total_local / factura.tasa_cambio)
        factura.calcular_impuestos()
        factura.save(update_fields=["descuento_local", "descuento_motivo", "descuento_por", "total_local", "total_usd",
                                    "iva_porcentaje", "base_imponible_local", "iva_local"])

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
        if self.pedido.en_linea:
            return  # se pagó por adelantado: la comanda se cierra cuando el cliente la retira
        if self.pedido.se_cobra_antes and self.pedido.sin_enviar.exists():
            self.pedido._enviar_nuevos()  # para llevar: pagado, ahora sí va a cocina
        if self.pedido.es_para_llevar and self.pedido.detalles_vigentes.exclude(estado="ENT").exists():
            return  # cobrado en el mostrador: sigue en cocina y se cierra al entregarlo
        self.pedido.estado = Pedido.Estado.CERRADO
        self.pedido.cerrado_en = ahora
        self.pedido.save(update_fields=["estado", "cerrado_en", "actualizado_en"])
        if self.pedido.mesa:
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
        if factura.pedido.mesa and factura.pedido.mesa.estado == Mesa.Estado.POR_FACTURAR:
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
        # Lo financiado por Cashea no se le devuelve al cliente en caja: la orden se anula ante Cashea.
        for financiamiento in FinanciamientoCashea.objects.filter(factura=factura).exclude(
                estado=FinanciamientoCashea.Estado.ANULADO):
            financiamiento.anular()
        factura.estado = self.Estado.ANULADA
        factura.anulada_en = timezone.now()
        factura.motivo_anulacion = motivo.strip()[:200]
        factura.save(update_fields=["estado", "anulada_en", "motivo_anulacion"])
        if not estaba_pagada and factura.pedido.mesa and factura.pedido.mesa.estado == Mesa.Estado.POR_FACTURAR:
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
        BINANCE = "BIN", "Binance Pay (USD)"
        ZINLI = "ZIN", "Zinli (USD)"
        CASHEA = "CAS", "Financiado (USD)"

    METODOS_USD = {Metodo.DIVISA, Metodo.BINANCE, Metodo.ZINLI, Metodo.CASHEA}
    METODOS_CON_REFERENCIA = {Metodo.PAGO_MOVIL, Metodo.TARJETA, Metodo.BINANCE, Metodo.ZINLI, Metodo.CASHEA}
    METODOS_DIGITALES = (Metodo.PAGO_MOVIL, Metodo.BINANCE, Metodo.ZINLI)  # el cliente paga desde su teléfono

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
    igtf_local = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        help_text="IGTF cobrado sobre este pago en divisas (no reduce la deuda: es impuesto).",
    )
    registrado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "pago"
        verbose_name_plural = "pagos"
        ordering = ["registrado_en"]

    def __str__(self) -> str:
        return f"{self.get_metodo_display()} {self.monto_recibido} → Factura {self.factura.numero}"

    @property
    def lleva_igtf(self) -> bool:
        return self.metodo in settings.IGTF_METODOS

    def _calcular_con_igtf(self):
        """Divisa en efectivo: el cliente paga la deuda más el IGTF sobre la parte que cubre."""
        tasa = Decimal(self.factura.tasa_cambio)
        saldo = self.factura.saldo_local
        factor = 1 + Decimal(settings.IGTF_PORCENTAJE) / 100
        equivalente = _redondear(self.monto_recibido * tasa)
        necesario = _redondear(saldo * factor)  # deuda + IGTF
        if saldo > 0 and (equivalente >= necesario or self.monto_recibido >= _redondear(necesario / tasa)):
            aplicado, igtf = saldo, necesario - saldo
            vuelto = max(equivalente - necesario, Decimal("0.00"))
        else:
            aplicado = min(_redondear(equivalente / factor), saldo)
            igtf, vuelto = equivalente - aplicado, Decimal("0.00")
        return self.Moneda.USD, equivalente, aplicado, vuelto, igtf

    def _calcular(self):
        """(moneda, equivalente_local, aplicado_local, vuelto_local) sin modificar el objeto."""
        if self.lleva_igtf:
            return self._calcular_con_igtf()[:4]
        tasa = Decimal(self.factura.tasa_cambio)
        saldo = self.factura.saldo_local
        moneda = self.Moneda.USD if self.metodo in self.METODOS_USD else self.Moneda.LOCAL
        if moneda == self.Moneda.USD:
            equivalente = _redondear(self.monto_recibido * tasa)
            # Pagar exactamente el total en USD salda la cuenta aunque el redondeo difiera un centavo.
            cubre = saldo > 0 and self.monto_recibido >= _redondear(saldo / tasa)
        else:
            equivalente = _redondear(self.monto_recibido)
            cubre = equivalente >= saldo
        aplicado = saldo if cubre else min(equivalente, saldo)
        vuelto = max(equivalente - saldo, Decimal("0.00"))
        if self.metodo in self.METODOS_CON_REFERENCIA and moneda == self.Moneda.USD and cubre \
                and self.monto_recibido <= _redondear(saldo / tasa):
            vuelto = Decimal("0.00")  # Binance/Zinli por el total exacto: la diferencia es solo redondeo
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
        # La referencia o el voucher se guardan si se escriben, pero no son obligatorios… salvo en Cashea:
        # el número de orden es lo que permite cuadrar lo que Cashea paga cada miércoles.
        if self.metodo == self.Metodo.CASHEA and len((self.referencia or "").strip()) < 3:
            raise ValidationError("Escribe el número de orden del financiamiento.")
        if self.metodo in self.METODOS_CON_REFERENCIA:
            if self._calcular()[3] > 0:
                raise ValidationError("El monto excede el saldo; con este método no se entrega vuelto.")

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.factura.saldo_local <= 0:
                raise ValidationError("La factura no tiene saldo pendiente.")
            if self.lleva_igtf:
                self.moneda, _, self.monto_local, self.vuelto_local, self.igtf_local = self._calcular_con_igtf()
            else:
                self.moneda, _, self.monto_local, self.vuelto_local = self._calcular()
        super().save(*args, **kwargs)


class Propina(models.Model):
    """Propina que deja una mesa: queda como saldo a favor del mesero que la atendió hasta que se le paga."""

    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name="propinas")
    mesero = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="propinas")
    metodo = models.CharField(max_length=3, choices=Pago.Metodo.choices)
    moneda = models.CharField(max_length=3, choices=Pago.Moneda.choices)
    monto = models.DecimalField(max_digits=12, decimal_places=2, help_text="En la moneda en que se recibió.")
    monto_local = models.DecimalField(max_digits=12, decimal_places=2)
    monto_usd = models.DecimalField(max_digits=12, decimal_places=2)
    registrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="propinas_registradas")
    registrada_en = models.DateTimeField(auto_now_add=True)
    pagada = models.BooleanField(default=False)
    pagada_en = models.DateTimeField(null=True, blank=True)
    pagada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="propinas_entregadas"
    )

    class Meta:
        verbose_name = "propina"
        verbose_name_plural = "propinas"
        ordering = ["-registrada_en"]
        indexes = [models.Index(fields=["mesero", "pagada"])]

    def __str__(self) -> str:
        return f"Propina {self.monto} {self.get_moneda_display()} · {self.mesero}"

    @classmethod
    @transaction.atomic
    def registrar(cls, factura: "Factura", *, metodo: str, monto: Decimal, usuario) -> "Propina":
        if metodo == Pago.Metodo.CASHEA:
            raise ValidationError("La propina no se puede financiar.")
        if metodo not in Pago.Metodo.values:
            raise ValidationError("Método de la propina no válido.")
        if monto is None or not monto.is_finite() or monto <= 0 or monto >= Decimal("1000000"):
            raise ValidationError("La propina debe ser un monto mayor a cero.")
        if factura.estado == Factura.Estado.ANULADA:
            raise ValidationError("La factura está anulada: no admite propinas.")
        en_usd = metodo in Pago.METODOS_USD
        tasa = Decimal(factura.tasa_cambio)
        return cls.objects.create(
            factura=factura,
            mesero_id=factura.pedido.mesero_id,
            metodo=metodo,
            moneda=Pago.Moneda.USD if en_usd else Pago.Moneda.LOCAL,
            monto=_redondear(monto),
            monto_local=_redondear(monto * tasa) if en_usd else _redondear(monto),
            monto_usd=_redondear(monto) if en_usd else _redondear(monto / tasa),
            registrada_por=usuario,
        )

    @classmethod
    @transaction.atomic
    def marcar_pagadas(cls, propinas, usuario) -> int:
        """Marca como entregadas al mesero las propinas pendientes indicadas. Devuelve cuántas."""
        return cls.objects.filter(pk__in=[p.pk for p in propinas], pagada=False).update(
            pagada=True, pagada_en=timezone.now(), pagada_por=usuario
        )


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
            monto = min(pago.monto_recibido, _redondear((pago.monto_local + pago.igtf_local) / pago.factura.tasa_cambio))
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
    secuencia = models.PositiveIntegerField(
        default=1,
        help_text="Consecutivo del cajero (1, 2, 3…). Cada cierre toma solo lo nuevo desde el anterior.",
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
    correo_enviado_a = models.CharField(max_length=300, blank=True)
    correo_enviado_en = models.DateTimeField(null=True, blank=True)
    correo_error = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "cierre de caja"
        verbose_name_plural = "cierres de caja"
        ordering = ["-hasta"]
        constraints = [
            # Dos cierres simultáneos del mismo cajero calcularían el mismo tramo: solo uno se guarda.
            models.UniqueConstraint(fields=["cajero", "secuencia"], name="uniq_cierre_secuencia_por_cajero"),
        ]

    def __str__(self) -> str:
        return f"Cierre {self.fecha} · {self.cajero}"

    @classmethod
    def ultimo_de(cls, cajero):
        return cls.objects.filter(cajero=cajero).order_by("-secuencia").first()

    def datos_guardados(self) -> dict:
        """El corte tal como quedó al generarse (no se recalcula con datos posteriores).

        Devuelve el mismo formato que `calcular`, para usar las mismas plantillas y el mismo CSV.
        Las facturas se leen de la foto guardada; los cierres anteriores a esa foto las consultan.
        """
        d = self.desglose or {}
        dec = lambda v: Decimal(str(v)) if v not in (None, "") else Decimal("0.00")  # noqa: E731
        lineas = []
        for linea in d.get("lineas", []):
            lineas.append(
                {
                    **linea,
                    **{k: dec(linea.get(k)) for k in
                       ("recibido", "aplicado_local", "vuelto_local", "devuelto", "devuelto_local")},
                }
            )
        if "facturas" in d:
            facturas = [
                {**f, "total_local": dec(f["total_local"]), "total_usd": dec(f["total_usd"]), "descuento_local": dec(f.get("descuento_local")),
                 "pagada_en": parse_datetime(f["pagada_en"]) if f.get("pagada_en") else None}
                for f in d["facturas"]
            ]
        else:
            facturas = CierreCaja.calcular(self.cajero, self.desde, self.hasta)["facturas"]
        return {
            "desde": self.desde,
            "hasta": self.hasta,
            "lineas": lineas,
            "facturas": facturas,
            "cantidad_facturas": self.cantidad_facturas,
            "facturas_anuladas": self.facturas_anuladas,
            "cantidad_pagos": d.get("cantidad_pagos", 0),
            "cantidad_devoluciones": d.get("cantidad_devoluciones", 0),
            "devuelto_local": dec(d.get("devuelto_local")),
            "total_local": self.total_local,
            "total_usd": self.total_usd,
            "vuelto_local": dec(d.get("vuelto_local")),
            "efectivo_bs_neto": dec(d.get("efectivo_bs_neto")),
            "divisas_usd": dec(d.get("divisas_usd")),
            "financiado_local": dec(d.get("financiado_local")),
            "financiado_usd": dec(d.get("financiado_usd")),
            "recibido_local": self.total_local - dec(d.get("financiado_local")),
            "descuentos_local": dec(d.get("descuentos_local")),
            "propinas_local": dec(d.get("propinas_local")),
            "iva_local": dec(d.get("iva_local")),
            "base_local": dec(d.get("base_local")),
            "igtf_local": dec(d.get("igtf_local")),
            "propinas": [{**x, "monto": dec(x.get("monto")), "monto_local": dec(x.get("monto_local"))}
                         for x in d.get("propinas", [])],
        }

    @classmethod
    def calcular(cls, cajero=None, desde=None, hasta=None) -> dict:
        """Totales de cobros y ventas en (desde, hasta]. `cajero=None` consolida a todos."""
        hasta = hasta or timezone.now()
        pagos = Pago.objects.select_related("factura").filter(registrado_en__lte=hasta)
        devoluciones = Devolucion.objects.select_related("factura", "pago").filter(registrado_en__lte=hasta)
        # Una factura cobrada y luego devuelta se sigue contando en el período en que se cobró.
        facturas = Factura.objects.select_related("pedido__mesa", "pedido__mesero", "cajero").filter(
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
        igtf_total = igtf_devuelto = Decimal("0.00")
        efectivo_bs = Decimal("0.00")
        divisas_usd = Decimal("0.00")
        financiado_local = financiado_usd = Decimal("0.00")
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
            igtf_total += pago.igtf_local
            if pago.metodo == Pago.Metodo.EFECTIVO:
                efectivo_bs += pago.monto_recibido
            elif pago.metodo == Pago.Metodo.DIVISA:
                divisas_usd += pago.monto_recibido
            elif pago.metodo == Pago.Metodo.CASHEA:
                financiado_local += pago.monto_local
                financiado_usd += pago.monto_recibido

        cantidad_devoluciones = 0
        for dev in devoluciones:
            cantidad_devoluciones += 1
            linea = linea_de(dev.metodo, dev.moneda)
            linea["devuelto"] += dev.monto
            linea["devuelto_local"] += dev.monto_local
            total_local -= dev.monto_local
            total_usd -= _redondear(dev.monto_local / dev.factura.tasa_cambio)
            devuelto_total_local += dev.monto_local
            igtf_devuelto += dev.pago.igtf_local if dev.pago_id else Decimal("0.00")
            if dev.metodo == Pago.Metodo.EFECTIVO:
                efectivo_bs -= dev.monto
            elif dev.metodo == Pago.Metodo.DIVISA:
                divisas_usd -= dev.monto
            elif dev.metodo == Pago.Metodo.CASHEA:
                financiado_local -= dev.monto_local
                financiado_usd -= dev.monto

        lineas = sorted(acumulado.values(), key=lambda x: x["metodo_label"])
        facturas = [_foto_factura(f) for f in facturas.order_by("pagada_en")]

        # Propinas: entran a la caja pero se les deben a los meseros (se informan aparte).
        propinas = Propina.objects.filter(registrada_en__lte=hasta)
        if desde:
            propinas = propinas.filter(registrada_en__gt=desde)
        if cajero is not None:
            propinas = propinas.filter(registrada_por=cajero)
        por_metodo: dict[tuple[str, str], dict] = {}
        for propina in propinas:
            fila = por_metodo.setdefault((propina.metodo, propina.moneda), {
                "metodo": propina.metodo, "metodo_label": Pago.Metodo(propina.metodo).label, "moneda": propina.moneda,
                "cantidad": 0, "monto": Decimal("0.00"), "monto_local": Decimal("0.00"),
            })
            fila["cantidad"] += 1
            fila["monto"] += propina.monto
            fila["monto_local"] += propina.monto_local
        propinas_lineas = sorted(por_metodo.values(), key=lambda x: x["metodo_label"])
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
            "financiado_local": financiado_local,
            "financiado_usd": financiado_usd,
            "recibido_local": total_local - financiado_local,
            "descuentos_local": sum((f["descuento_local"] for f in facturas if f["estado"] != Factura.Estado.ANULADA), Decimal("0.00")),
            "propinas": propinas_lineas,
            "propinas_local": sum((x["monto_local"] for x in propinas_lineas), Decimal("0.00")),
            "iva_local": sum((f["iva_local"] for f in facturas if f["estado"] != Factura.Estado.ANULADA), Decimal("0.00")),
            "base_local": sum((f["base_local"] for f in facturas if f["estado"] != Factura.Estado.ANULADA), Decimal("0.00")),
            "igtf_local": igtf_total - igtf_devuelto,
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
            or datos["propinas"]
        ):
            raise ValidationError("No hay movimientos nuevos desde el último cierre.")
        tasa = TasaCambio.vigente()
        try:
            with transaction.atomic():
                return cls.objects.create(
                    fecha=timezone.localdate(),
                    cajero=cajero,
                    secuencia=(ultimo.secuencia + 1) if ultimo else 1,
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
                        "financiado_local": str(datos["financiado_local"]),
                        "financiado_usd": str(datos["financiado_usd"]),
                        "cantidad_pagos": datos["cantidad_pagos"],
                        "cantidad_devoluciones": datos["cantidad_devoluciones"],
                        "devuelto_local": str(datos["devuelto_local"]),
                        "descuentos_local": str(datos["descuentos_local"]),
                        "propinas_local": str(datos["propinas_local"]),
                        "iva_local": str(datos["iva_local"]),
                        "base_local": str(datos["base_local"]),
                        "igtf_local": str(datos["igtf_local"]),
                        "propinas": [
                            {k: (str(v) if isinstance(v, Decimal) else v) for k, v in linea.items()}
                            for linea in datos["propinas"]
                        ],
                        "facturas": [
                            {k: (v.isoformat() if hasattr(v, "isoformat") else str(v) if isinstance(v, Decimal) else v)
                             for k, v in f.items()}
                            for f in datos["facturas"]
                        ],
                    },
                )
        except IntegrityError:
            raise ValidationError("Ya se generó un cierre para este tramo. Revisa el historial de cierres.")


def _foto_factura(f: "Factura") -> dict:
    """Datos de una factura tal como se muestran en el cierre, sin depender de su estado futuro."""
    return {
        "pk": f.pk,
        "numero": f.numero,
        "mesa": f.pedido.mesa.numero if f.pedido.mesa_id else f.pedido.codigo,
        "cajero": f.cajero.username,
        "total_local": f.total_local,
        "total_usd": f.total_usd,
        "descuento_local": f.descuento_local,
        "iva_local": f.iva_local,
        "base_local": f.base_imponible_local,
        "mesero": f.pedido.mesero.username,
        "pagada_en": f.pagada_en,
        "estado": f.estado,
    }


class SolicitudCuenta(models.Model):
    """La mesa pide la cuenta desde el menú digital y dice cómo va a pagar.

    Con pago móvil, Binance o Zinli el cliente ve los datos y el monto, paga desde su teléfono y
    reporta la referencia: en caja solo se confirma que llegó. Con efectivo, divisa o tarjeta se le
    indica que pase por caja. Así la cola de caja se acorta.
    """

    class Estado(models.TextChoices):
        SOLICITADA = "SOL", "Cuenta solicitada"
        REPORTADA = "REP", "Pago reportado"
        CONFIRMADA = "CON", "Pago confirmado"
        RECHAZADA = "REC", "Pago no encontrado"

    ACTIVAS = (Estado.SOLICITADA, Estado.REPORTADA, Estado.RECHAZADA)

    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="solicitudes_cuenta")
    metodo = models.CharField(max_length=3, choices=Pago.Metodo.choices)
    estado = models.CharField(max_length=3, choices=Estado.choices, default=Estado.SOLICITADA)
    monto_local = models.DecimalField(max_digits=12, decimal_places=2, help_text="Total a pagar en Bs. al pedirla.")
    monto_usd = models.DecimalField(max_digits=12, decimal_places=2)
    propina = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        help_text="Propina que el cliente sumó al pagar, en la moneda del método.",
    )
    referencia = models.CharField(max_length=60, blank=True)
    origen = models.CharField(max_length=80, blank=True, help_text="Banco o teléfono desde el que pagó (opcional).")
    datos_cliente = models.JSONField(default=dict, blank=True, help_text="Datos de factura personalizada (vacío = genérica).")
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)
    revisada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="cuentas_revisadas"
    )
    pago = models.ForeignKey(Pago, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "solicitud de cuenta"
        verbose_name_plural = "solicitudes de cuenta"
        ordering = ["-creada_en"]
        constraints = [
            models.UniqueConstraint(fields=["pedido"], condition=Q(estado__in=["SOL", "REP", "REC"]),
                                    name="uniq_solicitud_cuenta_activa"),
        ]

    def __str__(self) -> str:
        return f"Cuenta de {self.pedido.etiqueta} · {self.get_metodo_display()}"

    @property
    def es_digital(self) -> bool:
        return self.metodo in Pago.METODOS_DIGITALES

    @property
    def en_usd(self) -> bool:
        return self.metodo in Pago.METODOS_USD

    @property
    def monto(self) -> Decimal:
        """Lo que el cliente debe transferir, en la moneda del método."""
        return self.monto_usd if self.en_usd else self.monto_local

    @property
    def total_a_pagar(self) -> Decimal:
        return self.monto + self.propina

    @classmethod
    def activa_de(cls, pedido) -> "SolicitudCuenta | None":
        return cls.objects.filter(pedido=pedido, estado__in=cls.ACTIVAS).first()

    @classmethod
    @transaction.atomic
    def pedir(cls, pedido, metodo: str, datos_cliente: dict | None = None) -> "SolicitudCuenta":
        """Crea (o cambia de método) la solicitud de cuenta y pasa la mesa a «Por facturar»."""
        if metodo not in Pago.Metodo.values:
            raise ValidationError("Elige cómo vas a pagar.")
        pedido = Pedido.objects.select_for_update().select_related("mesa").get(pk=pedido.pk)
        pedido.solicitar_cuenta()
        factura = pedido.factura_activa
        if factura is not None:
            if factura.estado != Factura.Estado.ABIERTA:
                raise ValidationError("Tu cuenta ya está pagada. ¡Gracias por visitarnos!")
            monto_local, tasa = factura.saldo_local, Decimal(factura.tasa_cambio)
        else:
            monto_local, tasa = _redondear(pedido.subtotal), TasaCambio.vigente().valor
        # En dólares se redondea hacia arriba al centavo para que el pago siempre cubra el total.
        monto_usd = (monto_local / tasa).quantize(CENTAVO, rounding="ROUND_CEILING")
        actual = cls.objects.select_for_update().filter(pedido=pedido, estado__in=cls.ACTIVAS).first()
        if actual is not None:
            if actual.estado == cls.Estado.REPORTADA:
                raise ValidationError("Ya reportaste tu pago: caja lo está verificando.")
            actual.metodo, actual.monto_local, actual.monto_usd = metodo, monto_local, monto_usd
            actual.estado, actual.referencia, actual.origen = cls.Estado.SOLICITADA, "", ""
            actual.propina = Decimal("0.00")
            actual.datos_cliente = datos_cliente or {}
            actual.save()
            solicitud = actual
        else:
            solicitud = cls.objects.create(pedido=pedido, metodo=metodo, monto_local=monto_local, monto_usd=monto_usd,
                                           datos_cliente=datos_cliente or {})
        if factura is not None and datos_cliente:
            factura.guardar_datos_cliente(datos_cliente)
        return solicitud

    @transaction.atomic
    def reportar(self, referencia: str, origen: str = "", propina: Decimal = Decimal("0.00"), telefono: str = "") -> None:
        """El cliente avisa que pagó. Con pago móvil debe dar el teléfono desde el que pagó (así caja lo ubica)."""
        import re
        solicitud = SolicitudCuenta.objects.select_for_update().get(pk=self.pk)
        if not solicitud.es_digital:
            raise ValidationError("Con este método se paga en caja.")
        if solicitud.estado not in (self.Estado.SOLICITADA, self.Estado.RECHAZADA):
            raise ValidationError("Este pago ya fue reportado.")
        referencia = " ".join((referencia or "").split())[:60]
        if len(referencia) < 4:
            raise ValidationError("Escribe la referencia completa del pago (al menos 4 caracteres).")
        propina = _redondear(propina or Decimal("0.00"))
        if propina < 0 or propina > solicitud.monto:
            raise ValidationError("La propina debe ser un monto entre cero y el total de la cuenta.")
        origen = " ".join((origen or "").split())[:60]
        if solicitud.metodo == Pago.Metodo.PAGO_MOVIL:
            telefono = re.sub(r"[\s-]", "", telefono or "")
            if not re.fullmatch(r"(\+?58)?0?4\d{9}", telefono):
                raise ValidationError("Escribe el teléfono desde el que hiciste el pago móvil (ej.: 0414-1234567).")
            digitos = telefono[-10:]
            telefono = f"0{digitos[:3]}-{digitos[3:]}"
            origen = f"Tel. {telefono}" + (f" · {origen}" if origen else "")
        solicitud.referencia, solicitud.origen = referencia, origen[:80]
        solicitud.propina = propina
        solicitud.estado = self.Estado.REPORTADA
        solicitud.save(update_fields=["referencia", "origen", "propina", "estado", "actualizada_en"])
        self.refresh_from_db()

    @transaction.atomic
    def confirmar(self, factura: "Factura", usuario) -> Pago:
        """Caja verificó que el dinero llegó: se registra el pago (y la propina, si la sumó) con la referencia del cliente."""
        solicitud = SolicitudCuenta.objects.select_for_update().get(pk=self.pk)
        if solicitud.estado != self.Estado.REPORTADA:
            raise ValidationError("No hay un pago reportado pendiente de confirmar.")
        if factura.pedido_id != solicitud.pedido_id:
            raise ValidationError("El pago reportado es de otra cuenta.")
        pago, _ = factura.cobrar(metodo=solicitud.metodo, monto=solicitud.monto, referencia=solicitud.referencia,
                                 pagador=solicitud.origen)
        if solicitud.propina > 0:
            Propina.registrar(factura, metodo=solicitud.metodo, monto=solicitud.propina, usuario=usuario)
        solicitud.estado, solicitud.revisada_por, solicitud.pago = self.Estado.CONFIRMADA, usuario, pago
        solicitud.save(update_fields=["estado", "revisada_por", "pago", "actualizada_en"])
        return pago

    @transaction.atomic
    def rechazar(self, usuario) -> None:
        solicitud = SolicitudCuenta.objects.select_for_update().get(pk=self.pk)
        if solicitud.estado != self.Estado.REPORTADA:
            raise ValidationError("No hay un pago reportado pendiente de revisar.")
        solicitud.estado, solicitud.revisada_por = self.Estado.RECHAZADA, usuario
        solicitud.save(update_fields=["estado", "revisada_por", "actualizada_en"])


def datos_cliente_validos(crudos) -> tuple[dict | None, str]:
    """Valida los datos de una factura personalizada. Devuelve (datos limpios, error)."""
    import re

    from django.core.validators import validate_email

    if not isinstance(crudos, dict):
        return None, "Datos de factura no válidos."
    limpio = {k: " ".join(str(crudos.get(k) or "").split()) for k in ("nombre", "documento", "direccion", "telefono", "correo")}
    limpio["documento"] = limpio["documento"].upper().replace(" ", "")
    if len(limpio["nombre"]) < 3:
        return None, "Escribe el nombre o la razón social para la factura."
    if not re.fullmatch(r"[VEJGP]-?\d{6,9}(-?\d)?", limpio["documento"]):
        return None, "La cédula o el RIF no es válido (ej.: V-12345678 o J-123456789)."
    if not re.match(r"^[VEJGP]-", limpio["documento"]):
        limpio["documento"] = limpio["documento"][0] + "-" + limpio["documento"][1:]
    if len(limpio["direccion"]) < 5:
        return None, "Escribe la dirección fiscal para la factura."
    if limpio["telefono"] and not re.fullmatch(r"[0-9+()\- ]{7,20}", limpio["telefono"]):
        return None, "El teléfono no es válido."
    if limpio["correo"]:
        try:
            validate_email(limpio["correo"])
        except ValidationError:
            return None, "El correo no es válido."
    limpio["nombre"], limpio["direccion"] = limpio["nombre"][:120], limpio["direccion"][:200]
    limpio["telefono"], limpio["correo"] = limpio["telefono"][:20], limpio["correo"][:254]
    return limpio, ""


# ---- Cashea: ventas financiadas ---------------------------------------------------------------
def miercoles_de_pago(fecha):
    """Cashea paga cada miércoles lo financiado en la semana anterior (lunes a domingo)."""
    from datetime import timedelta

    lunes = fecha - timedelta(days=fecha.weekday())
    return lunes + timedelta(days=9)


class LiquidacionCashea(models.Model):
    """Pago que Cashea le hace al local un miércoles por las ventas financiadas de la semana anterior."""

    fecha = models.DateField(help_text="Día en que llegó el dinero.")
    moneda = models.CharField(max_length=3, choices=Pago.Moneda.choices, default=Pago.Moneda.USD)
    monto_recibido = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    tasa_cambio = models.DecimalField(max_digits=14, decimal_places=4, help_text="Tasa usada si llegó en bolívares.")
    monto_recibido_usd = models.DecimalField(max_digits=12, decimal_places=2)
    monto_esperado_usd = models.DecimalField(max_digits=12, decimal_places=2)
    referencia = models.CharField(max_length=60, blank=True)
    nota = models.CharField(max_length=200, blank=True)
    registrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    registrada_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "pago de Cashea"
        verbose_name_plural = "pagos de Cashea"
        ordering = ["-fecha", "-id"]

    def __str__(self) -> str:
        return f"Cashea {self.fecha} · ${self.monto_recibido_usd}"

    @property
    def descuento_usd(self) -> Decimal:
        """Lo que Cashea retuvo (comisión). Negativo si pagó de más."""
        return self.monto_esperado_usd - self.monto_recibido_usd

    @classmethod
    @transaction.atomic
    def registrar(cls, *, financiamientos, fecha, moneda: str, monto: Decimal, referencia: str, usuario,
                  nota: str = "") -> "LiquidacionCashea":
        ids = [f.pk for f in financiamientos]
        pendientes = list(FinanciamientoCashea.objects.select_for_update().filter(
            pk__in=ids, estado=FinanciamientoCashea.Estado.PENDIENTE))
        if not pendientes or len(pendientes) != len(set(ids)):
            raise ValidationError("Elige ventas financiadas que estén por cobrar (alguna ya se registró o se anuló).")
        if moneda not in Pago.Moneda.values:
            raise ValidationError("Moneda no válida.")
        if monto is None or monto <= 0:
            raise ValidationError("Escribe el monto que pagó Cashea.")
        if fecha > timezone.localdate():
            raise ValidationError("La fecha del pago no puede ser futura.")
        esperado = sum((f.monto_usd for f in pendientes), Decimal("0.00"))
        tasa = TasaCambio.vigente().valor
        recibido_usd = monto if moneda == Pago.Moneda.USD else _redondear(monto / tasa)
        if recibido_usd > esperado * Decimal("1.05") + Decimal("1"):
            raise ValidationError(f"El monto supera lo financiado en esas ventas (${esperado}). Revisa la selección.")
        liquidacion = cls.objects.create(
            fecha=fecha, moneda=moneda, monto_recibido=monto, tasa_cambio=tasa, monto_recibido_usd=recibido_usd,
            monto_esperado_usd=esperado, referencia=" ".join((referencia or "").split())[:60],
            nota=" ".join((nota or "").split())[:200], registrada_por=usuario,
        )
        FinanciamientoCashea.objects.filter(pk__in=[f.pk for f in pendientes]).update(
            estado=FinanciamientoCashea.Estado.COBRADO, liquidacion=liquidacion)
        return liquidacion


class FinanciamientoCashea(models.Model):
    """Parte de una factura que financió Cashea: no entra a la caja el día de la venta, se cobra el miércoles."""

    class Estado(models.TextChoices):
        PENDIENTE = "PEN", "Por cobrar"
        COBRADO = "COB", "Cobrado"
        ANULADO = "ANU", "Anulado"

    factura = models.ForeignKey(Factura, on_delete=models.PROTECT, related_name="financiamientos")
    pago = models.OneToOneField(Pago, on_delete=models.PROTECT, related_name="financiamiento")
    orden = models.CharField(max_length=60, help_text="Número de orden de Cashea.")
    monto_usd = models.DecimalField(max_digits=12, decimal_places=2)
    monto_local = models.DecimalField(max_digits=12, decimal_places=2)
    fecha_venta = models.DateField()
    fecha_pago = models.DateField(help_text="Miércoles en que Cashea debe pagarlo.")
    estado = models.CharField(max_length=3, choices=Estado.choices, default=Estado.PENDIENTE)
    liquidacion = models.ForeignKey(
        LiquidacionCashea, on_delete=models.PROTECT, null=True, blank=True, related_name="financiamientos"
    )
    anulado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "venta financiada (Cashea)"
        verbose_name_plural = "ventas financiadas (Cashea)"
        ordering = ["fecha_pago", "fecha_venta", "id"]
        indexes = [models.Index(fields=["estado", "fecha_pago"])]

    def __str__(self) -> str:
        return f"Cashea {self.orden} · ${self.monto_usd}"

    @classmethod
    def desde_pago(cls, pago: Pago) -> "FinanciamientoCashea":
        fecha = timezone.localdate(pago.registrado_en)
        return cls.objects.create(
            factura=pago.factura, pago=pago, orden=pago.referencia, monto_usd=pago.monto_recibido,
            monto_local=pago.monto_local, fecha_venta=fecha, fecha_pago=miercoles_de_pago(fecha),
        )

    @property
    def atrasado(self) -> bool:
        return self.estado == self.Estado.PENDIENTE and self.fecha_pago < timezone.localdate()

    @property
    def por_devolver(self) -> bool:
        """Venta anulada después de que Cashea ya la pagó: ese dinero se le debe devolver a Cashea."""
        return self.estado == self.Estado.ANULADO and self.liquidacion_id is not None

    def anular(self) -> None:
        self.estado, self.anulado_en = self.Estado.ANULADO, timezone.now()
        self.save(update_fields=["estado", "anulado_en"])
