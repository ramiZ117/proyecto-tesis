"""Integridad transaccional: las reglas que la tesis declara en 4.3.11, 4.3.13 y 4.4.

- Una sola comanda abierta por mesa (uniq_pedido_abierto_por_mesa, Pedido.abrir_para).
- Correlativo de factura único, con reintento si dos emisiones chocan.
- Un cierre por tramo de cada cajero (CierreCaja.secuencia).
- El cierre guardado no cambia aunque después se anule una factura.
- No existe el panel técnico de Django: facturas, pagos y cierres solo cambian por sus reglas.
- SQLite en modo IMMEDIATE (las transacciones se atienden en fila).
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from caja.models import CierreCaja, Factura, Pago
from config.base_pruebas import DatosBase
from mesas.models import Mesa, Pedido


class SQLiteConfiguradaTests(DatosBase):
    def test_transacciones_inmediatas(self):
        self.assertEqual(connection.vendor, "sqlite")
        self.assertEqual(connection.transaction_mode, "IMMEDIATE")


class UnaComandaPorMesaTests(DatosBase):
    def test_abrir_para_crea_y_luego_recupera_la_misma_comanda(self):
        pedido, creado = Pedido.abrir_para(self.mesa1.pk, self.mesero)
        self.assertTrue(creado)
        otra_vez, creado = Pedido.abrir_para(self.mesa1.pk, self.mesero2)
        self.assertFalse(creado)
        self.assertEqual(otra_vez.pk, pedido.pk)
        self.mesa1.refresh_from_db()
        self.assertEqual(self.mesa1.estado, Mesa.Estado.OCUPADA)

    def test_la_base_rechaza_una_segunda_comanda_abierta(self):
        Pedido.objects.create(mesa=self.mesa1, mesero=self.mesero)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Pedido.objects.create(mesa=self.mesa1, mesero=self.mesero2)

    def test_cerrada_o_anulada_no_bloquea_una_nueva(self):
        viejo = Pedido.objects.create(mesa=self.mesa1, mesero=self.mesero)
        viejo.anular()
        nuevo, creado = Pedido.abrir_para(self.mesa1.pk, self.mesero2)
        self.assertTrue(creado)
        self.assertNotEqual(nuevo.pk, viejo.pk)

    def test_si_otro_la_abrio_en_el_mismo_instante_se_devuelve_la_suya(self):
        """Simula la carrera: la consulta no ve la comanda, pero la base ya la tiene."""
        ajeno = Pedido.objects.create(mesa=self.mesa1, mesero=self.mesero2)
        with mock.patch.object(Mesa, "pedido_activo", side_effect=[None, ajeno]):
            pedido, creado = Pedido.abrir_para(self.mesa1.pk, self.mesero)
        self.assertFalse(creado)
        self.assertEqual(pedido.pk, ajeno.pk)
        self.assertEqual(Pedido.objects.filter(mesa=self.mesa1).count(), 1)

    def test_por_limpiar_no_se_abre(self):
        self.mesa1.marcar_por_limpiar()
        with self.assertRaisesMessage(ValidationError, "limpiarse"):
            Pedido.abrir_para(self.mesa1.pk, self.mesero)

    def test_dos_meseros_en_la_pantalla_dejan_una_sola_comanda(self):
        self.cliente(self.mesero).post(f"/mesas/abrir/{self.mesa1.pk}/")
        r = self.cliente(self.mesero2).post(f"/mesas/abrir/{self.mesa1.pk}/", follow=True)
        self.assertContains(r, "la atiende mesero1")
        self.assertEqual(Pedido.objects.filter(mesa=self.mesa1).count(), 1)


class NumeroDeFacturaTests(DatosBase):
    def test_correlativo_consecutivo(self):
        f1 = Factura.emitir_desde_pedido(self.pedido(self.mesa1, items=[(self.agua, 1)]), self.cajero)
        f2 = Factura.emitir_desde_pedido(self.pedido(self.mesa2, items=[(self.agua, 1)]), self.cajero2)
        self.assertEqual((f1.numero, f2.numero), ("F-000001", "F-000002"))

    def test_si_el_numero_choca_se_reintenta_con_el_siguiente(self):
        Factura.emitir_desde_pedido(self.pedido(self.mesa1, items=[(self.agua, 1)]), self.cajero)
        pedido = self.pedido(self.mesa2, items=[(self.agua, 1)])
        # El primer intento recibe un número ya usado, como si otro cajero lo hubiera tomado.
        with mock.patch.object(Factura, "_siguiente_numero", side_effect=["F-000001", "F-000002"]):
            factura = Factura.emitir_desde_pedido(pedido, self.cajero2)
        self.assertEqual(factura.numero, "F-000002")
        self.assertEqual(Factura.objects.count(), 2)

    def test_si_todos_los_intentos_chocan_no_se_crea_nada(self):
        Factura.emitir_desde_pedido(self.pedido(self.mesa1, items=[(self.agua, 1)]), self.cajero)
        pedido = self.pedido(self.mesa2, items=[(self.agua, 1)])
        with mock.patch.object(Factura, "_siguiente_numero", return_value="F-000001"):
            with self.assertRaisesMessage(ValidationError, "número de factura"):
                Factura.emitir_desde_pedido(pedido, self.cajero2)
        self.assertEqual(Factura.objects.count(), 1)
        self.mesa2.refresh_from_db()
        self.assertEqual(self.mesa2.estado, Mesa.Estado.OCUPADA)


class CierrePorTramoTests(DatosBase):
    def vender(self, mesa, monto="400"):
        factura = Factura.emitir_desde_pedido(self.pedido(mesa, items=[(self.bistec, 1)]), self.cajero)
        return factura.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal(monto))[1]

    def test_la_secuencia_crece_por_cajero(self):
        self.vender(self.mesa1)
        self.assertEqual(CierreCaja.generar(self.cajero).secuencia, 1)
        self.vender(self.mesa2)
        self.assertEqual(CierreCaja.generar(self.cajero).secuencia, 2)

    def test_dos_cierres_del_mismo_tramo_no_se_guardan(self):
        """Simula el doble clic: el segundo cierre calcula el mismo consecutivo que el primero."""
        self.vender(self.mesa1)
        CierreCaja.generar(self.cajero)
        self.vender(self.mesa2)
        with mock.patch.object(CierreCaja, "ultimo_de", return_value=None):
            with self.assertRaisesMessage(ValidationError, "Ya se generó un cierre"):
                CierreCaja.generar(self.cajero)
        self.assertEqual(CierreCaja.objects.filter(cajero=self.cajero).count(), 1)

    def test_el_cierre_guardado_no_cambia_si_luego_se_devuelve_la_factura(self):
        factura = self.vender(self.mesa1)
        cierre = CierreCaja.generar(self.cajero)
        factura.anular_con_devolucion("Cliente reclamó", self.admin)

        r = self.cliente(self.cajero).get(f"/caja/cierre/{cierre.pk}/")
        datos = r.context["datos"]
        self.assertEqual(datos["total_local"], Decimal("400.00"))
        self.assertEqual(datos["facturas"][0]["estado"], Factura.Estado.PAGADA)
        self.assertContains(r, "Cobrada")
        self.assertNotContains(r, "Devuelta")

        csv = self.cliente(self.cajero).get(f"/caja/cierre/{cierre.pk}/csv/").content.decode("utf-8-sig")
        self.assertIn("F-000001", csv)
        self.assertIn("Cobrada", csv)

    def test_la_pantalla_en_vivo_si_refleja_la_devolucion(self):
        factura = self.vender(self.mesa1)
        factura.anular_con_devolucion("Cliente reclamó", self.admin)
        r = self.cliente(self.cajero).get("/caja/cierre/")
        self.assertContains(r, "Devuelta")


class SinPanelTecnicoTests(DatosBase):
    """El panel técnico de Django ya no existe: todo se administra en las pantallas propias."""

    def test_no_hay_panel_tecnico(self):
        superusuario = User.objects.create_superuser("super", password=self.PASSWORD)
        self.assertEqual(self.cliente(superusuario).get("/admin/").status_code, 404)

    def test_el_estado_de_la_mesa_no_se_edita_a_mano(self):
        r = self.cliente(self.admin).get(f"/gestion/mesas/{self.mesa1.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'name="estado"')
