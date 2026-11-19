"""Revisa la instalación y dice qué falta: migraciones, .env, asistente de IA, correo y tasa BCV.

    python manage.py diagnostico            # revisión completa (prueba la IA y el correo de verdad)
    python manage.py diagnostico --rapido   # sin conectarse a servicios externos

No envía correos: solo comprueba que el servidor SMTP acepte la conexión y la clave.
"""

import platform
import sys
from pathlib import Path

import django
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Diagnóstico de la instalación (útil cuando algo funciona en una PC y en otra no)."

    def add_arguments(self, parser):
        parser.add_argument("--rapido", action="store_true", help="No probar servicios externos.")

    def ok(self, texto):
        self.stdout.write(self.style.SUCCESS("  ✔ " + texto))

    def mal(self, texto):
        self.problemas += 1
        self.stdout.write(self.style.ERROR("  ✘ " + texto))

    def aviso(self, texto):
        self.stdout.write(self.style.WARNING("  • " + texto))

    def handle(self, *args, **opciones):
        self.problemas = 0
        self.stdout.write(f"Python {platform.python_version()} · Django {django.get_version()} · {platform.system()}")

        self.stdout.write("\nConfiguración")
        env = Path(settings.BASE_DIR) / ".env"
        (self.ok if env.exists() else self.aviso)(".env encontrado" if env.exists() else "No hay .env: se usan los valores por defecto")
        self.ok(f"DEBUG={'sí' if settings.DEBUG else 'no'} · hosts permitidos: {', '.join(settings.ALLOWED_HOSTS)}")
        if settings.CSRF_TRUSTED_ORIGINS:
            self.ok("Orígenes de formularios: " + ", ".join(settings.CSRF_TRUSTED_ORIGINS))

        self.stdout.write("\nBase de datos")
        pendientes = MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
        if pendientes:
            self.mal(f"Hay {len(pendientes)} migraciones sin aplicar: ejecuta «python manage.py migrate»")
        else:
            self.ok("Migraciones al día")
        from django.contrib.auth.models import User

        from inventario.models import Producto
        self.ok(f"{User.objects.count()} usuarios · {Producto.objects.count()} platos")
        if not Producto.objects.exists():
            self.aviso("La carta está vacía: carga los datos de prueba con «python manage.py seed_restaurante»")

        self.stdout.write("\nAsistente del menú")
        from menu.asistente import _llamar_modelo, proveedores_disponibles

        proveedores = proveedores_disponibles()
        if not proveedores:
            self.aviso("Sin claves de IA: responde el asistente local (funciona sin internet)")
        for p in proveedores:
            if opciones["rapido"]:
                self.ok(f"{p['nombre']} configurado ({p['modelo']})")
                continue
            try:
                texto = _llamar_modelo(p, "Responde solo: ok", [{"rol": "cliente", "texto": "di ok"}])
                self.ok(f"{p['nombre']} responde ({texto.strip()[:20]!r})")
            except Exception as exc:
                self.mal(f"{p['nombre']} no responde: {exc}. El menú usará el siguiente proveedor o el asistente local.")

        self.stdout.write("\nCorreo de las facturas")
        if "console" in settings.EMAIL_BACKEND:
            self.aviso("Sin servidor de correo (EMAIL_HOST vacío): las facturas se muestran en la consola")
        elif opciones["rapido"]:
            self.ok(f"Configurado: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        else:
            import smtplib

            from caja.correo import motivo_error_correo
            try:
                with smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT, timeout=15) as smtp:
                    if settings.EMAIL_USE_TLS:
                        smtp.starttls()
                    smtp.login(settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)
                self.ok(f"{settings.EMAIL_HOST} acepta la conexión y la clave · remitente {settings.DEFAULT_FROM_EMAIL}")
                if "resend.dev" in settings.DEFAULT_FROM_EMAIL:
                    self.aviso("Remitente de prueba de Resend: solo llega al correo dueño de la cuenta")
            except Exception as exc:
                self.mal(motivo_error_correo(exc))

        if not opciones["rapido"]:
            self.stdout.write("\nTasa BCV")
            from caja.tasa_bcv import consultar
            try:
                valor, fecha = consultar()
                self.ok(f"Fuente en línea: 1 USD = {valor} Bs ({fecha or 'sin fecha'})")
            except Exception as exc:
                self.aviso(f"No se pudo consultar la tasa en línea ({exc}); se usa la última guardada")

        self.stdout.write("\nArchivos")
        for nombre, ruta in (("Fotos (media)", settings.MEDIA_ROOT), ("Comprobantes (privado)", settings.PRIVADO_ROOT)):
            ruta = Path(ruta)
            try:
                ruta.mkdir(parents=True, exist_ok=True)
                prueba = ruta / ".prueba_escritura"
                prueba.write_text("ok")
                prueba.unlink()
                self.ok(f"{nombre}: se puede escribir en {ruta}")
            except OSError as exc:
                self.mal(f"{nombre}: no se puede escribir en {ruta} ({exc})")

        self.stdout.write("")
        if self.problemas:
            self.stdout.write(self.style.ERROR(f"{self.problemas} problema(s) encontrado(s)."))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("Todo en orden."))
