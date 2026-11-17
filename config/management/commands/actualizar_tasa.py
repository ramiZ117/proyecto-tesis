"""Actualiza la tasa oficial del BCV (para programarlo varias veces al día).

    python manage.py actualizar_tasa
    python manage.py actualizar_tasa --forzar     # publica aunque cambie más de un 50 %
"""

from django.core.management.base import BaseCommand

from caja.tasa_bcv import actualizar


class Command(BaseCommand):
    help = "Consulta la tasa oficial del BCV y la publica si cambió."

    def add_arguments(self, parser):
        parser.add_argument("--forzar", action="store_true", help="Publica aunque el cambio sea muy grande.")

    def handle(self, *args, **opciones):
        resultado = actualizar(forzar=opciones["forzar"])
        estilo = self.style.SUCCESS if resultado.actualizada else self.style.WARNING
        self.stdout.write(estilo(resultado.mensaje))
