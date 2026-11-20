"""Ajustes del local que el administrador edita desde el sistema (sin tocar el servidor)."""

from django.core.cache import cache
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

CLAVE_CACHE = "gestion:ajustes_local"


class AjustesLocal(models.Model):
    """Una sola fila: datos para cobrar desde el teléfono del cliente y enlaces de delivery."""

    pm_banco = models.CharField("Banco", max_length=60, blank=True)
    pm_telefono = models.CharField(
        "Teléfono del pago móvil", max_length=20, blank=True,
        validators=[RegexValidator(r"^0?4\d{2}-?\d{7}$", "Usa el formato 0414-1234567.")],
    )
    pm_documento = models.CharField(
        "Cédula o RIF", max_length=20, blank=True,
        validators=[RegexValidator(r"^[VEJGPvejgp]-?\d{6,9}(-?\d)?$", "Usa el formato V-12345678 o J-123456789.")],
    )
    pm_titular = models.CharField("Titular", max_length=80, blank=True)
    binance_id = models.CharField(
        "Binance Pay ID o correo", max_length=80, blank=True,
        help_text="Lo que el cliente escribe en Binance para enviarte el pago.",
    )
    zinli_correo = models.EmailField("Correo de Zinli", blank=True)
    cashea_activo = models.BooleanField(
        "Aceptar Cashea en caja", default=True,
        help_text="Ventas financiadas: el cliente paga la inicial y Cashea paga el resto al local cada miércoles.",
    )
    retiro_activo = models.BooleanField(
        "Recibir pedidos para retirar desde el menú en línea", default=True,
        help_text="Solo con pago móvil, Binance o Zinli configurados: el cliente paga y sube el comprobante.",
    )
    pedidosya_url = models.URLField("Enlace a PedidosYa", blank=True, help_text="La página del local dentro de PedidosYa (opcional).")
    yummy_url = models.URLField("Enlace a Yummy", blank=True, help_text="La página del local dentro de Yummy (opcional).")
    mostrar_delivery = models.BooleanField(
        "Mostrar el aviso de delivery en el menú", default=True,
        help_text="«Pide a domicilio por PedidosYa y Yummy», aunque no tengas los enlaces.",
    )
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ajustes del local"
        verbose_name_plural = "ajustes del local"

    def __str__(self) -> str:
        return "Ajustes del local"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(CLAVE_CACHE)

    @classmethod
    def actual(cls) -> "AjustesLocal":
        ajustes = cache.get(CLAVE_CACHE)
        if ajustes is None:
            ajustes, _ = cls.objects.get_or_create(pk=1)
            cache.set(CLAVE_CACHE, ajustes, 300)
        return ajustes

    @property
    def pago_movil_listo(self) -> bool:
        return all([self.pm_banco, self.pm_telefono, self.pm_documento])

    def metodos_cliente(self) -> list[dict]:
        """Métodos que el cliente puede elegir al pedir la cuenta (los digitales, solo si hay datos)."""
        metodos = []
        if self.pago_movil_listo:
            metodos.append({"clave": "PMO", "nombre": "Pago móvil", "ayuda": "Pagas desde tu teléfono", "digital": True})
        if self.binance_id:
            metodos.append({"clave": "BIN", "nombre": "Binance Pay", "ayuda": "En dólares (USDT)", "digital": True})
        if self.zinli_correo:
            metodos.append({"clave": "ZIN", "nombre": "Zinli", "ayuda": "En dólares", "digital": True})
        metodos += [
            {"clave": "EFE", "nombre": "Efectivo en bolívares", "ayuda": "Pagas en caja", "digital": False},
            {"clave": "DIV", "nombre": "Dólares en efectivo", "ayuda": "Pagas en caja", "digital": False},
            {"clave": "TAR", "nombre": "Tarjeta", "ayuda": "Punto de venta en caja", "digital": False},
        ]
        return metodos

    def metodos_retiro(self) -> list[dict]:
        """Para pedidos para retirar solo se aceptan pagos digitales (se pagan antes de cocinar)."""
        return [m for m in self.metodos_cliente() if m["digital"]] if self.retiro_activo else []

    def datos_de_pago(self, metodo: str) -> list[tuple[str, str]]:
        """(etiqueta, valor) que el cliente necesita para pagar con un método digital."""
        if metodo == "PMO":
            filas = [("Banco", self.pm_banco), ("Teléfono", self.pm_telefono), ("Cédula / RIF", self.pm_documento)]
            if self.pm_titular:
                filas.append(("Titular", self.pm_titular))
            return filas
        if metodo == "BIN":
            return [("Binance Pay ID / correo", self.binance_id)]
        if metodo == "ZIN":
            return [("Correo Zinli", self.zinli_correo)]
        return []



