import csv
import io
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from caja.models import CierreCaja, Devolucion, Factura, Pago, TasaCambio
from config.base_pruebas import DatosBase
from mesas.models import Mesa, Pedido


class FacturacionTests(DatosBase):
    """RF-05: consumo consolidado y emisión de facturas."""

    def test_cuentas_muestra_el_consumo_consolidado_por_mesa(self):
        self.pedido(self.mesa1, items=[(self.bistec, 2), (self.agua, 1, "Con hielo")])
        r = self.cliente(self.cajero).get("/caja/")
        self.assertContains(r, "2 × Bistec")
        self.assertContains(r, "Con hielo")
        cuenta = r.context["cuentas_abiertas"][0]
        self.assertEqual(cuenta["subtotal"], Decimal("840.00"))
        self.assertEqual(cuenta["usd"], Decimal("21.00"))

    def test_las_cuentas_solicitadas_van_primero(self):
        self.pedido(self.mesa1, items=[(self.bistec, 1)])
        self.pedido(self.mesa2, items=[(self.agua, 1)])
        self.mesa2.marcar_por_facturar()
        mesas = [c["pedido"].mesa.numero for c in self.cliente(self.cajero).get("/caja/").context["cuentas_abiertas"]]
        self.assertEqual(mesas, [2, 1])

    def test_no_se_factura_un_pedido_vacio(self):
        vacio = self.pedido(items=[])
        with self.assertRaisesMessage(ValidationError, "no tiene platillos"):
            Factura.emitir_desde_pedido(vacio, self.cajero)

    def test_no_se_factura_con_platillos_sin_enviar_a_cocina(self):
        pedido = self.pedido(items=[(self.bistec, 1)], enviar=False)
        with self.assertRaisesMessage(ValidationError, "sin enviar"):
            Factura.emitir_desde_pedido(pedido, self.cajero)
        r = self.cliente(self.cajero).post(f"/caja/pedido/{pedido.pk}/facturar/")
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Factura.objects.exists())

    def test_factura_congela_tasa_y_totales_y_pone_la_mesa_por_facturar(self):
        pedido = self.pedido(items=[(self.bistec, 2)])
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        self.assertEqual((f.total_local, f.total_usd, f.tasa_cambio), (Decimal("800.00"), Decimal("20.00"), Decimal("40.0000")))
        self.assertEqual(f.numero, "F-000001")
        self.mesa1.refresh_from_db()
        self.assertEqual(self.mesa1.estado, Mesa.Estado.POR_FACTURAR)
        TasaCambio.objects.filter(activa=True).update(activa=False)
        TasaCambio.objects.create(valor=Decimal("99"), creada_por=self.cajero, activa=True)
        f.refresh_from_db()
        self.assertEqual(f.total_usd, Decimal("20.00"))

    def test_un_pedido_no_se_factura_dos_veces(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        Factura.emitir_desde_pedido(pedido, self.cajero)
        with self.assertRaisesMessage(ValidationError, "ya tiene una factura"):
            Factura.emitir_desde_pedido(pedido, self.cajero)

    def test_anular_factura_sin_pagos_reabre_la_mesa_y_permite_refacturar(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        c = self.cliente(self.cajero)
        c.post(f"/caja/factura/{f.pk}/anular/", {"motivo": "Error de digitación"})
        f.refresh_from_db()
        self.mesa1.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.ANULADA)
        self.assertEqual(f.motivo_anulacion, "Error de digitación")
        self.assertEqual(self.mesa1.estado, Mesa.Estado.OCUPADA)
        nueva = Factura.emitir_desde_pedido(pedido, self.cajero)
        self.assertEqual(nueva.numero, "F-000002")

    def test_no_se_anula_una_factura_con_pagos_ni_sin_motivo(self):
        pedido = self.pedido(items=[(self.bistec, 2)])
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        with self.assertRaisesMessage(ValidationError, "motivo"):
            f.anular("  ")
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("100"))
        with self.assertRaisesMessage(ValidationError, "pagos"):
            f.anular("Error")


