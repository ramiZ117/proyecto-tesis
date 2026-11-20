"""Factura sin «saldo 0», errores claros en el menú, cadena de IAs, cantidades escritas, admin y caja para llevar."""

import json
import smtplib
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, override_settings

from caja.correo import motivo_error_correo
from caja.models import Factura, Pago
from config.base_pruebas import DatosBase
from menu import asistente
from menu.mesa import codigo
from mesas.models import DetallePedido, Mesa, Pedido


class FacturaPagadaTests(DatosBase):
    def test_la_factura_pagada_muestra_total_y_forma_de_pago_sin_saldo_cero(self):
        f = Factura.emitir_desde_pedido(self.pedido(items=[(self.bistec, 1)]), self.cajero)
        f.cobrar(metodo=Pago.Metodo.PAGO_MOVIL, monto=Decimal("400"), referencia="1234")
        r = self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/")
        self.assertContains(r, "PAGO MOVIL")
        self.assertContains(r, "Ref. 1234")
        self.assertContains(r, "TOTAL")
        self.assertNotContains(r, "− Bs 400")  # el pago no se resta del total
        self.assertNotContains(r, "Falta por cobrar")
        self.assertNotContains(r, '<span class="fuerte">Saldo</span>')

    def test_la_factura_abierta_con_abono_muestra_lo_que_falta(self):
        f = Factura.emitir_desde_pedido(self.pedido(items=[(self.bistec, 1)]), self.cajero)
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("100"))
        r = self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/")
        self.assertContains(r, "Falta por cobrar")
        self.assertContains(r, "Bs 300,00")


class ErroresClarosTests(DatosBase):
    def test_sin_cookie_de_seguridad_el_menu_recibe_un_motivo_y_no_sin_conexion(self):
        cliente = Client(enforce_csrf_checks=True)
        r = cliente.post("/menu/solicitar/", json.dumps({"mesa": 2, "codigo": codigo(2), "items": []}),
                         content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertIn("Recarga la página", r.json()["error"])

    def test_un_error_interno_del_menu_responde_json(self):
        cache.clear()
        with mock.patch("menu.views.SolicitudCliente.objects.create", side_effect=RuntimeError("falla de prueba")):
            r = Client(raise_request_exception=False).post(
                "/menu/solicitar/", json.dumps({"mesa": 2, "codigo": codigo(2), "items": []}), content_type="application/json")
        self.assertEqual(r.status_code, 500)
        self.assertIn("Avisa al personal", r.json()["error"])

    def test_motivos_del_correo(self):
        resend = smtplib.SMTPDataError(450, b"You can only send testing emails to your own email address")
        self.assertIn("modo de prueba", motivo_error_correo(resend))
        self.assertIn("usuario o la clave", motivo_error_correo(smtplib.SMTPAuthenticationError(535, b"bad")))
        self.assertIn("conexión", motivo_error_correo(TimeoutError("timed out")))

    def test_diagnostico_rapido(self):
        salida = StringIO()
        call_command("diagnostico", "--rapido", stdout=salida)
        texto = salida.getvalue()
        for parte in ("Migraciones al día", "Asistente del menú", "Correo de las facturas", "Todo en orden"):
            self.assertIn(parte, texto)


@override_settings(IA_PROVEEDOR="gemini", GEMINI_API_KEY="g", GEMINI_MODELO="m1", GROQ_API_KEY="q", GROQ_MODELO="llama",
                   XAI_API_KEY="")
class CadenaDeIasTests(DatosBase):
    def preguntar(self, texto):
        return Client().post("/menu/asistente/", json.dumps({"mensaje": texto}), content_type="application/json").json()

    def test_si_gemini_falla_responde_groq(self):
        cache.clear()
        groq_ok = {"choices": [{"message": {"content": "Te recomiendo el Bistec."}}]}
        with mock.patch.object(asistente, "_post_json", side_effect=[TimeoutError("sin red"), groq_ok]):
            datos = self.preguntar("¿Qué me recomiendas?")
        self.assertEqual((datos["origen"], datos["respuesta"]), ("groq", "Te recomiendo el Bistec."))

    def test_si_todas_fallan_responde_el_asistente_local(self):
        cache.clear()
        with mock.patch.object(asistente, "_post_json", side_effect=ValueError("respuesta rara")):
            datos = self.preguntar("¿Qué me recomiendas?")
        self.assertEqual(datos["origen"], "local")
        self.assertTrue(datos["respuesta"])


class CantidadEscritaTests(DatosBase):
    def test_escribir_la_cantidad_de_un_plato(self):
        pedido = self.pedido(items=[(self.agua, 1)], enviar=False)
        linea = pedido.detalles.get()
        mesero = self.cliente(self.mesero)
        self.assertContains(mesero.get(f"/mesas/comanda/{pedido.pk}/"), 'inputmode="numeric"')
        mesero.post(f"/mesas/item/{linea.pk}/cantidad/", {"cantidad": "25"})
        linea.refresh_from_db()
        self.assertEqual(linea.cantidad, 25)
        for malo in ("0", "100", "abc", "-3"):
            mesero.post(f"/mesas/item/{linea.pk}/cantidad/", {"cantidad": malo})
            linea.refresh_from_db()
            self.assertEqual(linea.cantidad, 25, malo)
        r = mesero.post(f"/mesas/item/{linea.pk}/cantidad/", {"cantidad": "4"}, follow=True)
        linea.refresh_from_db()
        self.assertEqual(linea.cantidad, 4)

    def test_no_permite_mas_de_lo_que_alcanza_el_inventario(self):
        pedido = self.pedido(items=[(self.hamburguesa, 1)], enviar=False)  # pan: 5 unidades
        linea = pedido.detalles.get()
        r = self.cliente(self.mesero).post(f"/mesas/item/{linea.pk}/cantidad/", {"cantidad": "20"}, follow=True)
        self.assertContains(r, "porciones")
        linea.refresh_from_db()
        self.assertEqual(linea.cantidad, 1)

    def test_agregar_varios_de_una_vez_desde_la_carta(self):
        pedido = self.pedido(items=[], enviar=False)
        self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.agua.pk, "cantidad": "12"})
        self.assertEqual(pedido.detalles.get().cantidad, 12)


