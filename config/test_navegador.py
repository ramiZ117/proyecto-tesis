"""Pruebas en un navegador real (Edge, Chrome o Chromium) con Playwright.

Cubren lo que las pruebas del servidor no ven: el JavaScript de la interfaz (envío sin recargar,
botones que llevan el dato, calculadora de vuelto). Se omiten si Playwright no está instalado:

    pip install -r requirements-dev.txt
    python manage.py test config.test_navegador
"""

import os
import re
import shutil
from decimal import Decimal
from unittest import skipUnless

from django.contrib.auth.models import Group, User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from caja.models import TasaCambio
from config.permisos import GRUPO_CAJA, GRUPO_COCINA, GRUPO_MESERO
from inventario.models import Categoria, Producto
from mesas.models import DetallePedido, Mesa, Pedido

try:
    from playwright.sync_api import sync_playwright

    HAY_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    HAY_PLAYWRIGHT = False

CLAVE = "Prueba-12345"


def _canal():
    """Usa el Edge o Chrome instalado si existe; si no, el Chromium que descarga Playwright."""
    for canal, rutas in (
        ("msedge", [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "/usr/bin/microsoft-edge"]),
        ("chrome", [r"C:\Program Files\Google\Chrome\Application\chrome.exe", "/usr/bin/google-chrome"]),
    ):
        if any(os.path.exists(r) for r in rutas) or shutil.which(canal):
            return canal
    return None


@skipUnless(HAY_PLAYWRIGHT, "Playwright no está instalado (pip install -r requirements-dev.txt)")
# Pruebas anteriores al IGTF: verifican conversión, vuelto y cierre sin el 3 % (el IGTF tiene sus propias pruebas).
@override_settings(IGTF_METODOS=[])
class NavegadorTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"  # Playwright síncrono usa un bucle de eventos
        super().setUpClass()
        cls.pw = sync_playwright().start()
        cls.navegador = cls.pw.chromium.launch(channel=_canal(), headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.navegador.close()
        cls.pw.stop()
        super().tearDownClass()

    def setUp(self):
        def usuario(nombre, grupo):
            u = User.objects.create_user(nombre, password=CLAVE)
            u.groups.add(Group.objects.get_or_create(name=grupo)[0])
            return u

        self.mesero = usuario("mesero1", GRUPO_MESERO)
        usuario("chef1", GRUPO_COCINA)
        cajero = usuario("cajero1", GRUPO_CAJA)
        TasaCambio.objects.create(valor=Decimal("40"), creada_por=cajero, activa=True)
        self.mesa = Mesa.objects.create(numero=1, capacidad=4)
        shawarmas = Categoria.objects.create(nombre="Shawarmas", estacion=Categoria.Estacion.PARRILLA)
        bebidas = Categoria.objects.create(nombre="Bebidas", estacion=Categoria.Estacion.BEBIDAS)
        Producto.objects.create(categoria=shawarmas, nombre="Shawarma de pollo", precio_usd=Decimal("6"))
        Producto.objects.create(categoria=bebidas, nombre="Té árabe", precio_usd=Decimal("1.50"))
        self.errores_js = []

    def pagina(self, usuario):
        contexto = self.navegador.new_context()
        page = contexto.new_page()
        page.on("pageerror", lambda e: self.errores_js.append(str(e)))
        page.goto(self.live_server_url + "/login/")
        page.fill("#id_username", usuario)
        page.fill("#id_password", CLAVE)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")
        return page

    def esperar_mensaje(self, page, texto):
        page.wait_for_selector(f".flash li:has-text('{texto}')", timeout=8000)

    def test_ciclo_completo_desde_el_navegador(self):
        sala = self.pagina("mesero1")
        sala.click("button[aria-label^='Mesa 1,']")
        sala.wait_for_url(re.compile(r"/mesas/comanda/\d+/"))

        # Tocar un plato lo agrega (el dato viaja en el propio botón).
        sala.click("button.plato:has-text('Shawarma de pollo')")
        self.esperar_mensaje(sala, "Se agregó 1 × Shawarma de pollo")
        sala.fill("#nota-plato", "Sin azúcar")
        sala.click("button.plato:has-text('Té árabe')")
        self.esperar_mensaje(sala, "Se agregó 1 × Té árabe")
        sala.click("button[aria-label='Agregar uno de Shawarma de pollo']")
        sala.wait_for_selector(".item-comanda:has-text('Shawarma de pollo') .cantidad span:text-is('2')")
        pedido = Pedido.objects.get(mesa=self.mesa)
        self.assertEqual(
            sorted(pedido.detalles.values_list("producto__nombre", "cantidad", "observaciones")),
            [("Shawarma de pollo", 2, ""), ("Té árabe", 1, "Sin azúcar")],
        )
        sala.click("button:has-text('Enviar a cocina')")
        self.esperar_mensaje(sala, "Comanda enviada a cocina")

        # Cocina: el botón lleva la acción (preparar / listo) y el filtro por estación funciona.
        cocina = self.pagina("chef1")
        cocina.click(".segmentos a:has-text('Bebidas')")
        cocina.wait_for_load_state("networkidle")
        self.assertEqual(cocina.locator(".ticket li").count(), 1)
        cocina.click(".segmentos a:has-text('Todo')")
        cocina.wait_for_load_state("networkidle")
        # Un solo botón por pedido: «Empezar» toma todos sus platos y «Todo listo» los termina.
        self.assertEqual(cocina.locator("button:has-text('Empezar')").count(), 1)
        cocina.click("button:has-text('Empezar')")
        self.esperar_mensaje(cocina, "2 platos en preparación")
        cocina.click("button.accion-kds:has-text('Todo listo')")
        self.esperar_mensaje(cocina, "listo para servir")
        self.assertFalse(DetallePedido.objects.exclude(estado=DetallePedido.Estado.LISTO).exists())

        # Salón: entregar y pedir la cuenta.
        sala.reload()
        sala.click("button:has-text('Entregar todo')")
        self.esperar_mensaje(sala, "platos entregados")
        sala.click("button:has-text('Pedir cuenta')")
        self.esperar_mensaje(sala, "por facturar")

        # Caja: emitir, la calculadora muestra el vuelto en vivo y se cobra en divisas.
        caja = self.pagina("cajero1")
        caja.click("button:has-text('Emitir factura')")
        caja.wait_for_url(re.compile(r"/caja/factura/\d+/"))
        caja.check("#metodo-DIV")
        caja.fill("#monto_recibido", "20")
        self.assertEqual(caja.inner_text("[data-vuelto]"), "Bs 260,00")  # 20 USD = 800 Bs; total 540 Bs
        caja.check("#metodo-PMO")
        caja.fill("#monto_recibido", "600")
        self.assertTrue(caja.is_visible("[data-aviso]"))  # pago móvil no admite vuelto
        self.assertTrue(caja.is_disabled("[data-cobrar]"))
        caja.check("#metodo-DIV")
        caja.fill("#monto_recibido", "20")
        caja.click("[data-cobrar]")
        caja.wait_for_selector("text=Cuenta saldada")
        self.assertIn("Bs 260,00", caja.inner_text(".vuelto"))
        self.mesa.refresh_from_db()
        self.assertEqual(self.mesa.estado, Mesa.Estado.POR_LIMPIAR)
        self.assertEqual(self.errores_js, [])