class PagosTests(DatosBase):
    """RF-05: pagos totales o divididos con efectivo, pago móvil, divisa y tarjeta."""

    def factura(self, cantidad=1, producto=None, mesa=None):
        pedido = self.pedido(mesa or self.mesa1, items=[(producto or self.bistec, cantidad)])
        return Factura.emitir_desde_pedido(pedido, self.cajero)

    def test_los_metodos_disponibles_son_los_del_requisito(self):
        self.assertEqual(sorted(Pago.Metodo.values), sorted(["EFE", "PMO", "DIV", "TAR"]))

    def test_pago_total_en_efectivo_cierra_cuenta_y_deja_la_mesa_por_limpiar(self):
        f = self.factura()
        pago, f = f.cobrar(metodo="EFE", monto=Decimal("400"))
        self.mesa1.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.PAGADA)
        self.assertIsNotNone(f.pagada_en)
        self.assertEqual(f.pedido.estado, Pedido.Estado.CERRADO)
        self.assertEqual(self.mesa1.estado, Mesa.Estado.POR_LIMPIAR)
        self.assertEqual(pago.vuelto_local, 0)

    def test_efectivo_con_vuelto(self):
        pago, f = self.factura().cobrar(metodo="EFE", monto=Decimal("500"))
        self.assertEqual((pago.monto_local, pago.vuelto_local), (Decimal("400.00"), Decimal("100.00")))

    def test_pago_en_divisa_se_convierte_con_la_tasa_de_la_factura(self):
        pago, f = self.factura().cobrar(metodo="DIV", monto=Decimal("10"))
        self.assertEqual(pago.moneda, "USD")
        self.assertEqual(pago.monto_local, Decimal("400.00"))
        self.assertEqual(f.estado, Factura.Estado.PAGADA)

    def test_divisa_con_exceso_entrega_vuelto_en_bolivares(self):
        pago, _ = self.factura().cobrar(metodo="DIV", monto=Decimal("20"))
        self.assertEqual((pago.monto_local, pago.vuelto_local), (Decimal("400.00"), Decimal("400.00")))

    def test_pagar_el_total_exacto_en_usd_salda_aunque_la_tasa_tenga_decimales(self):
        TasaCambio.objects.filter(activa=True).update(activa=False)
        TasaCambio.objects.create(valor=Decimal("36.4723"), creada_por=self.cajero, activa=True)
        f = self.factura(cantidad=2)
        _, f = f.cobrar(metodo="DIV", monto=f.total_usd)
        self.assertEqual(f.saldo_local, Decimal("0.00"))
        self.assertEqual(f.estado, Factura.Estado.PAGADA)

    def test_pago_movil_y_tarjeta_exigen_referencia(self):
        f = self.factura()
        for metodo in ("PMO", "TAR"):
            with self.assertRaisesMessage(ValidationError, "referencia"):
                f.cobrar(metodo=metodo, monto=Decimal("100"))
        pago, _ = f.cobrar(metodo="PMO", monto=Decimal("100"), referencia="00123456")
        self.assertEqual(pago.referencia, "00123456")

    def test_pago_movil_y_tarjeta_no_dan_vuelto(self):
        f = self.factura()
        with self.assertRaisesMessage(ValidationError, "no se entrega vuelto"):
            f.cobrar(metodo="TAR", monto=Decimal("999"), referencia="V-1")

    def test_cuenta_dividida_con_varios_metodos(self):
        f = self.factura(cantidad=2)  # 800 Bs. / 20 USD
        f.cobrar(metodo="EFE", monto=Decimal("200"), pagador="Ana")
        f.cobrar(metodo="DIV", monto=Decimal("5"), pagador="Luis")  # 200 Bs.
        f.cobrar(metodo="PMO", monto=Decimal("150"), referencia="9911", pagador="Eva")
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.ABIERTA)
        self.assertEqual(f.saldo_local, Decimal("250.00"))
        _, f = f.cobrar(metodo="TAR", monto=Decimal("250"), referencia="V-77", pagador="Mario")
        self.assertEqual(f.estado, Factura.Estado.PAGADA)
        self.assertEqual(f.pagos.count(), 4)
        self.assertEqual(f.total_pagado_local, Decimal("800.00"))

    def test_no_se_paga_una_factura_ya_cerrada(self):
        f = self.factura()
        _, f = f.cobrar(metodo="EFE", monto=Decimal("400"))
        with self.assertRaises(ValidationError):
            f.cobrar(metodo="EFE", monto=Decimal("1"))

    def test_pago_desde_la_pantalla_valida_el_monto(self):
        f = self.factura()
        c = self.cliente(self.cajero)
        c.post(f"/caja/factura/{f.pk}/pago/", {"metodo": "EFE", "monto_recibido": "abc"})
        c.post(f"/caja/factura/{f.pk}/pago/", {"metodo": "EFE", "monto_recibido": "-5"})
        self.assertFalse(f.pagos.exists())
        c.post(f"/caja/factura/{f.pk}/pago/", {"metodo": "PMO", "monto_recibido": "400", "referencia": "R1"})
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.PAGADA)

    def test_calculo_de_cuenta_dividida_entre_personas(self):
        f = self.factura(cantidad=3)  # 1200 Bs.
        r = self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/?partes=4")
        self.assertEqual(r.context["cuota_local"], Decimal("300.00"))
        self.assertEqual(r.context["cuota_usd"], Decimal("7.50"))


