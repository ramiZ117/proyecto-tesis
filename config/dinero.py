from decimal import Decimal

from django.core.exceptions import ValidationError

from caja.models import TasaCambio


def precio_local_vigente(precio_usd: Decimal) -> Decimal:
    try:
        return TasaCambio.vigente().a_local(precio_usd)
    except ValidationError:
        return Decimal("0.00")
