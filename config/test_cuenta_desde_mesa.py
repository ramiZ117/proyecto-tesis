"""Cuenta desde la mesa: pedirla, pagar por pago móvil/Binance/Zinli con propina, confirmar en caja; delivery."""

import json
from decimal import Decimal

from django.test import Client

from caja.models import Factura, Pago, Propina, SolicitudCuenta
from config.base_pruebas import DatosBase
from gestion.models import AjustesLocal
from menu.mesa import codigo
from mesas.models import DetallePedido, Mesa, SolicitudCliente


class _Base(DatosBase):
    def setUp(self):
        super().setUp()
        self.publico = Client()
        AjustesLocal.objects.update_or_create(pk=1, defaults={
            "pm_banco": "Banesco (0134)", "pm_telefono": "0414-1111222", "pm_documento": "J-123456789",
            "pm_titular": "El Amir C.A.", "binance_id": "elamir@correo.com", "zinli_correo": "",
        })
        from django.core.cache import cache
        cache.clear()

    def post(self, ruta, mesa=2, **datos):
        return self.publico.post(ruta, json.dumps({"mesa": mesa, "codigo": codigo(mesa), **datos}), content_type="application/json")

    def estado(self, mesa=2, pedido=None):
        url = f"/menu/solicitud/estado/?mesa={mesa}&c={codigo(mesa)}" + (f"&pedido={pedido}" if pedido else "")
        return self.publico.get(url).json()

    def mesa_servida(self, entregar=True):
        """Mesa 2 con Bistec (Bs 400) + Agua (Bs 40) enviados a cocina; opcionalmente entregados."""
        pedido = self.pedido(mesa=self.mesa2, items=[(self.bistec, 1), (self.agua, 1)])
        if entregar:
            pedido.detalles.update(estado=DetallePedido.Estado.ENTREGADO)
        return pedido


class PagosEnDolaresDigitalesTests(_Base):
    def test_binance_es_en_dolares_con_referencia_y_sin_vuelto(self):
        f = Factura.emitir_desde_pedido(self.mesa_servida(), self.cajero)  # Bs 440 = $11
        with self.assertRaises(Exception):
            f.cobrar(metodo=Pago.Metodo.BINANCE, monto=Decimal("12"), referencia="ABC123")  # no da vuelto
        pago, f = f.cobrar(metodo=Pago.Metodo.ZINLI, monto=Decimal("11"), referencia="ZX9988")
        self.assertEqual((pago.moneda, pago.monto_local, pago.vuelto_local), ("USD", Decimal("440.00"), Decimal("0.00")))
        self.assertEqual(f.estado, Factura.Estado.PAGADA)

    def test_caja_ofrece_binance_y_zinli(self):
        f = Factura.emitir_desde_pedido(self.mesa_servida(), self.cajero)
        r = self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/")
        self.assertContains(r, 'value="BIN"')
        self.assertContains(r, 'value="ZIN"')


class AjustesTests(_Base):
    def test_metodos_del_cliente_segun_los_datos_cargados(self):
        claves = [m["clave"] for m in AjustesLocal.actual().metodos_cliente()]
        self.assertEqual(claves, ["PMO", "BIN", "EFE", "DIV", "TAR"])  # Zinli sin correo: no aparece
        ajustes = AjustesLocal.actual()
        ajustes.pm_banco = ""
        ajustes.save()
        self.assertNotIn("PMO", [m["clave"] for m in AjustesLocal.actual().metodos_cliente()])

    def test_pantalla_de_ajustes_valida(self):
        c = self.cliente(self.admin)
        base = {"pm_banco": "Banesco", "pm_telefono": "0414-1234567", "pm_documento": "j-12345678", "pm_titular": "",
                "binance_id": "", "zinli_correo": "pagos@elamir.com", "mostrar_delivery": "on"}
        self.assertEqual(c.post("/gestion/cobros-y-delivery/", base).status_code, 302)
        self.assertEqual(AjustesLocal.actual().pm_documento, "J-12345678")
        r = c.post("/gestion/cobros-y-delivery/", {**base, "pm_telefono": ""})
        self.assertContains(r, "completa banco, teléfono y cédula")
        r = c.post("/gestion/cobros-y-delivery/", {**base, "pm_documento": "123"})
        self.assertContains(r, "V-12345678")
        self.assertEqual(self.cliente(self.cajero).get("/gestion/cobros-y-delivery/").status_code, 403)

    def test_aviso_de_delivery_en_el_menu(self):
        self.assertContains(self.publico.get("/menu/"), "PedidosYa")
        ajustes = AjustesLocal.actual()
        ajustes.mostrar_delivery = False
        ajustes.save()
        self.assertNotContains(self.publico.get("/menu/"), "PedidosYa")


