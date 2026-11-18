"""Ganancia real: ventas sin IVA, costo de lo vendido, mermas, gastos fijos y extraordinarios; inversión."""

from datetime import date
from decimal import Decimal

from django.utils import timezone

from caja.models import Factura, Pago
from config.base_pruebas import DatosBase
from gestion.finanzas import gastos_del_mes, inversion_actual, rango_mes, rentabilidad_por_plato, resultado_mes
from gestion.models import GastoExtraordinario, GastoFijo
from inventario.models import Insumo
from inventario.servicios import registrar_merma


class FinanzasTests(DatosBase):
    """Datos base: tasa 40, IVA 16 %. Bistec $10 (receta 0,5 kg de Res); Res cuesta $8/kg → costo $4."""

    def setUp(self):
        super().setUp()
        Insumo.objects.filter(pk=self.res.pk).update(costo_unitario=Decimal("8"))
        self.hoy = timezone.localdate()

    def vender_bistec(self, cantidad=1):
        pedido = self.pedido(items=[(self.bistec, cantidad)])
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=f.total_local)
        pedido.mesa.marcar_disponible()
        return f

    def test_ganancia_bruta_y_por_plato(self):
        self.vender_bistec(2)  # $20 con IVA → $17,24 sin IVA; costo 2 × $4 = $8
        r = resultado_mes(self.hoy.year, self.hoy.month)
        self.assertEqual((r["ventas"], r["costo_ventas"], r["ganancia_bruta"]), (Decimal("17.24"), Decimal("8.00"), Decimal("9.24")))
        self.assertEqual(r["margen_bruto"], 54)
        _, _, desde, hasta = rango_mes(self.hoy.year, self.hoy.month)
        plato = rentabilidad_por_plato(desde, hasta)[0]
        self.assertEqual((plato["producto"], plato["unidades"], plato["ventas"], plato["costo"], plato["ganancia"]),
                         (self.bistec, 2, Decimal("17.24"), Decimal("8.00"), Decimal("9.24")))

    def test_el_descuento_baja_la_ganancia_del_plato(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        f.aplicar_descuento(tipo="porcentaje", valor=Decimal("50"), motivo="Cortesía", usuario=self.cajero)
        f.refresh_from_db()
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=f.total_local)
        _, _, desde, hasta = rango_mes(self.hoy.year, self.hoy.month)
        plato = rentabilidad_por_plato(desde, hasta)[0]
        self.assertEqual((plato["ventas"], plato["ganancia"]), (Decimal("4.31"), Decimal("0.31")))

    def test_ganancia_neta_descuenta_mermas_y_gastos(self):
        self.vender_bistec(2)  # bruta 9,24
        registrar_merma(self.res.pk, Decimal("0.5"), "VEN", usuario=self.cajero)  # $4 perdidos
        GastoFijo.objects.create(nombre="Alquiler", monto_usd=Decimal("3"), desde=self.hoy.replace(day=1))
        GastoFijo.objects.create(nombre="Viejo", monto_usd=Decimal("50"), desde=date(2020, 1, 1), hasta=date(2020, 12, 31))
        GastoExtraordinario.objects.create(descripcion="Plancha", monto_usd=Decimal("12"), meses=12, fecha=self.hoy)
        r = resultado_mes(self.hoy.year, self.hoy.month)
        self.assertEqual((r["mermas"], r["total_fijos"], r["total_extraordinarios"]), (Decimal("4.00"), Decimal("3"), Decimal("1.00")))
        self.assertEqual(r["ganancia_neta"], Decimal("1.24"))  # 9,24 − 4 − 3 − 1

    def test_gasto_repartido_en_cuotas_exactas(self):
        g = GastoExtraordinario.objects.create(descripcion="Nevera", monto_usd=Decimal("100"), meses=3, fecha=date(2026, 11, 5))
        cuotas = [g.cuota_en(2026, 11), g.cuota_en(2026, 12), g.cuota_en(2027, 1), g.cuota_en(2027, 2), g.cuota_en(2026, 10)]
        self.assertEqual(cuotas, [Decimal("33.33"), Decimal("33.33"), Decimal("33.34"), Decimal("0.00"), Decimal("0.00")])
        self.assertEqual(gastos_del_mes(2027, 1)["total_extraordinarios"], Decimal("33.34"))

    def test_total_invertido(self):
        GastoExtraordinario.objects.create(descripcion="Horno", monto_usd=Decimal("500"), categoria="EQU", fecha=self.hoy)
        GastoExtraordinario.objects.create(descripcion="Arreglo", monto_usd=Decimal("50"), categoria="REP", fecha=self.hoy)
        inv = inversion_actual()
        # inventario: Res 10 kg × $8 = $80 (Pan sin costo) · equipos: solo el horno
        self.assertEqual((inv["inventario"], inv["equipos"], inv["total"]), (Decimal("80.00"), Decimal("500.00"), Decimal("580.00")))

    def test_pantalla_y_formularios(self):
        self.vender_bistec()
        admin = self.cliente(self.admin)
        r = admin.get("/gestion/finanzas/")
        for texto in ("Ganancia bruta", "Ganancia neta", "Total invertido hoy", "Bistec", "Gastos fijos mensuales"):
            self.assertContains(r, texto)
        self.assertEqual(self.cliente(self.cajero).get("/gestion/finanzas/").status_code, 403)
        # validaciones
        r = admin.post("/gestion/finanzas/gasto-fijo/nuevo/", {"nombre": "Luz", "categoria": "SER", "monto_usd": "0", "desde": "2026-11-01"})
        self.assertContains(r, "mayor a cero")
        r = admin.post("/gestion/finanzas/gasto-fijo/nuevo/", {"nombre": "Luz", "categoria": "SER", "monto_usd": "40",
                                                                "desde": "2026-11-01", "hasta": "2026-10-01"})
        self.assertContains(r, "No puede terminar antes")
        admin.post("/gestion/finanzas/gasto-fijo/nuevo/", {"nombre": "Luz", "categoria": "SER", "monto_usd": "40", "desde": "2026-11-01"})
        self.assertTrue(GastoFijo.objects.filter(nombre="Luz").exists())
        r = admin.post("/gestion/finanzas/gasto/nuevo/", {"fecha": "2999-01-01", "descripcion": "Máquina", "categoria": "EQU",
                                                          "monto_usd": "900", "meses": "12"})
        self.assertContains(r, "no puede ser futura")
        r = admin.post("/gestion/finanzas/gasto/nuevo/", {"fecha": self.hoy.isoformat(), "descripcion": "Máquina", "categoria": "EQU",
                                                          "monto_usd": "900", "meses": "99"})
        self.assertContains(r, "Entre 1 y 60")
        admin.post("/gestion/finanzas/gasto/nuevo/", {"fecha": self.hoy.isoformat(), "descripcion": "Máquina de shawarma",
                                                      "categoria": "EQU", "monto_usd": "900", "meses": "12"})
        gasto = GastoExtraordinario.objects.get()
        self.assertEqual((gasto.registrado_por, gasto.cuota_usd), (self.admin, Decimal("75.00")))
        csv = admin.get("/gestion/finanzas/?formato=csv").content.decode("utf-8")
        self.assertIn("Ganancia neta", csv)
        admin.post(f"/gestion/finanzas/extra/{gasto.pk}/eliminar/")
        self.assertFalse(GastoExtraordinario.objects.exists())
        self.assertEqual(admin.get("/gestion/finanzas/?mes=2999-01").status_code, 200)  # mes inválido → mes actual

    def test_el_panel_muestra_la_ganancia(self):
        self.vender_bistec()
        self.assertContains(self.cliente(self.admin).get("/panel/"), "Ganancia bruta")
