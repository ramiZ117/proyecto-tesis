"""Código de mesa del QR: evita que alguien llame a una mesa desde fuera del local.

El QR de cada mesa lleva ?mesa=N&c=<firma>. La firma depende de SECRET_KEY, así que
no se puede adivinar el código de otra mesa cambiando el número en la dirección.
"""

from django.core import signing
from django.utils.crypto import constant_time_compare

_SAL = "el-amir.menu.mesa"


def codigo(numero: int) -> str:
    return signing.Signer(salt=_SAL).signature(str(numero))[:16]


def es_valido(numero, recibido) -> bool:
    if not recibido or not isinstance(recibido, str):
        return False
    try:
        numero = int(numero)
    except (TypeError, ValueError):
        return False
    return constant_time_compare(codigo(numero), recibido)
