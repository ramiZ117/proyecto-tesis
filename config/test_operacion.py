"""Operación del sistema: respaldos, servidor de producción y configuración segura."""

import sqlite3
from contextlib import closing
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings


class RespaldoTests(SimpleTestCase):
    def setUp(self):
        self.carpeta = Path(tempfile.mkdtemp())
        self.base = self.carpeta / "db.sqlite3"
        with closing(sqlite3.connect(self.base)) as c, c:
            c.execute("create table t (x integer)")
            c.execute("insert into t values (42)")

    def respaldar(self, **opciones):
        salida = StringIO()
        bases = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": self.base}}
        with override_settings(DATABASES=bases, RESPALDOS_DIR=self.carpeta / "respaldos", RESPALDOS_CONSERVAR=30,
                               MEDIA_ROOT=self.carpeta / "media"):
            call_command("respaldar", stdout=salida, **opciones)
        return salida.getvalue()

    def test_crea_una_copia_verificada_y_legible(self):
        texto = self.respaldar()
        self.assertIn("Respaldo creado y verificado", texto)
        copias = list((self.carpeta / "respaldos").glob("el_amir_*.sqlite3"))
        self.assertEqual(len(copias), 1)
        with closing(sqlite3.connect(copias[0])) as c:
            self.assertEqual(c.execute("select x from t").fetchone()[0], 42)

    def test_incluye_las_fotos_de_los_platos(self):
        import zipfile

        (self.carpeta / "media" / "platos").mkdir(parents=True)
        (self.carpeta / "media" / "platos" / "tabule.jpg").write_bytes(b"foto")
        texto = self.respaldar()
        self.assertIn("Fotos de los platos", texto)
        zips = list((self.carpeta / "respaldos").glob("*_fotos.zip"))
        self.assertEqual(len(zips), 1)
        with zipfile.ZipFile(zips[0]) as z:
            self.assertIn("platos/tabule.jpg", z.namelist())

    def test_conserva_solo_los_mas_recientes(self):
        destino = self.carpeta / "respaldos"
        destino.mkdir()
        for dia in range(1, 6):
            (destino / f"el_amir_2026-01-0{dia}_120000.sqlite3").write_bytes(b"viejo")
        texto = self.respaldar(conservar=3)
        restantes = sorted(p.name for p in destino.glob("el_amir_*.sqlite3"))
        self.assertEqual(len(restantes), 3)
        self.assertNotIn("el_amir_2026-01-01_120000.sqlite3", restantes)
        self.assertIn("eliminados: 3", texto)

    def test_valida_las_opciones(self):
        with self.assertRaisesMessage(CommandError, "al menos 1"):
            self.respaldar(conservar=-1)
        self.base.unlink()
        with self.assertRaisesMessage(CommandError, "No existe"):
            self.respaldar()


class ConfiguracionTests(SimpleTestCase):
    def test_en_la_red_local_por_http_las_cookies_no_exigen_https(self):
        """Con DJANGO_DEBUG=0 y sin certificado, iniciar sesión por HTTP debe seguir funcionando."""
        # Se vuelve a leer config/settings.py con otras variables de entorno; la configuración
        # activa de Django (django.conf.settings) no cambia.
        import importlib
        import os
        import sys
        from unittest import mock

        with mock.patch.dict(os.environ, {"DJANGO_DEBUG": "0", "DJANGO_SECRET_KEY": "x" * 50, "DJANGO_HTTPS": "0"}):
            sys.modules.pop("config.settings", None)
            prod = importlib.import_module("config.settings")
            self.assertFalse(getattr(prod, "SESSION_COOKIE_SECURE", False))
            self.assertFalse(getattr(prod, "CSRF_COOKIE_SECURE", False))
            self.assertTrue(prod.SESSION_COOKIE_HTTPONLY)
        with mock.patch.dict(os.environ, {"DJANGO_DEBUG": "0", "DJANGO_SECRET_KEY": "x" * 50, "DJANGO_HTTPS": "1"}):
            sys.modules.pop("config.settings", None)
            prod = importlib.import_module("config.settings")
            self.assertTrue(prod.SESSION_COOKIE_SECURE)
        sys.modules.pop("config.settings", None)
        importlib.import_module("config.settings")

    def test_servir_exige_estaticos_en_produccion(self):
        with override_settings(DEBUG=False, STATIC_ROOT=Path(tempfile.mkdtemp())):
            with self.assertRaisesMessage(CommandError, "collectstatic"):
                call_command("servir", stdout=StringIO())
