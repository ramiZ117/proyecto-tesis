from decimal import Decimal

from caja.models import Factura, TasaCambio
from config.base_pruebas import DatosBase
from inventario.models import Insumo, MovimientoStock
from inventario.servicios import disponibilidad_menu


class DescuentoAlCobrarTests(DatosBase):
    """RF-04: al procesar la venta (cuenta cobrada) se descuenta la receta de cada plato."""

    def cobrar_todo(self, pedido):
        factura = Factura.emitir_desde_pedido(pedido, self.cajero)
        factura.cobrar(metodo="EFE", monto=factura.total_local)
        return factura

    def test_preparar_en_cocina_no_descuenta_todavia(self):
        d = self.pedido(items=[(self.bistec, 2)]).detalles.get()
        self.cliente(self.chef).post(f"/cocina/item/{d.pk}/", {"accion": "preparar"})
        self.cliente(self.chef).post(f"/cocina/item/{d.pk}/", {"accion": "listo"})
        self.recargar(self.res, d)
        self.assertEqual(self.res.stock_actual, Decimal("10.000"))
        self.assertFalse(d.insumos_descontados)
        self.assertFalse(MovimientoStock.objects.exists())

    def test_cobrar_descuenta_la_receta_por_la_cantidad_pedida(self):
        pedido = self.pedido(items=[(self.hamburguesa, 3)])
        self.cobrar_todo(pedido)
        self.recargar(self.res, self.pan)
        self.assertEqual(self.res.stock_actual, Decimal("9.400"))  # 10 - 0.2*3
        self.assertEqual(self.pan.stock_actual, Decimal("2.000"))  # 5 - 1*3
        self.assertTrue(pedido.detalles.get().insumos_descontados)

    def test_un_pago_parcial_no_descuenta_hasta_saldar(self):
        pedido = self.pedido(items=[(self.bistec, 2)])
        factura = Factura.emitir_desde_pedido(pedido, self.cajero)
        factura.cobrar(metodo="EFE", monto=Decimal("300"))
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("10.000"))
        factura.cobrar(metodo="EFE", monto=Decimal("500"))
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("9.000"))

    def test_cada_descuento_deja_un_movimiento_auditable(self):
        pedido = self.pedido(items=[(self.hamburguesa, 1)])
        self.cobrar_todo(pedido)
        movimientos = MovimientoStock.objects.filter(detalle_pedido=pedido.detalles.get(), tipo="SAL")
        self.assertEqual(movimientos.count(), 2)
        mov = movimientos.get(insumo=self.pan)
        self.assertEqual(mov.stock_resultante, Decimal("4.000"))
        self.assertIn(f"pedido #{pedido.pk}", mov.nota)

    def test_no_se_descuenta_dos_veces(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        self.cobrar_todo(pedido)
        pedido.descontar_inventario()
        pedido.detalles.get()._descontar_insumos()
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("9.500"))

    def test_platillos_anulados_y_productos_sin_receta_no_descuentan(self):
        pedido = self.pedido(items=[(self.bistec, 1), (self.agua, 2)])
        self.cliente(self.mesero).post(f"/mesas/item/{pedido.detalles.get(producto=self.bistec).pk}/quitar/")
        self.cobrar_todo(pedido)
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("10.000"))
        self.assertFalse(MovimientoStock.objects.exists())

    def test_stock_insuficiente_no_bloquea_el_cobro_y_deja_constancia(self):
        pedido = self.pedido(items=[(self.bistec, 2)])  # necesita 1 kg
        Insumo.objects.filter(pk=self.res.pk).update(stock_actual=Decimal("0.400"))
        factura = self.cobrar_todo(pedido)
        factura.refresh_from_db()
        self.res.refresh_from_db()
        self.assertEqual(factura.estado, Factura.Estado.PAGADA)
        self.assertEqual(self.res.stock_actual, Decimal("0.000"))
        mov = MovimientoStock.objects.get()
        self.assertEqual(mov.cantidad, Decimal("0.400"))
        self.assertIn("faltante 0.600", mov.nota)

    def test_pagar_por_la_pantalla_de_caja_descuenta(self):
        pedido = self.pedido(items=[(self.bistec, 2)])
        factura = Factura.emitir_desde_pedido(pedido, self.cajero)
        self.cliente(self.cajero).post(
            f"/caja/factura/{factura.pk}/pago/", {"metodo": "EFE", "monto_recibido": "800"}
        )
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("9.000"))

    def test_anular_factura_no_descuenta_y_refacturar_si_al_cobrar(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        Factura.emitir_desde_pedido(pedido, self.cajero).anular("Error")
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("10.000"))
        self.cobrar_todo(pedido)
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("9.500"))

    def test_anular_pedido_con_platillo_preparado_registra_merma(self):
        pedido = self.pedido(items=[(self.bistec, 2), (self.hamburguesa, 1)])
        preparado = pedido.detalles.get(producto=self.bistec)
        preparado.marcar_en_preparacion()
        pedido.anular(forzar=True)
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("9.000"))  # solo el bistec preparado
        self.assertIn("Merma", MovimientoStock.objects.get().nota)

    def test_anular_pedido_sin_preparar_no_descuenta(self):
        pedido = self.pedido(items=[(self.bistec, 2)])
        pedido.anular()
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("10.000"))
        self.assertEqual(disponibilidad_menu()[self.bistec.pk], 20)

    def test_lo_pedido_sin_cobrar_sigue_reservado_hasta_cobrar(self):
        pedido = self.pedido(items=[(self.bistec, 4)])  # reserva 2 kg
        self.assertEqual(disponibilidad_menu()[self.bistec.pk], 16)
        pedido.detalles.get().marcar_en_preparacion()
        self.assertEqual(disponibilidad_menu()[self.bistec.pk], 16)
        self.cobrar_todo(pedido)
        self.assertEqual(disponibilidad_menu()[self.bistec.pk], 16)  # ahora vía stock real (8 kg)