class CuentaDesdeLaMesaTests(_Base):
    def test_se_puede_pedir_la_cuenta_con_platos_en_cocina_y_se_avisa_cuanto_falta(self):
        from mesas.models import Pedido

        sin_enviar = Pedido.objects.create(mesa=self.mesa2, mesero=self.mesero)
        self.mesa2.ocupar()
        DetallePedido.objects.create(pedido=sin_enviar, producto=self.agua, cantidad=1, precio_unitario=Decimal("40"))
        self.assertFalse(self.estado()["cuenta"]["disponible"])  # nada en cocina todavía
        self.assertEqual(self.post("/menu/cuenta/", metodo="EFE").status_code, 409)
        sin_enviar.enviar_a_cocina()
        cuenta = self.estado()["cuenta"]
        self.assertTrue(cuenta["disponible"])
        self.assertEqual((cuenta["pendientes"], cuenta["total_bs"]), (1, "40,00"))
        self.assertIsNotNone(cuenta["pendientes_min"])
        r = self.post("/menu/cuenta/", metodo="TAR", para_llevar=True)
        self.assertEqual(r.status_code, 200)
        sin_enviar.refresh_from_db()
        self.assertIn("Lo pendiente, para llevar", sin_enviar.notas)
        self.assertContains(self.cliente(self.chef).get("/cocina/"), "Lo pendiente, para llevar")
        sin_enviar.detalles.update(estado=DetallePedido.Estado.ENTREGADO)
        self.assertEqual(self.estado()["cuenta"]["pendientes"], 0)

    def test_pago_en_caja_con_tarjeta(self):
        self.mesa_servida()
        r = self.post("/menu/cuenta/", metodo="TAR")
        self.assertEqual(r.status_code, 200)
        s = r.json()["cuenta"]["solicitud"]
        self.assertEqual((s["metodo"], s["digital"], s["datos"]), ("TAR", False, []))
        self.mesa2.refresh_from_db()
        self.assertEqual(self.mesa2.estado, Mesa.Estado.POR_FACTURAR)
        self.assertContains(self.cliente(self.cajero).get("/caja/"), "Pide la cuenta · Tarjeta")

    def test_pago_movil_con_propina_confirmado_por_caja(self):
        pedido = self.mesa_servida()
        cuenta = self.post("/menu/cuenta/", metodo="PMO").json()["cuenta"]
        self.assertIn({"etiqueta": "Teléfono", "valor": "0414-1111222"}, cuenta["solicitud"]["datos"])
        self.assertEqual(cuenta["solicitud"]["monto"], "440.00")
        self.assertEqual(self.post("/menu/cuenta/reportar/", referencia="12", propina="0").status_code, 400)
        self.assertEqual(self.post("/menu/cuenta/reportar/", referencia="00451234", propina="9999").status_code, 400)
        r = self.post("/menu/cuenta/reportar/", referencia="00451234", origen="Mercantil", propina="44")
        self.assertEqual(r.json()["cuenta"]["solicitud"]["estado"], "REP")

        caja = self.cliente(self.cajero)
        self.assertContains(caja.get("/caja/"), "reportado · por confirmar")
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        r = caja.get(f"/caja/factura/{f.pk}/")
        self.assertContains(r, "El cliente reportó un pago")
        self.assertContains(r, "00451234")
        caja.post(f"/caja/factura/{f.pk}/pago-cliente/", {"accion": "confirmar"})
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.PAGADA)
        pago = f.pagos.get()
        self.assertEqual((pago.metodo, pago.referencia, pago.monto_local), ("PMO", "00451234", Decimal("440.00")))
        propina = Propina.objects.get()
        self.assertEqual((propina.mesero, propina.monto, propina.pagada), (self.mesero, Decimal("44.00"), False))
        self.assertEqual(SolicitudCuenta.objects.get().estado, SolicitudCuenta.Estado.CONFIRMADA)
        # el teléfono se entera de que ya pagó
        self.assertTrue(self.estado(pedido=pedido.pk)["cuenta"]["pagada"])

    def test_binance_en_dolares_y_rechazo_y_nuevo_reporte(self):
        pedido = self.mesa_servida()
        s = self.post("/menu/cuenta/", metodo="BIN").json()["cuenta"]["solicitud"]
        self.assertEqual((s["en_usd"], s["monto"]), (True, "11.00"))
        self.post("/menu/cuenta/reportar/", referencia="BNB-777", propina="1")
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        caja = self.cliente(self.cajero)
        caja.post(f"/caja/factura/{f.pk}/pago-cliente/", {"accion": "rechazar"})
        self.assertEqual(self.estado()["cuenta"]["solicitud"]["estado"], "REC")
        self.post("/menu/cuenta/reportar/", referencia="BNB-778", propina="1")
        caja.post(f"/caja/factura/{f.pk}/pago-cliente/", {"accion": "confirmar"})
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.PAGADA)
        self.assertEqual(f.pagos.get().moneda, "USD")
        self.assertEqual(Propina.objects.get().monto_local, Decimal("40.00"))

    def test_metodo_sin_datos_o_inventado_no_se_acepta(self):
        self.mesa_servida()
        self.assertEqual(self.post("/menu/cuenta/", metodo="ZIN").status_code, 400)
        self.assertEqual(self.post("/menu/cuenta/", metodo="XXX").status_code, 400)
        self.assertEqual(self.publico.post("/menu/cuenta/", json.dumps({"mesa": 2, "codigo": "malo", "metodo": "EFE"}),
                                           content_type="application/json").status_code, 403)

    def test_cambiar_de_metodo_antes_de_reportar(self):
        self.mesa_servida()
        self.post("/menu/cuenta/", metodo="EFE")
        self.post("/menu/cuenta/", metodo="PMO")
        self.assertEqual(SolicitudCuenta.objects.get().metodo, "PMO")
        self.post("/menu/cuenta/reportar/", referencia="99887766")
        self.assertEqual(self.post("/menu/cuenta/", metodo="EFE").status_code, 409)

    def test_platos_sin_enviar_piden_llamar_al_mesero(self):
        pedido = self.mesa_servida()
        DetallePedido.objects.create(pedido=pedido, producto=self.agua, cantidad=1, precio_unitario=Decimal("40"))
        r = self.post("/menu/cuenta/", metodo="EFE")
        self.assertEqual(r.status_code, 409)
        self.assertIn("Llámalo", r.json()["error"])

    def test_el_mesero_ve_que_la_mesa_pidio_la_cuenta(self):
        pedido = self.mesa_servida()
        self.post("/menu/cuenta/", metodo="PMO")
        r = self.cliente(self.mesero).get(f"/mesas/comanda/{pedido.pk}/")
        self.assertContains(r, "La mesa pidió la cuenta desde el menú")


class LlamarConMesaAtendidaTests(_Base):
    def test_la_mesa_atendida_puede_volver_a_llamar(self):
        pedido = self.mesa_servida()
        self.post("/menu/solicitar/", items=[{"tipo": "producto", "id": self.agua.pk, "cantidad": 1}])
        primera = SolicitudCliente.objects.get()
        primera.cargar_en(pedido, self.mesero)
        r = self.post("/menu/solicitar/", items=[], nota="Quiere pedir algo más")
        self.assertEqual(r.status_code, 200)
        nueva = SolicitudCliente.objects.get(estado=SolicitudCliente.Estado.PENDIENTE)
        self.assertEqual(nueva.nota, "Quiere pedir algo más")
        comanda = self.cliente(self.mesero).get(f"/mesas/comanda/{pedido.pk}/")
        self.assertContains(comanda, "Quiere pedir algo más")
        self.assertContains(self.cliente(self.mesero).get("/mesas/parcial/"), "Llama al mesero")