class CierreDeCajaTests(DatosBase):
    """RF-05: cierre y reporte de caja diario."""

    def vender(self, cajero, mesa, metodo, monto, cantidad=1, referencia=""):
        pedido = self.pedido(mesa, items=[(self.bistec, cantidad)])
        f = Factura.emitir_desde_pedido(pedido, cajero)
        return f.cobrar(metodo=metodo, monto=Decimal(monto), referencia=referencia)

    def test_cierre_desglosa_por_metodo_y_moneda(self):
        self.vender(self.cajero, self.mesa1, "EFE", "500")          # 400 aplicado, vuelto 100
        self.vender(self.cajero, self.mesa2, "DIV", "10")           # 10 USD
        self.vender(self.cajero, self.mesa3, "PMO", "400", referencia="R1")
        cierre = CierreCaja.generar(self.cajero)
        lineas = {l["metodo"]: l for l in cierre.desglose["lineas"]}
        self.assertEqual(set(lineas), {"EFE", "DIV", "PMO"})
        self.assertEqual(lineas["DIV"]["moneda"], "USD")
        self.assertEqual(Decimal(lineas["DIV"]["recibido"]), Decimal("10"))
        self.assertEqual(cierre.total_local, Decimal("1200.00"))
        self.assertEqual(cierre.total_usd, Decimal("30.00"))
        self.assertEqual(cierre.cantidad_facturas, 3)
        # Efectivo neto en caja: 500 recibidos - 100 de vuelto = 400 Bs.; 10 USD en divisas.
        self.assertEqual(Decimal(cierre.desglose["efectivo_bs_neto"]), Decimal("400.00"))
        self.assertEqual(Decimal(cierre.desglose["divisas_usd"]), Decimal("10.00"))

    def test_cada_cierre_solo_incluye_lo_nuevo(self):
        self.vender(self.cajero, self.mesa1, "EFE", "400")
        primero = CierreCaja.generar(self.cajero)
        with self.assertRaisesMessage(ValidationError, "No hay movimientos nuevos"):
            CierreCaja.generar(self.cajero)
        self.vender(self.cajero, self.mesa2, "EFE", "800", cantidad=2)
        segundo = CierreCaja.generar(self.cajero)
        self.assertEqual(primero.total_local, Decimal("400.00"))
        self.assertEqual(segundo.total_local, Decimal("800.00"))
        self.assertEqual(CierreCaja.objects.filter(cajero=self.cajero).count(), 2)

    def test_cada_cajero_cierra_lo_suyo(self):
        self.vender(self.cajero, self.mesa1, "EFE", "400")
        self.vender(self.cajero2, self.mesa2, "EFE", "400")
        self.assertEqual(CierreCaja.generar(self.cajero).cantidad_facturas, 1)
        self.assertEqual(CierreCaja.generar(self.cajero2).cantidad_facturas, 1)

    def test_el_cierre_cuenta_las_facturas_anuladas(self):
        pedido = self.pedido(self.mesa1, items=[(self.bistec, 1)])
        Factura.emitir_desde_pedido(pedido, self.cajero).anular("Duplicada")
        self.vender(self.cajero, self.mesa2, "EFE", "400")
        self.assertEqual(CierreCaja.generar(self.cajero).facturas_anuladas, 1)

    def test_pantalla_de_cierre_y_generacion(self):
        self.vender(self.cajero, self.mesa1, "EFE", "400")
        c = self.cliente(self.cajero)
        r = c.get("/caja/cierre/")
        self.assertContains(r, "Efectivo (Bs.)")
        r = c.post("/caja/cierre/generar/", follow=True)
        self.assertEqual(r.status_code, 200)
        cierre = CierreCaja.objects.get()
        self.assertContains(r, "Cierre de caja")
        self.assertEqual(cierre.cajero, self.cajero)

    def test_exportar_cierre_a_csv(self):
        self.vender(self.cajero, self.mesa1, "DIV", "10")
        cierre = CierreCaja.generar(self.cajero)
        r = self.cliente(self.cajero).get(f"/caja/cierre/{cierre.pk}/csv/")
        self.assertTrue(r["Content-Type"].startswith("text/csv"))
        filas = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
        self.assertTrue(any("Divisa" in " ".join(f) for f in filas))
        self.assertTrue(any(f and f[0] == "F-000001" for f in filas))

    def test_un_cajero_no_ve_los_cierres_de_otro_pero_el_admin_si(self):
        self.vender(self.cajero2, self.mesa1, "EFE", "400")
        ajeno = CierreCaja.generar(self.cajero2)
        self.assertEqual(self.cliente(self.cajero).get(f"/caja/cierre/{ajeno.pk}/").status_code, 403)
        self.assertEqual(self.cliente(self.cajero2).get(f"/caja/cierre/{ajeno.pk}/").status_code, 200)
        self.assertEqual(self.cliente(self.admin).get(f"/caja/cierre/{ajeno.pk}/").status_code, 200)
        self.assertEqual(len(self.cliente(self.cajero).get("/caja/cierre/historial/").context["cierres"]), 0)

    def test_reporte_del_dia_consolida_a_todos_los_cajeros_para_el_admin(self):
        self.vender(self.cajero, self.mesa1, "EFE", "400")
        self.vender(self.cajero2, self.mesa2, "PMO", "400", referencia="R2")
        admin = self.cliente(self.admin).get("/caja/reporte/")
        self.assertEqual(admin.context["datos"]["total_local"], Decimal("800.00"))
        propio = self.cliente(self.cajero).get("/caja/reporte/")
        self.assertEqual(propio.context["datos"]["total_local"], Decimal("400.00"))

    def test_reporte_de_otro_dia_sale_vacio_y_exporta_csv(self):
        self.vender(self.cajero, self.mesa1, "EFE", "400")
        ayer = (timezone.localdate() - timedelta(days=1)).isoformat()
        r = self.cliente(self.admin).get(f"/caja/reporte/?fecha={ayer}")
        self.assertEqual(r.context["datos"]["total_local"], Decimal("0.00"))
        r = self.cliente(self.admin).get("/caja/reporte/?formato=csv")
        self.assertEqual(r.status_code, 200)

    def test_pagos_parciales_de_una_factura_abierta_cuentan_como_cobrado(self):
        pedido = self.pedido(self.mesa1, items=[(self.bistec, 2)])
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        f.cobrar(metodo="EFE", monto=Decimal("300"))
        cierre = CierreCaja.generar(self.cajero)
        self.assertEqual(cierre.total_local, Decimal("300.00"))
        self.assertEqual(cierre.cantidad_facturas, 0)


