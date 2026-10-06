from django.core.exceptions import ValidationError

from cocina.models import PedidoKDS
from config.base_pruebas import DatosBase
from mesas.models import DetallePedido, Mesa, Pedido


class MapaDeMesasTests(DatosBase):
    """RF-02: mapa de mesas con su estatus."""

    def test_mapa_muestra_los_cuatro_estados(self):
        self.mesa2.ocupar()
        self.mesa3.marcar_por_facturar()
        Mesa.objects.filter(pk=self.mesa1.pk).update(estado=Mesa.Estado.POR_LIMPIAR)
        r = self.cliente(self.mesero).get("/mesas/")
        for texto in ("Por limpiar", "Ocupada", "Por facturar"):
            self.assertContains(r, texto)
        self.assertEqual(Mesa.Estado.values, ["DIS", "OCU", "FAC", "LIM"])

    def test_mapa_usa_la_posicion_de_cada_mesa(self):
        r = self.cliente(self.mesero).get("/mesas/")
        self.assertContains(r, "grid-column: 2; grid-row: 1")

    def test_mapa_parcial_para_refresco_automatico(self):
        r = self.cliente(self.mesero).get("/mesas/parcial/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Mesa 1")

    def test_abrir_mesa_crea_pedido_y_la_ocupa(self):
        c = self.cliente(self.mesero)
        r = c.post(f"/mesas/abrir/{self.mesa1.pk}/")
        self.assertEqual(r.status_code, 302)
        self.mesa1.refresh_from_db()
        self.assertEqual(self.mesa1.estado, Mesa.Estado.OCUPADA)
        self.assertEqual(Pedido.objects.get(mesa=self.mesa1).mesero, self.mesero)

    def test_mesa_por_limpiar_no_se_puede_ocupar(self):
        self.mesa1.marcar_por_limpiar()
        self.cliente(self.mesero).post(f"/mesas/abrir/{self.mesa1.pk}/")
        self.assertFalse(Pedido.objects.filter(mesa=self.mesa1).exists())

    def test_limpiar_libera_la_mesa(self):
        self.mesa1.marcar_por_limpiar()
        self.cliente(self.mesero).post(f"/mesas/limpiar/{self.mesa1.pk}/")
        self.mesa1.refresh_from_db()
        self.assertEqual(self.mesa1.estado, Mesa.Estado.DISPONIBLE)


