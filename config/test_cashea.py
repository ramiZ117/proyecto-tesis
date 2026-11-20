"""Ventas financiadas con Cashea: inicial en caja, lo financiado por cobrar y el pago de cada miércoles."""

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.utils import timezone

from caja.models import CierreCaja, Factura, FinanciamientoCashea, LiquidacionCashea, Pago, miercoles_de_pago
from caja.ticket import ticket_cierre, ticket_factura
from config.base_pruebas import DatosBase
from gestion import finanzas
from gestion.models import AjustesLocal
from menu.mesa import codigo


class CasheaTests(DatosBase):
    def factura(self, mesa=None):
        return Factura.emitir_desde_pedido(self.pedido(mesa=mesa, items=[(self.bistec, 1)]), self.cajero)  # $10 = Bs 400

    def cobrar_con_cashea(self, f, inicial_bs="160", financiado_usd="6", orden="CSH-1001"):
        return self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/pago/", {
            "metodo": ["PMO", "CAS"], "monto_recibido": [inicial_bs, financiado_usd], "referencia": ["4455", orden],
        }, follow=True)

    def test_miercoles_de_pago(self):
        # ventas de lunes 16 a domingo 22 de noviembre de 2026 → miércoles 25
        for dia in range(16, 23):
            self.assertEqual(miercoles_de_pago(date(2026, 11, dia)), date(2026, 11, 25))
        self.assertEqual(miercoles_de_pago(date(2026, 11, 23)), date(2026, 12, 2))

    def test_inicial_en_caja_y_lo_financiado_queda_por_cobrar(self):
        f = self.factura()
        r = self.cobrar_con_cashea(f)
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.PAGADA)  # el cliente ya cumplió con el local
        fin = FinanciamientoCashea.objects.get()
        self.assertEqual((fin.orden, fin.monto_usd, fin.monto_local, fin.estado),
                         ("CSH-1001", Decimal("6.00"), Decimal("240.00"), FinanciamientoCashea.Estado.PENDIENTE))
        self.assertEqual(fin.fecha_pago, miercoles_de_pago(timezone.localdate()))
        self.assertEqual(fin.fecha_pago.weekday(), 2)
        # en la factura aparece lo financiado con su orden
        texto = ticket_factura(f)
        self.assertIn("FINANCIADO $ 6,00", texto)
        self.assertNotIn("CASHEA", texto.upper())
        self.assertIn("Orden CSH-1001", texto)
        # el cierre no lo cuenta como dinero recibido
        datos = CierreCaja.calcular(self.cajero)
        self.assertEqual(datos["total_local"], Decimal("400.00"))
        self.assertEqual(datos["financiado_local"], Decimal("240.00"))
        self.assertEqual(datos["recibido_local"], Decimal("160.00"))
        self.assertEqual(datos["efectivo_bs_neto"], Decimal("0.00"))
        cierre = CierreCaja.generar(self.cajero)
        nota = ticket_cierre(cierre)
        self.assertIn("RECIBIDO EN CAJA", nota)
        self.assertIn("Bs 160,00", nota)
        self.assertEqual(cierre.datos_guardados()["financiado_usd"], Decimal("6.00"))
        # módulo de financiamiento
        self.assertEqual(self.cliente(self.cajero).get("/caja/cashea/").status_code, 403)
        pagina = self.cliente(self.admin).get("/caja/cashea/")
        self.assertContains(pagina, "Financiado por cobrar")
        self.assertContains(pagina, "CSH-1001")
        self.assertContains(pagina, "$6,00")

    def test_validaciones_del_cobro_con_cashea(self):
        f = self.factura()
        r = self.cobrar_con_cashea(f, orden="")
        self.assertContains(r, "número de orden del financiamiento")
        r = self.cobrar_con_cashea(f, financiado_usd="15")  # más de lo que se debe: Cashea no da vuelto
        self.assertContains(r, "no se entrega vuelto")
        self.assertFalse(FinanciamientoCashea.objects.exists())
        self.assertFalse(f.pagos.exists())
        ajustes = AjustesLocal.actual()
        ajustes.cashea_activo = False
        ajustes.save()
        r = self.cobrar_con_cashea(f)
        self.assertContains(r, "no están activas")
        self.assertNotContains(self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/"), 'id="metodo-CAS"')

    def test_la_propina_y_el_menu_no_usan_cashea(self):
        f = self.factura()
        pagina = self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/")
        self.assertContains(pagina, 'id="metodo-CAS"')
        self.assertNotContains(pagina, '<option value="CAS">Cashea</option>\n            </select>\n            <input name="propina_monto"')
        r = self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/pago/", {
            "metodo": "EFE", "monto_recibido": "400", "propina_metodo": "CAS", "propina_monto": "10"}, follow=True)
        self.assertContains(r, "no se puede financiar")
        respuesta = self.client.post("/menu/cuenta/", json.dumps({"mesa": 1, "codigo": codigo(1), "metodo": "CAS"}),
                                     content_type="application/json")
        self.assertEqual(respuesta.status_code, 400)

    def test_cashea_paga_el_miercoles_con_comision(self):
        self.cobrar_con_cashea(self.factura(mesa=self.mesa1), orden="A-1")
        self.cobrar_con_cashea(self.factura(mesa=self.mesa2), financiado_usd="4", inicial_bs="240", orden="A-2")
        ids = list(FinanciamientoCashea.objects.values_list("pk", flat=True))
        caja = self.cliente(self.admin)
        r = caja.post("/caja/cashea/registrar-pago/", {"financiamiento": ids, "monto": "9.50", "moneda": "USD",
                                                       "referencia": "TRF-77", "fecha": timezone.localdate().isoformat()},
                      follow=True)
        self.assertContains(r, "Cashea retuvo $0,50")
        liq = LiquidacionCashea.objects.get()
        self.assertEqual((liq.monto_esperado_usd, liq.monto_recibido_usd, liq.descuento_usd),
                         (Decimal("10.00"), Decimal("9.50"), Decimal("0.50")))
        self.assertEqual(FinanciamientoCashea.objects.filter(estado="COB").count(), 2)
        self.assertContains(caja.get("/caja/cashea/"), "No hay ventas financiadas por cobrar")
        # no se puede cobrar dos veces lo mismo
        r = caja.post("/caja/cashea/registrar-pago/", {"financiamiento": ids, "monto": "10", "moneda": "USD"}, follow=True)
        self.assertContains(r, "ya se registró")
        # la comisión resta en finanzas
        hoy = timezone.localdate()
        resultado = finanzas.resultado_mes(hoy.year, hoy.month)
        self.assertEqual(resultado["comision_cashea"], Decimal("0.50"))
        self.assertEqual(resultado["cashea_por_cobrar"], Decimal("0.00"))

    def test_pago_de_cashea_en_bolivares_y_validaciones(self):
        self.cobrar_con_cashea(self.factura())
        fin = FinanciamientoCashea.objects.get()
        caja = self.cliente(self.admin)
        manana = (timezone.localdate() + timedelta(days=1)).isoformat()
        for datos, texto in (({"monto": "240", "moneda": "LOC", "fecha": manana}, "no puede ser futura"),
                             ({"monto": "900", "moneda": "USD"}, "supera lo financiado"),
                             ({"monto": "abc"}, "Escribe el monto")):
            r = caja.post("/caja/cashea/registrar-pago/", {"financiamiento": [fin.pk], **datos}, follow=True)
            self.assertContains(r, texto)
        r = caja.post("/caja/cashea/registrar-pago/", {"monto": "6"}, follow=True)
        self.assertContains(r, "Marca las ventas")
        caja.post("/caja/cashea/registrar-pago/", {"financiamiento": [fin.pk], "monto": "240", "moneda": "LOC"})
        liq = LiquidacionCashea.objects.get()
        self.assertEqual(liq.monto_recibido_usd, Decimal("6.00"))  # 240 Bs ÷ 40

    def test_anular_la_venta_anula_lo_financiado(self):
        f = self.factura()
        self.cobrar_con_cashea(f)
        f.refresh_from_db()
        f.anular_con_devolucion("Error de cobro", self.admin)
        fin = FinanciamientoCashea.objects.get()
        self.assertEqual(fin.estado, FinanciamientoCashea.Estado.ANULADO)
        self.assertFalse(fin.por_devolver)
        datos = CierreCaja.calcular(self.cajero)
        self.assertEqual(datos["financiado_local"], Decimal("0.00"))

    def test_anulada_despues_de_que_cashea_pago_queda_por_devolver(self):
        f = self.factura()
        self.cobrar_con_cashea(f)
        fin = FinanciamientoCashea.objects.get()
        LiquidacionCashea.registrar(financiamientos=[fin], fecha=timezone.localdate(), moneda="USD",
                                    monto=Decimal("6"), referencia="", usuario=self.cajero)
        f.refresh_from_db()
        f.anular_con_devolucion("Cliente devolvió", self.admin)
        fin.refresh_from_db()
        self.assertTrue(fin.por_devolver)
        self.assertContains(self.cliente(self.admin).get("/caja/cashea/"), "Por devolver a Cashea")

    def test_solo_caja_ve_el_modulo(self):
        self.assertEqual(self.cliente(self.mesero).get("/caja/cashea/").status_code, 403)
        self.assertEqual(self.cliente(self.cajero).get("/caja/cashea/").status_code, 403)
        self.assertEqual(self.cliente(self.chef).post("/caja/cashea/registrar-pago/").status_code, 403)
        self.assertEqual(self.cliente(self.admin).get("/caja/cashea/").status_code, 200)

    def test_semana_atrasada(self):
        self.cobrar_con_cashea(self.factura())
        FinanciamientoCashea.objects.update(fecha_pago=timezone.localdate() - timedelta(days=7))
        r = self.cliente(self.admin).get("/caja/cashea/")
        self.assertContains(r, "Atrasado")