class DevolucionTests(DatosBase):
    """Anular una factura con pagos: se devuelve el dinero y la caja lo refleja."""

    def pagada(self, mesa=None, metodo="EFE", monto="400", cantidad=1, referencia=""):
        pedido = self.pedido(mesa or self.mesa1, items=[(self.bistec, cantidad)])
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        f.cobrar(metodo=metodo, monto=Decimal(monto), referencia=referencia)
        f.refresh_from_db()
        return f

    def test_devolver_una_factura_pagada_registra_el_reverso_de_cada_pago(self):
        f = self.pagada(monto="500")  # 400 aplicados + 100 de vuelto
        devoluciones = f.anular_con_devolucion("Cliente inconforme", self.admin)
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.ANULADA)
        self.assertEqual(f.motivo_anulacion, "Cliente inconforme")
        self.assertEqual(len(devoluciones), 1)
        self.assertEqual((devoluciones[0].metodo, devoluciones[0].monto), ("EFE", Decimal("400.00")))
        self.assertEqual(devoluciones[0].registrado_por, self.admin)

    def test_la_devolucion_en_divisa_se_hace_en_usd(self):
        f = self.pagada(metodo="DIV", monto="20")  # 10 USD aplicados, resto en vuelto
        (dev,) = f.anular_con_devolucion("Error de cobro", self.admin)
        self.assertEqual((dev.moneda, dev.monto, dev.monto_local), ("USD", Decimal("10.00"), Decimal("400.00")))

    def test_pedido_y_mesa_de_una_venta_ya_cerrada_no_se_tocan_ni_se_repone_stock(self):
        f = self.pagada()
        self.res.refresh_from_db()
        stock = self.res.stock_actual
        f.anular_con_devolucion("Error", self.admin)
        f.pedido.refresh_from_db()
        self.mesa1.refresh_from_db()
        self.res.refresh_from_db()
        self.assertEqual(f.pedido.estado, Pedido.Estado.CERRADO)
        self.assertEqual(self.mesa1.estado, Mesa.Estado.POR_LIMPIAR)
        self.assertEqual(self.res.stock_actual, stock)
        with self.assertRaises(ValidationError):
            Factura.emitir_desde_pedido(f.pedido, self.cajero)

    def test_pagos_parciales_se_devuelven_y_el_pedido_se_puede_refacturar(self):
        pedido = self.pedido(items=[(self.bistec, 2)])
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        f.cobrar(metodo="EFE", monto=Decimal("300"))
        f.cobrar(metodo="PMO", monto=Decimal("100"), referencia="R1")
        devoluciones = f.anular_con_devolucion("Se equivocaron de mesa", self.admin)
        self.assertEqual(len(devoluciones), 2)
        self.mesa1.refresh_from_db()
        self.assertEqual(self.mesa1.estado, Mesa.Estado.OCUPADA)
        nueva = Factura.emitir_desde_pedido(pedido, self.cajero)
        self.assertEqual(nueva.total_pagado_local, Decimal("0.00"))

    def test_exige_motivo_y_no_se_repite(self):
        f = self.pagada()
        with self.assertRaisesMessage(ValidationError, "motivo"):
            f.anular_con_devolucion("  ", self.admin)
        f.anular_con_devolucion("Error", self.admin)
        with self.assertRaisesMessage(ValidationError, "ya está anulada"):
            f.anular_con_devolucion("Otra vez", self.admin)
        self.assertEqual(Devolucion.objects.count(), 1)

    def test_el_cajero_no_puede_devolver_pero_el_administrador_si(self):
        f = self.pagada()
        r = self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/anular/", {"motivo": "Prueba"}, follow=True)
        self.assertContains(r, "Solo un administrador")
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.PAGADA)
        r = self.cliente(self.admin).post(f"/caja/factura/{f.pk}/anular/", {"motivo": "Prueba"}, follow=True)
        self.assertContains(r, "Devolver al cliente")
        self.assertContains(r, "Devoluciones")
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.ANULADA)

    def test_solo_el_admin_ve_el_formulario_de_devolucion(self):
        f = self.pagada()
        self.assertNotContains(self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/"), "Anular y devolver")
        self.assertContains(self.cliente(self.admin).get(f"/caja/factura/{f.pk}/"), "Anular y devolver")

    def test_el_cierre_posterior_descuenta_la_devolucion(self):
        f = self.pagada(metodo="DIV", monto="10")
        self.pagada(self.mesa2, metodo="EFE", monto="400")
        primero = CierreCaja.generar(self.cajero)
        self.assertEqual(primero.total_local, Decimal("800.00"))
        f.anular_con_devolucion("Error", self.admin)
        datos = CierreCaja.calcular(self.cajero, primero.hasta)
        self.assertEqual(datos["cantidad_devoluciones"], 1)
        self.assertEqual(datos["total_local"], Decimal("-400.00"))
        self.assertEqual(datos["total_usd"], Decimal("-10.00"))
        self.assertEqual(datos["divisas_usd"], Decimal("-10.00"))
        segundo = CierreCaja.generar(self.cajero)
        self.assertEqual(segundo.total_local, Decimal("-400.00"))
        self.assertEqual(segundo.desglose["cantidad_devoluciones"], 1)
        # El cierre anterior no cambia al recalcularlo.
        detalle = CierreCaja.calcular(primero.cajero, primero.desde, primero.hasta)
        self.assertEqual((detalle["total_local"], detalle["cantidad_facturas"]), (Decimal("800.00"), 2))

    def test_cobro_y_devolucion_en_el_mismo_cierre_se_netean(self):
        f = self.pagada()
        f.anular_con_devolucion("Error", self.admin)
        cierre = CierreCaja.generar(self.cajero)
        self.assertEqual(cierre.total_local, Decimal("0.00"))
        self.assertEqual(Decimal(cierre.desglose["efectivo_bs_neto"]), Decimal("0.00"))

    def test_la_devolucion_cuenta_como_movimiento_para_generar_cierre(self):
        f = self.pagada()
        CierreCaja.generar(self.cajero)
        f.anular_con_devolucion("Error", self.admin)
        self.assertEqual(CierreCaja.generar(self.cajero).cantidad_facturas, 0)

    def test_el_csv_y_el_reporte_marcan_las_facturas_devueltas(self):
        f = self.pagada()
        f.anular_con_devolucion("Error", self.admin)
        r = self.cliente(self.admin).get("/caja/reporte/?formato=csv")
        self.assertIn("Devuelta", r.content.decode("utf-8-sig"))
        r = self.cliente(self.admin).get("/caja/reporte/")
        self.assertEqual(r.context["datos"]["total_local"], Decimal("0.00"))
        self.assertContains(r, "Devuelta")


class CuentaDivididaPorPlatilloTests(DatosBase):
    """Cada comensal paga solo lo que consumió."""

    def setUp(self):
        super().setUp()
        self.pedido_dividido = self.pedido(items=[(self.bistec, 2), (self.agua, 1)])
        self.factura = Factura.emitir_desde_pedido(self.pedido_dividido, self.cajero)
        self.bistec_d = self.pedido_dividido.detalles.get(producto=self.bistec)
        self.agua_d = self.pedido_dividido.detalles.get(producto=self.agua)

    def asignar_basico(self):
        self.factura.asignar(self.bistec_d.pk, "Ana", 1)
        self.factura.asignar(self.agua_d.pk, "Ana")
        self.factura.asignar(self.bistec_d.pk, "Luis", 1)

    def test_resumen_por_comensal(self):
        self.asignar_basico()
        r = self.factura.resumen_por_pagador()
        ana, luis = r["personas"]
        self.assertEqual((ana["nombre"], ana["total_local"], ana["total_usd"]), ("Ana", Decimal("440.00"), Decimal("11.00")))
        self.assertEqual((luis["nombre"], luis["total_local"]), ("Luis", Decimal("400.00")))
        self.assertEqual(r["sin_asignar_local"], Decimal("0.00"))
        self.assertEqual(r["libres"], [])
        self.assertEqual(ana["total_local"] + luis["total_local"], self.factura.total_local)

    def test_lo_no_asignado_se_informa(self):
        self.factura.asignar(self.bistec_d.pk, "Ana", 1)
        r = self.factura.resumen_por_pagador()
        self.assertEqual(r["sin_asignar_local"], Decimal("440.00"))
        self.assertEqual({(f["detalle"].producto.nombre, f["restante"]) for f in r["libres"]}, {("Bistec", 1), ("Agua", 1)})

    def test_no_se_asigna_mas_de_lo_pedido_ni_de_otra_cuenta(self):
        self.factura.asignar(self.bistec_d.pk, "Ana", 2)
        with self.assertRaisesMessage(ValidationError, "ya está asignado completo"):
            self.factura.asignar(self.bistec_d.pk, "Luis", 1)
        self.factura.asignar(self.agua_d.pk, "Luis", 1)
        with self.assertRaises(ValidationError):
            self.factura.asignar(self.agua_d.pk, "Eva", 1)
        ajeno = self.pedido(self.mesa2, items=[(self.agua, 1)]).detalles.get()
        with self.assertRaisesMessage(ValidationError, "no pertenece"):
            self.factura.asignar(ajeno.pk, "Ana", 1)
        with self.assertRaises(ValidationError):
            self.factura.asignar(self.agua_d.pk, "  ", 1)

    def test_el_mismo_nombre_se_acumula_sin_importar_mayusculas(self):
        self.factura.asignar(self.bistec_d.pk, "Ana", 1)
        self.factura.asignar(self.bistec_d.pk, "  ana ", 1)
        self.assertEqual(self.factura.asignaciones.count(), 1)
        self.assertEqual(self.factura.asignaciones.get().cantidad, 2)

    def test_cobrar_a_cada_comensal_su_parte_cierra_la_cuenta(self):
        self.asignar_basico()
        self.factura.cobrar(metodo="EFE", monto=Decimal("440"), pagador="ana")
        r = self.factura.resumen_por_pagador()
        self.assertEqual([(p["nombre"], p["pendiente_local"]) for p in r["personas"]], [("Ana", Decimal("0.00")), ("Luis", Decimal("400.00"))])
        _, f = self.factura.cobrar(metodo="PMO", monto=Decimal("400"), referencia="R9", pagador="Luis")
        self.assertEqual(f.estado, Factura.Estado.PAGADA)

    def test_pantalla_asignar_sugerir_monto_y_quitar(self):
        c = self.cliente(self.cajero)
        url = f"/caja/factura/{self.factura.pk}/"
        cerrada = c.get(url)
        self.assertContains(cerrada, "Registrar pago")
        self.assertContains(cerrada, "Dividir la cuenta (opcional)")
        self.assertNotContains(cerrada, 'class="opcional" open')
        self.assertLess(cerrada.content.find(b"Registrar pago"), cerrada.content.find(b"Dividir por platillo"))
        c.post(url + "asignar/", {"detalle_id": self.bistec_d.pk, "pagador": "Ana", "cantidad": "1"})
        c.post(url + "asignar/", {"detalle_id": self.agua_d.pk, "pagador": "Ana"})
        r = c.get(url)
        self.assertContains(r, "Dividir por platillo")
        self.assertContains(r, 'class="opcional" open')
        self.assertEqual(r.context["division"]["personas"][0]["total_local"], Decimal("440.00"))
        r = c.get(url + "?pagador=Ana")
        self.assertEqual((r.context["prefill_pagador"], r.context["prefill_monto"]), ("Ana", Decimal("440.00")))
        self.assertContains(r, 'value="440.00"')
        asignacion = self.factura.asignaciones.first()
        c.post(url + "asignar/quitar/", {"asignacion_id": asignacion.pk})
        self.assertEqual(self.factura.asignaciones.count(), 1)
        c.post(url + "asignar/quitar/")
        self.assertEqual(self.factura.asignaciones.count(), 0)

    def test_valores_invalidos_en_la_pantalla_no_rompen(self):
        c = self.cliente(self.cajero)
        url = f"/caja/factura/{self.factura.pk}/asignar/"
        for datos in ({"detalle_id": "x", "pagador": "Ana"}, {"detalle_id": self.agua_d.pk, "pagador": "Ana", "cantidad": "abc"},
                      {"detalle_id": self.agua_d.pk, "pagador": "", "cantidad": "1"}, {"detalle_id": self.agua_d.pk, "pagador": "Ana", "cantidad": "9"}):
            self.assertEqual(c.post(url, datos).status_code, 302)
        self.assertFalse(self.factura.asignaciones.exists())
        self.assertEqual(c.post(f"/caja/factura/{self.factura.pk}/asignar/quitar/", {"asignacion_id": "zz"}).status_code, 302)

    def test_no_se_divide_una_factura_ya_pagada_y_el_mesero_no_accede(self):
        self.factura.cobrar(metodo="EFE", monto=Decimal("840"))
        self.factura.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "factura abierta"):
            self.factura.asignar(self.agua_d.pk, "Ana", 1)
        r = self.cliente(self.mesero).post(f"/caja/factura/{self.factura.pk}/asignar/", {"detalle_id": self.agua_d.pk, "pagador": "A"})
        self.assertEqual(r.status_code, 403)
