"""Verifica que el historial de movimientos de cada insumo explique su stock actual.

    python manage.py auditar_inventario
"""

from django.core.management.base import BaseCommand

from config.formato import num
from inventario.servicios import auditar_inventario


class Command(BaseCommand):
    help = "Revisa que los movimientos de inventario encadenen y terminen en el stock registrado."

    def handle(self, *args, **opciones):
        problemas = auditar_inventario()
        if not problemas:
            self.stdout.write(self.style.SUCCESS("Inventario auditado: el historial de todos los insumos cuadra con su stock."))
            return
        for p in problemas:
            detalle = []
            if p["saltos"]:
                detalle.append(f"{p['saltos']} salto(s) en la cadena de saldos")
            if p["diferencia"]:
                detalle.append(f"diferencia de {num(p['diferencia'])} {p['insumo'].abreviatura} con el stock registrado")
            self.stdout.write(self.style.WARNING(f"{p['insumo'].nombre}: " + "; ".join(detalle)))
        self.stdout.write(self.style.WARNING(f"{len(problemas)} insumo(s) con diferencias: revisa su kárdex o haz un ajuste por conteo."))
