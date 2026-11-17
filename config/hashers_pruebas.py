"""Hasher solo para la suite de pruebas.

Es el mismo PBKDF2-SHA256 de producción (las contraseñas se siguen guardando con el prefijo
`pbkdf2_sha256$`, que es lo que verifica RNF-04), pero con pocas iteraciones: el costo real
está pensado para frenar ataques y hacía que la suite tardara más de dos minutos.
"""

from django.contrib.auth.hashers import PBKDF2PasswordHasher


class PBKDF2Rapido(PBKDF2PasswordHasher):
    iterations = 1_000