class AlertasDeStockTests(DatosBase):
    """RF-04: alertas visuales ante niveles críticos de materia prima."""

    def test_sin_stock_critico_no_hay_alerta(self):
        r = self.cliente(self.cajero).get("/caja/")
        self.assertNotContains(r, "Stock crítico")

    def test_insumo_en_nivel_critico_alerta_a_caja_y_cocina(self):
        Insumo.objects.filter(pk=self.pan.pk).update(stock_actual=Decimal("1"))
        for usuario, url in ((self.cajero, "/caja/"), (self.chef, "/cocina/"), (self.admin, "/inventario/")):
            r = self.cliente(usuario).get(url)
            self.assertContains(r, "Stock crítico", msg_prefix=usuario.username)
            self.assertContains(r, "Pan", msg_prefix=usuario.username)

    def test_lo_pedido_sin_cobrar_ya_cuenta_para_la_alerta(self):
        self.pedido(items=[(self.bistec, 17)])  # reserva 8.5 kg de 10: quedan 1.5 (mínimo 2)
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("10.000"))
        r = self.cliente(self.cajero).get("/inventario/")
        self.assertContains(r, "Stock crítico")
        res = next(i for i in r.context["insumos"] if i.pk == self.res.pk)
        self.assertEqual((res.comprometido, res.disponible, res.critico), (Decimal("8.500"), Decimal("1.500"), True))

    def test_mesero_no_ve_la_alerta_de_inventario(self):
        Insumo.objects.filter(pk=self.pan.pk).update(stock_actual=Decimal("0"))
        self.assertNotContains(self.cliente(self.mesero).get("/mesas/"), "Stock crítico")

    def test_cocina_recibe_aviso_al_dejar_un_insumo_en_nivel_critico(self):
        Insumo.objects.filter(pk=self.res.pk).update(stock_actual=Decimal("2.5"))
        d = self.pedido(items=[(self.bistec, 1)]).detalles.get()
        r = self.cliente(self.chef).post(f"/cocina/item/{d.pk}/", {"accion": "preparar"}, follow=True)
        self.assertContains(r, "nivel crítico")

    def test_inventario_resalta_los_criticos_primero(self):
        Insumo.objects.filter(pk=self.pan.pk).update(stock_actual=Decimal("0.5"))
        r = self.cliente(self.cajero).get("/inventario/")
        self.assertContains(r, "CRÍTICO")
        self.assertEqual(r.context["insumos"][0].pk, self.pan.pk)


