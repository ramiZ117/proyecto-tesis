"""Servidor de producción para la red del restaurante (Windows o Linux).

    python manage.py servir                 # escucha en 0.0.0.0:8000
    python manage.py servir --puerto 8080

Usa waitress: un solo proceso con varios hilos. Así la caché en memoria (bloqueo de inicio de
sesión tras 5 intentos) es la misma para todas las peticiones, y SQLite atiende las escrituras
en fila (transaction_mode=IMMEDIATE).
"""

import socket

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Inicia el servidor de producción (waitress) para las estaciones del salón, la cocina y la caja."

    def add_arguments(self, parser):
        parser.add_argument("--host", default="0.0.0.0", help="Interfaz de red (0.0.0.0 = todas).")
        parser.add_argument("--puerto", type=int, default=8000)
        parser.add_argument("--hilos", type=int, default=8, help="Peticiones atendidas en paralelo.")

    def handle(self, *args, **opciones):
        try:
            from waitress import serve
        except ImportError as exc:  # pragma: no cover - depende de la instalación
            raise CommandError("Falta waitress: ejecuta `pip install -r requirements.txt`.") from exc
        if settings.DEBUG:
            self.stdout.write(self.style.WARNING(
                "DJANGO_DEBUG=1: estás sirviendo en modo desarrollo. En el local usa DJANGO_DEBUG=0 en el archivo .env."
            ))
        elif not (settings.STATIC_ROOT / "staticfiles.json").exists():
            raise CommandError("Faltan los archivos estáticos: ejecuta `python manage.py collectstatic --noinput` primero.")

        from config.wsgi import application

        ip_local = self._ip_local()
        self.stdout.write(self.style.SUCCESS(f"El Amir en http://{ip_local}:{opciones['puerto']}/ (Ctrl+C para detener)"))
        if opciones["host"] == "0.0.0.0" and ip_local not in settings.ALLOWED_HOSTS and "*" not in settings.ALLOWED_HOSTS:
            self.stdout.write(self.style.WARNING(
                f"Agrega {ip_local} a DJANGO_ALLOWED_HOSTS en .env para que las tablets y teléfonos puedan entrar."
            ))
        serve(application, host=opciones["host"], port=opciones["puerto"], threads=opciones["hilos"], ident="El Amir")

    @staticmethod
    def _ip_local() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))
                return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