class GastoFijo(models.Model):
    """Gasto que se repite cada mes (alquiler, servicios, nómina…). Cuenta en cada mes de su vigencia."""

    class Categoria(models.TextChoices):
        ALQUILER = "ALQ", "Alquiler"
        SERVICIOS = "SER", "Servicios (luz, agua, gas, internet)"
        NOMINA = "NOM", "Nómina"
        IMPUESTOS = "IMP", "Impuestos y permisos"
        MANTENIMIENTO = "MAN", "Mantenimiento"
        OTRO = "OTR", "Otro"

    nombre = models.CharField(max_length=80, unique=True)
    categoria = models.CharField(max_length=3, choices=Categoria.choices, default=Categoria.OTRO)
    monto_usd = models.DecimalField("Monto mensual (USD)", max_digits=12, decimal_places=2,
                                    validators=[MinValueValidator(Decimal("0.01"))])
    desde = models.DateField("Se paga desde", default=date.today, help_text="Mes en que empieza a contar.")
    hasta = models.DateField("Hasta", null=True, blank=True, help_text="Vacío: sigue vigente.")
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "gasto fijo"
        verbose_name_plural = "gastos fijos"
        ordering = ["categoria", "nombre"]

    def __str__(self) -> str:
        return self.nombre

    @property
    def vigente(self) -> bool:
        return self.hasta is None or self.hasta >= date.today()

    def aplica_en(self, inicio_mes: date, fin_mes: date) -> bool:
        return self.desde <= fin_mes and (self.hasta is None or self.hasta >= inicio_mes)


class GastoExtraordinario(models.Model):
    """Gasto fuerte o puntual (una máquina, una reparación…). Se descuenta en el mes de su fecha
    o, si se reparte, una cuota igual durante varios meses (amortización)."""

    class Categoria(models.TextChoices):
        EQUIPOS = "EQU", "Equipos y maquinaria"
        MOBILIARIO = "MOB", "Mobiliario y remodelación"
        REPARACION = "REP", "Reparaciones"
        OTRO = "OTR", "Otro"

    INVERSION = (Categoria.EQUIPOS, Categoria.MOBILIARIO)

    fecha = models.DateField(default=date.today)
    descripcion = models.CharField("Descripción", max_length=120)
    categoria = models.CharField(max_length=3, choices=Categoria.choices, default=Categoria.EQUIPOS)
    monto_usd = models.DecimalField("Monto (USD)", max_digits=12, decimal_places=2,
                                    validators=[MinValueValidator(Decimal("0.01"))])
    meses = models.PositiveSmallIntegerField(
        "Repartir en (meses)", default=1, validators=[MinValueValidator(1), MaxValueValidator(60)],
        help_text="1 = se descuenta completo ese mes. Para una máquina cara puedes repartirlo (ej.: 12 meses).",
    )
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="gastos_registrados")
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "gasto extraordinario"
        verbose_name_plural = "gastos extraordinarios"
        ordering = ["-fecha", "-id"]

    def __str__(self) -> str:
        return self.descripcion

    @property
    def cuota_usd(self) -> Decimal:
        return (self.monto_usd / self.meses).quantize(Decimal("0.01"))

    def _indice_mes(self, anio: int, mes: int) -> int:
        return (anio - self.fecha.year) * 12 + (mes - self.fecha.month)

    def cuota_en(self, anio: int, mes: int) -> Decimal:
        """Lo que toca descontar en ese mes (la última cuota absorbe el redondeo)."""
        i = self._indice_mes(anio, mes)
        if not 0 <= i < self.meses:
            return Decimal("0.00")
        if i == self.meses - 1:
            return self.monto_usd - self.cuota_usd * (self.meses - 1)
        return self.cuota_usd
