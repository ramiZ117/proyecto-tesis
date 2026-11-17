"""Números en formato venezolano para los mensajes de pantalla (coma decimal, punto de miles)."""

from decimal import Decimal


def num(valor, decimales: int | None = None) -> str:
    """`num(Decimal("1234.5"), 2)` → "1.234,50"; sin `decimales`, quita los ceros sobrantes ("0,2")."""
    valor = Decimal(valor)
    if decimales is None:
        texto = f"{valor.normalize():,f}"
        if "." in texto:
            texto = texto.rstrip("0").rstrip(".")
    else:
        texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")
