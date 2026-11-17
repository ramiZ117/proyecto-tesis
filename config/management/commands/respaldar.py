"""Respaldo de la base de datos (riesgo de seguridad de la información, tesis 4.2.3).

    python manage.py respaldar               # copia en respaldos/ y conserva los 30 más recientes
    python manage.py respaldar --conservar 60

Usa la API de respaldo de SQLite: la copia es consistente aunque el sistema esté en uso.
Programarlo a diario: Programador de tareas de Windows o `cron` en Linux (ver docs/manual-tecnico.md).
"""

import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

registro = logging.getLogger("el_amir")


class Command(BaseCommand):
    help = "Crea una copia consistente de la base de datos y borra las más antiguas."

    def add_arguments(self, parser):
        parser.add_argument("--destino", default=None, help="Carpeta de respaldos (por defecto RESPALDOS_DIR).")
        parser.add_argument("--conservar", type=int, default=None, help="Cuántos respaldos guardar (por defecto 30).")

    def handle(self, *args, **opciones):
        if connection.vendor != "sqlite":
            raise CommandError("Este comando respalda bases SQLite.")
        origen = Path(settings.DATABASES["default"]["NAME"])
        if not origen.exists():
            raise CommandError(f"No existe la base de datos {origen}.")
        destino = Path(opciones["destino"] or settings.RESPALDOS_DIR)
        conservar = opciones["conservar"] or settings.RESPALDOS_CONSERVAR
        if conservar < 1:
            raise CommandError("--conservar debe ser al menos 1.")
        destino.mkdir(parents=True, exist_ok=True)

        archivo = destino / f"el_amir_{datetime.now():%Y-%m-%d_%H%M%S}.sqlite3"
        # closing(): el `with` de sqlite3 solo confirma la transacción, no cierra el archivo
        # (en Windows un archivo abierto no se puede mover ni borrar).
        with closing(sqlite3.connect(origen)) as fuente, closing(sqlite3.connect(archivo)) as copia:
            fuente.backup(copia)
        with closing(sqlite3.connect(archivo)) as copia:
            resultado = copia.execute("PRAGMA integrity_check").fetchone()[0]
        if resultado != "ok":
            archivo.unlink(missing_ok=True)
            raise CommandError(f"La copia no pasó la verificación de integridad: {resultado}")

        respaldos = sorted(destino.glob("el_amir_*.sqlite3"))
        borrados = respaldos[:-conservar] if len(respaldos) > conservar else []
        for viejo in borrados:
            viejo.unlink(missing_ok=True)

        tamano = archivo.stat().st_size / 1024
        registro.info("Respaldo creado: %s (%.0f KB)", archivo.name, tamano)
        self.stdout.write(self.style.SUCCESS(f"Respaldo creado y verificado: {archivo} ({tamano:.0f} KB)"))
        if borrados:
            self.stdout.write(f"Respaldos antiguos eliminados: {len(borrados)} (se conservan {conservar}).")
