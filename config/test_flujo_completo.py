"""Recorrido de extremo a extremo usando solo las pantallas (peticiones HTTP), como lo haría el personal."""
from decimal import Decimal

from caja.models import CierreCaja, Factura, TasaCambio
from config.base_pruebas import DatosBase
from inventario.models import Insumo
from mesas.models import Mesa, Pedido


class FlujoCompletoTests(DatosBase):
    def test_mesero_cocina_y_caja_atienden_una_mesa_de_principio_a_fin(self):
        mesero, chef, caja = self.cliente(self.mesero), self.cliente(self.chef), self.cliente(self.cajero)

        # Mesero: abre mesa, pide, agrega observación y envía.
        mesero.post(f"/mesas/abrir/{self.mesa1.pk}/")
        pedido = Pedido.objects.get(mesa=self.mesa1)
        mesero.post(
            f"/mesas/comanda/{pedido.pk}/agregar/",
            {"producto_id": self.bistec.pk, "cantidad": 2, "observaciones": "Término medio"},
        )
        mesero.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.agua.pk, "cantidad": 1})
        self.assertEqual(self.cliente(self.chef).get("/cocina/parcial/").context["pedidos"], [])
        mesero.post(f"/mesas/comanda/{pedido.pk}/enviar/")

        # Cocina: ve el ticket con la observación y prepara el bistec.
        r = chef.get("/cocina/")
        self.assertContains(r, "Término medio")
        bistec = pedido.detalles.get(producto=self.bistec)
        agua = pedido.detalles.get(producto=self.agua)
        chef.post(f"/cocina/item/{bistec.pk}/", {"accion": "preparar"})
        chef.post(f"/cocina/item/{bistec.pk}/", {"accion": "listo"})
        chef.post(f"/cocina/item/{agua.pk}/", {"accion": "preparar"})
        chef.post(f"/cocina/item/{agua.pk}/", {"accion": "listo"})
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("10.000"))  # el inventario baja al cobrar

        # Mesero: entrega y pide la cuenta.
        mesero.post(f"/mesas/item/{bistec.pk}/entregar/")
        mesero.post(f"/mesas/item/{agua.pk}/entregar/")
        mesero.post(f"/mesas/comanda/{pedido.pk}/cuenta/")
        self.mesa1.refresh_from_db()
        self.assertEqual(self.mesa1.estado, Mesa.Estado.POR_FACTURAR)

        # Cajero: factura y cobra dividido (divisa + pago móvil).
        caja.post(f"/caja/pedido/{pedido.pk}/facturar/")
        factura = Factura.objects.get()
        self.assertEqual(factura.total_local, Decimal("840.00"))
        caja.post(f"/caja/factura/{factura.pk}/pago/", {"metodo": "DIV", "monto_recibido": "10", "pagador": "Ana"})
        caja.post(
            f"/caja/factura/{factura.pk}/pago/",
            {"metodo": "PMO", "monto_recibido": "440", "referencia": "0042", "pagador": "Luis"},
        )
        factura.refresh_from_db()
        self.mesa1.refresh_from_db()
        self.assertEqual(factura.estado, Factura.Estado.PAGADA)
        self.assertEqual(self.mesa1.estado, Mesa.Estado.POR_LIMPIAR)
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("9.000"))  # 2 bistec × 0.5 kg

        # Mesero limpia la mesa; el cajero cierra la caja.
        mesero.post(f"/mesas/limpiar/{self.mesa1.pk}/")
        self.mesa1.refresh_from_db()
        self.assertEqual(self.mesa1.estado, Mesa.Estado.DISPONIBLE)
        caja.post("/caja/cierre/generar/")
        cierre = CierreCaja.objects.get()
        self.assertEqual(cierre.total_local, Decimal("840.00"))
        self.assertEqual(cierre.cantidad_facturas, 1)

    def test_sin_tasa_vigente_no_se_pueden_agregar_platillos(self):
        TasaCambio.objects.update(activa=False)
        mesero = self.cliente(self.mesero)
        mesero.post(f"/mesas/abrir/{self.mesa1.pk}/")
        pedido = Pedido.objects.get(mesa=self.mesa1)
        r = mesero.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.bistec.pk, "cantidad": 1}, follow=True)
        self.assertContains(r, "No hay tasa de cambio")
        self.assertFalse(pedido.detalles.exists())

    def test_observaciones_largas_se_recortan_en_vez_de_fallar(self):
        mesero = self.cliente(self.mesero)
        mesero.post(f"/mesas/abrir/{self.mesa1.pk}/")
        pedido = Pedido.objects.get(mesa=self.mesa1)
        mesero.post(
            f"/mesas/comanda/{pedido.pk}/agregar/",
            {"producto_id": self.bistec.pk, "cantidad": 1, "observaciones": "x" * 900},
        )
        self.assertEqual(len(pedido.detalles.get().observaciones), 250)

    def test_montos_invalidos_no_rompen_las_pantallas_de_caja(self):
        caja = self.cliente(self.cajero)
        pedido = self.pedido(items=[(self.bistec, 1)])
        factura = Factura.emitir_desde_pedido(pedido, self.cajero)
        for basura in ("Infinity", "NaN", "1e999", "99999999999", "0", "-1", "", "abc"):
            r = caja.post(f"/caja/factura/{factura.pk}/pago/", {"metodo": "EFE", "monto_recibido": basura})
            self.assertEqual(r.status_code, 302, basura)
            r = caja.post("/caja/tasa/guardar/", {"valor": basura})
            self.assertEqual(r.status_code, 302, basura)
        self.assertFalse(factura.pagos.exists())
        self.assertEqual(TasaCambio.objects.filter(activa=True).get().valor, Decimal("40.0000"))
        caja.post("/caja/tasa/guardar/", {"valor": "41,5"})
        self.assertEqual(TasaCambio.objects.get(activa=True).valor, Decimal("41.5000"))

    def test_reposicion_con_valores_invalidos(self):
        caja = self.cliente(self.cajero)
        for basura in ("Infinity", "NaN", "1e999", "99999999", "abc"):
            r = caja.post("/inventario/entrada/", {"insumo_id": self.pan.pk, "cantidad": basura})
            self.assertEqual(r.status_code, 302, basura)
        self.assertEqual(Insumo.objects.get(pk=self.pan.pk).stock_actual, Decimal("5.000"))
