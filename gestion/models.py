"""Ajustes del local que el administrador edita desde el sistema (sin tocar el servidor)."""

from django.core.cache import cache
from django.core.validators import RegexValidator
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
    retiro_activo = models.BooleanField(
        "Recibir pedidos para retirar desde el menú en línea", default=True,
        help_text="Solo con pago móvil, Binance o Zinli configurados: el cliente paga y sube el comprobante.",
    )
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