class AdminAgregaPlatosTests(DatosBase):
    def test_el_admin_abre_una_mesa_libre_solo_si_lo_pide(self):
        admin = self.cliente(self.admin)
        r = admin.post(f"/mesas/abrir/{self.mesa3.pk}/", follow=True)
        self.assertContains(r, "Abrir comanda como administrador")
        self.assertFalse(Pedido.objects.filter(mesa=self.mesa3).exists())
        admin.post(f"/mesas/abrir/{self.mesa3.pk}/", {"como_admin": "1"})
        pedido = Pedido.objects.get(mesa=self.mesa3)
        admin.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.agua.pk, "cantidad": "2"})
        self.assertEqual(pedido.detalles.get().cantidad, 2)
        self.mesa3.refresh_from_db()
        self.assertEqual(self.mesa3.estado, Mesa.Estado.OCUPADA)

    def test_el_admin_agrega_platos_a_la_mesa_de_un_mesero(self):
        pedido = self.pedido(mesa=self.mesa2, items=[(self.agua, 1)])
        self.cliente(self.admin).post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.bistec.pk, "cantidad": "1"})
        self.assertTrue(pedido.detalles.filter(producto=self.bistec).exists())
        pedido.refresh_from_db()
        self.assertEqual(pedido.mesero, self.mesero)


class CajaParaLlevarTests(DatosBase):
    def test_la_caja_atiende_un_pedido_para_llevar_de_principio_a_fin(self):
        caja = self.cliente(self.cajero)
        self.assertContains(caja.get("/caja/para-llevar/"), "Nuevo pedido")
        r = caja.post("/mesas/llevar/nuevo/", {"cliente": "Luis"}, follow=True)
        pedido = Pedido.objects.get(tipo=Pedido.Tipo.LLEVAR)
        self.assertContains(r, "Pedidos para llevar")  # la comanda se ve en la pantalla de caja, no en el salón
        caja.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.bistec.pk, "cantidad": "1"})
        caja.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.agua.pk, "cantidad": "2"})
        # Para llevar se cobra primero: «Enviar a cocina» no está y, si se fuerza, se rechaza.
        comanda = caja.get(f"/mesas/comanda/{pedido.pk}/")
        self.assertContains(comanda, "Cobrar y enviar a cocina")
        self.assertNotContains(comanda, "Enviar a cocina (")
        r = caja.post(f"/mesas/comanda/{pedido.pk}/enviar/", follow=True)
        self.assertContains(r, "se cobran primero")
        self.assertNotContains(self.cliente(self.chef).get("/cocina/"), "Luis")
        self.assertContains(caja.get("/caja/"), "Por cobrar")
        r = caja.post(f"/caja/pedido/{pedido.pk}/facturar/", follow=True)
        factura = pedido.facturas.get()
        caja.post(f"/caja/factura/{factura.pk}/pago/", {"metodo": "EFE", "monto_recibido": str(factura.total_local)})
        factura.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(factura.estado, Factura.Estado.PAGADA)
        self.assertNotEqual(pedido.estado, Pedido.Estado.CERRADO)
        self.assertFalse(pedido.sin_enviar.exists())  # pagado: se envió solo a cocina
        self.assertContains(self.cliente(self.chef).get("/cocina/"), "Luis")
        self.assertNotContains(caja.get("/caja/"), "Luis")  # ya no es una cuenta por cobrar
        self.assertContains(caja.get("/caja/para-llevar/"), "Pagado")
        # cocina lo termina y la caja lo entrega: se cierra
        self.cliente(self.chef).post(f"/cocina/pedido/{pedido.pk}/", {"accion": "empezar"})
        self.cliente(self.chef).post(f"/cocina/pedido/{pedido.pk}/", {"accion": "listo"})
        caja.post(f"/mesas/comanda/{pedido.pk}/entregar-todo/")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.CERRADO)
        self.assertNotContains(self.cliente(self.chef).get("/cocina/"), "Luis")

    def test_la_caja_edita_las_mesas_sin_quitar_al_mesero(self):
        pedido = self.pedido(mesa=self.mesa2, items=[(self.agua, 1)])
        caja = self.cliente(self.cajero)
        caja.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.bistec.pk, "cantidad": "1"})
        self.assertTrue(pedido.detalles.filter(producto=self.bistec).exists())
        pedido.refresh_from_db()
        self.assertEqual(pedido.mesero, self.mesero)
        self.assertEqual(caja.get("/mesas/").status_code, 403)  # el mapa del salón sigue siendo del mesero

    def test_el_mesero_sigue_creando_pedidos_para_llevar(self):
        self.cliente(self.mesero).post("/mesas/llevar/nuevo/", {"cliente": "Ana"})
        self.assertTrue(Pedido.objects.filter(cliente="Ana", mesero=self.mesero).exists())