class DisponibilidadYReposicionTests(DatosBase):
    def test_disponibilidad_descuenta_lo_ya_comprometido(self):
        # 10 kg de res: la hamburguesa usa 0.2 kg y 1 pan (hay 5 panes) -> 5 porciones.
        self.assertEqual(disponibilidad_menu()[self.hamburguesa.pk], 5)
        self.assertEqual(disponibilidad_menu()[self.bistec.pk], 20)
        self.pedido(items=[(self.hamburguesa, 2)], enviar=False)
        self.assertEqual(disponibilidad_menu()[self.hamburguesa.pk], 3)

    def test_producto_sin_receta_es_ilimitado(self):
        self.assertNotIn(self.agua.pk, disponibilidad_menu())

    def test_no_se_pide_mas_de_lo_que_alcanza(self):
        c = self.cliente(self.mesero)
        c.post(f"/mesas/abrir/{self.mesa1.pk}/")
        pedido = self.mesa1.pedidos.get()
        c.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.hamburguesa.pk, "cantidad": 6})
        self.assertFalse(pedido.detalles.exists())
        c.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.hamburguesa.pk, "cantidad": 5})
        self.assertEqual(pedido.detalles.count(), 1)

    def test_reposicion_suma_stock_y_registra_la_entrada(self):
        r = self.cliente(self.cajero).post(
            "/inventario/entrada/", {"insumo_id": self.pan.pk, "cantidad": "20", "nota": "Proveedor A"}
        )
        self.assertEqual(r.status_code, 302)
        self.pan.refresh_from_db()
        self.assertEqual(self.pan.stock_actual, Decimal("25.000"))
        mov = MovimientoStock.objects.get(insumo=self.pan)
        self.assertEqual((mov.tipo, mov.cantidad, mov.stock_resultante), ("ENT", Decimal("20"), Decimal("25.000")))

    def test_reposicion_rechaza_cantidades_invalidas(self):
        self.cliente(self.cajero).post("/inventario/entrada/", {"insumo_id": self.pan.pk, "cantidad": "-3"})
        self.pan.refresh_from_db()
        self.assertEqual(self.pan.stock_actual, Decimal("5.000"))

    def test_mesero_no_repone_inventario(self):
        r = self.cliente(self.mesero).post("/inventario/entrada/", {"insumo_id": self.pan.pk, "cantidad": "5"})
        self.assertEqual(r.status_code, 403)

    def test_ajuste_manual_en_el_admin_queda_registrado(self):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        from inventario.admin import InsumoAdmin

        request = RequestFactory().post("/")
        request.user = self.admin
        modelo_admin = InsumoAdmin(Insumo, django_admin.site)

        class FormaFalsa:
            changed_data = ["stock_actual"]

        self.pan.stock_actual = Decimal("9")
        modelo_admin.save_model(request, self.pan, FormaFalsa(), change=True)
        mov = MovimientoStock.objects.get(insumo=self.pan, tipo=MovimientoStock.Tipo.AJUSTE)
        self.assertEqual(mov.cantidad, Decimal("4"))

    def test_precio_en_moneda_local_sigue_a_la_tasa_vigente(self):
        self.assertEqual(self.bistec.precio_local, Decimal("400.00"))
        TasaCambio.objects.filter(activa=True).update(activa=False)
        TasaCambio.objects.create(valor=Decimal("50.0000"), creada_por=self.cajero, activa=True)
        self.assertEqual(self.bistec.precio_local, Decimal("500.00"))