class ComandaTests(DatosBase):
    """RF-02: registrar pedidos con ítems del menú, observaciones y envío inmediato."""

    def abrir(self, mesa=None):
        c = self.cliente(self.mesero)
        c.post(f"/mesas/abrir/{(mesa or self.mesa1).pk}/")
        return c, Pedido.objects.get(mesa=mesa or self.mesa1)

    def test_agregar_item_con_observaciones_y_precio_congelado(self):
        c, pedido = self.abrir()
        c.post(
            f"/mesas/comanda/{pedido.pk}/agregar/",
            {"producto_id": self.bistec.pk, "cantidad": 2, "observaciones": "Término medio"},
        )
        d = pedido.detalles.get()
        self.assertEqual((d.cantidad, d.observaciones), (2, "Término medio"))
        self.assertEqual(d.precio_unitario, 400)  # 10 USD * 40
        self.assertFalse(d.enviado)
        self.assertEqual(pedido.subtotal, 800)

    def test_enviar_a_cocina_marca_los_platillos_y_el_pedido(self):
        c, pedido = self.abrir()
        c.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.bistec.pk, "cantidad": 1})
        c.post(f"/mesas/comanda/{pedido.pk}/enviar/")
        pedido.refresh_from_db()
        d = pedido.detalles.get()
        self.assertTrue(d.enviado)
        self.assertIsNotNone(d.enviado_en)
        self.assertEqual(pedido.estado, Pedido.Estado.ENVIADO_COCINA)

    def test_no_se_envia_un_pedido_vacio(self):
        c, pedido = self.abrir()
        c.post(f"/mesas/comanda/{pedido.pk}/enviar/")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ABIERTO)

    def test_platillo_agregado_despues_del_envio_no_llega_a_cocina_hasta_enviarlo(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        nuevo = DetallePedido.objects.create(
            pedido=pedido, producto=self.agua, cantidad=1, precio_unitario=self.agua.precio_local
        )
        visibles = [i.pk for i in PedidoKDS.objects.en_cola()[0].items_cocina]
        self.assertNotIn(nuevo.pk, visibles)
        with self.assertRaises(ValidationError):
            nuevo.marcar_en_preparacion()
        pedido.enviar_a_cocina()
        visibles = [i.pk for i in PedidoKDS.objects.en_cola()[0].items_cocina]
        self.assertIn(nuevo.pk, visibles)

    def test_quitar_item_sin_enviar_lo_borra_y_enviado_pendiente_lo_anula(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        enviado = pedido.detalles.get()
        nuevo = DetallePedido.objects.create(
            pedido=pedido, producto=self.agua, cantidad=1, precio_unitario=self.agua.precio_local
        )
        c = self.cliente(self.mesero)
        c.post(f"/mesas/item/{nuevo.pk}/quitar/")
        self.assertFalse(DetallePedido.objects.filter(pk=nuevo.pk).exists())
        c.post(f"/mesas/item/{enviado.pk}/quitar/")
        enviado.refresh_from_db()
        self.assertEqual(enviado.estado, DetallePedido.Estado.ANULADO)

    def test_no_se_quita_un_platillo_en_preparacion(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        d = pedido.detalles.get()
        d.marcar_en_preparacion()
        self.cliente(self.mesero).post(f"/mesas/item/{d.pk}/quitar/")
        d.refresh_from_db()
        self.assertEqual(d.estado, DetallePedido.Estado.EN_PREPARACION)

    def test_solicitar_cuenta_pasa_la_mesa_a_por_facturar(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/cuenta/")
        self.mesa1.refresh_from_db()
        self.assertEqual(self.mesa1.estado, Mesa.Estado.POR_FACTURAR)

    def test_no_se_pide_la_cuenta_con_platillos_sin_enviar(self):
        pedido = self.pedido(items=[(self.bistec, 1)], enviar=False)
        self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/cuenta/")
        self.mesa1.refresh_from_db()
        self.assertEqual(self.mesa1.estado, Mesa.Estado.OCUPADA)

    def test_anular_pedido_libera_la_mesa(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/anular/")
        pedido.refresh_from_db()
        self.mesa1.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ANULADO)
        self.assertEqual(self.mesa1.estado, Mesa.Estado.DISPONIBLE)
        self.assertFalse(pedido.detalles.exclude(estado=DetallePedido.Estado.ANULADO).exists())

    def test_mesero_no_anula_si_ya_hay_platillos_en_marcha_pero_admin_si(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        pedido.detalles.get().marcar_en_preparacion()
        self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/anular/")
        pedido.refresh_from_db()
        self.assertNotEqual(pedido.estado, Pedido.Estado.ANULADO)
        self.cliente(self.admin).post(f"/mesas/comanda/{pedido.pk}/anular/")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ANULADO)

    def test_mesero_no_modifica_pedidos_de_otro_mesero(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        otro = self.cliente(self.mesero2)
        otro.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.agua.pk, "cantidad": 1})
        otro.post(f"/mesas/comanda/{pedido.pk}/anular/")
        pedido.refresh_from_db()
        self.assertEqual(pedido.detalles.count(), 1)
        self.assertNotEqual(pedido.estado, Pedido.Estado.ANULADO)
        self.assertEqual(otro.get(f"/mesas/comanda/{pedido.pk}/").status_code, 302)

    def test_platillo_agotado_no_se_puede_pedir(self):
        self.res.stock_actual = 0
        self.res.save()
        c, pedido = self.abrir()
        c.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.bistec.pk, "cantidad": 1})
        self.assertFalse(pedido.detalles.exists())
        self.assertContains(c.get(f"/mesas/comanda/{pedido.pk}/"), "Agotado")

    def test_no_se_modifica_un_pedido_ya_facturado(self):
        from caja.models import Factura

        pedido = self.pedido(items=[(self.bistec, 1)])
        Factura.emitir_desde_pedido(pedido, self.cajero)
        c = self.cliente(self.mesero)
        c.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.agua.pk, "cantidad": 1})
        self.assertEqual(pedido.detalles.count(), 1)

    def test_marcar_una_opcion_no_recarga_toda_la_pagina(self):
        c = self.cliente(self.mesero)
        c.post(f"/mesas/abrir/{self.mesa1.pk}/")
        pedido = Pedido.objects.get(mesa=self.mesa1)
        r = c.post(
            f"/mesas/comanda/{pedido.pk}/agregar/",
            {"producto_id": self.agua.pk, "cantidad": 1},
            HTTP_X_PARTIAL="1",
        )
        self.assertEqual(r.status_code, 204)
        self.assertFalse(r.has_header("Location"))
        self.assertIn(f"/mesas/comanda/{pedido.pk}/", r["X-Next"])
        pagina = c.get(r["X-Next"], HTTP_X_PARTIAL="1")
        self.assertContains(pagina, 'id="pagina-parcial"')
        self.assertNotContains(pagina, "<!DOCTYPE")
        self.assertContains(pagina, "Agua")
        self.assertEqual(pedido.detalles.count(), 1)