class CajaMesasTests(DatosBase):
    def test_la_caja_ve_las_mesas_y_edita_sin_quitar_al_mesero(self):
        pedido = self.pedido(mesa=self.mesa2, items=[(self.agua, 1)])
        caja = self.cliente(self.cajero)
        lista = caja.get("/caja/mesas/")
        self.assertContains(lista, "Mesa 2")
        self.assertContains(lista, "Atiende mesero1")
        comanda = caja.get(f"/mesas/comanda/{pedido.pk}/")
        self.assertContains(comanda, "Mesas en servicio")
        self.assertContains(comanda, "los cambios quedan en su comanda")
        self.assertNotContains(comanda, "Mover de mesa")
        self.assertNotContains(comanda, "Pedir cuenta")
        self.assertContains(comanda, "Emitir factura")
        caja.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.bistec.pk, "cantidad": "2"})
        caja.post(f"/mesas/comanda/{pedido.pk}/enviar/")
        pedido.refresh_from_db()
        self.assertEqual(pedido.mesero, self.mesero)
        self.assertEqual(pedido.detalles.get(producto=self.bistec).cantidad, 2)
        self.assertTrue(pedido.detalles.get(producto=self.bistec).enviado)
        # el mesero sigue viendo su mesa y sus cambios
        self.assertContains(self.cliente(self.mesero).get(f"/mesas/comanda/{pedido.pk}/"), "Bistec")
        # mover la mesa o pedir la cuenta sigue siendo del mesero
        self.assertEqual(caja.post(f"/mesas/comanda/{pedido.pk}/cuenta/").status_code, 403)

    def test_el_cajero_no_ve_inventario(self):
        r = self.cliente(self.cajero).get("/caja/")
        self.assertNotContains(r, 'href="/inventario/"')
        self.assertNotContains(r, "Movimientos de inventario")
        self.assertNotContains(r, "Cashea por cobrar")
        self.assertContains(r, 'href="/caja/mesas/"')
        for ruta in ("/inventario/", "/inventario/movimientos/"):
            self.assertEqual(self.cliente(self.cajero).get(ruta).status_code, 403)
        admin = self.cliente(self.admin).get("/")
        self.assertContains(admin, "Movimientos de inventario")
        self.assertContains(admin, "Cashea por cobrar")
