from datetime import timedelta

from django.utils import timezone

from cocina.models import PedidoKDS
from config.base_pruebas import DatosBase
from mesas.models import DetallePedido, Pedido


class PantallaKDSTests(DatosBase):
    """RF-03: cola de cocina cronológica con actualización de estados."""

    def test_los_pedidos_se_ordenan_por_llegada(self):
        a = self.pedido(self.mesa1, items=[(self.bistec, 1)])
        b = self.pedido(self.mesa2, items=[(self.agua, 1)])
        ahora = timezone.now()
        DetallePedido.objects.filter(pedido=b).update(enviado_en=ahora - timedelta(minutes=10))
        DetallePedido.objects.filter(pedido=a).update(enviado_en=ahora - timedelta(minutes=2))
        self.assertEqual([p.pk for p in PedidoKDS.objects.en_cola()], [b.pk, a.pk])

    def test_cocina_solo_ve_lo_enviado_y_no_entregado(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        entregado = DetallePedido.objects.create(
            pedido=pedido, producto=self.agua, cantidad=1, precio_unitario=1,
            enviado=True, enviado_en=timezone.now(), estado=DetallePedido.Estado.ENTREGADO,
        )
        items = PedidoKDS.objects.en_cola()[0].items_cocina
        self.assertEqual(len(items), 1)
        self.assertNotIn(entregado, items)

    def test_pedido_sin_enviar_no_aparece(self):
        self.pedido(items=[(self.bistec, 1)], enviar=False)
        self.assertEqual(PedidoKDS.objects.en_cola(), [])

    def test_flujo_preparar_y_marcar_listo(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        d = pedido.detalles.get()
        c = self.cliente(self.chef)
        c.post(f"/cocina/item/{d.pk}/", {"accion": "preparar"})
        d.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(d.estado, DetallePedido.Estado.EN_PREPARACION)
        self.assertEqual(pedido.estado, Pedido.Estado.EN_PREPARACION)
        c.post(f"/cocina/item/{d.pk}/", {"accion": "listo"})
        d.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual(d.estado, DetallePedido.Estado.LISTO)
        self.assertEqual(pedido.estado, Pedido.Estado.LISTO)

    def test_un_platillo_listo_no_vuelve_a_preparacion(self):
        d = self.pedido(items=[(self.bistec, 1)]).detalles.get()
        c = self.cliente(self.chef)
        c.post(f"/cocina/item/{d.pk}/", {"accion": "preparar"})
        c.post(f"/cocina/item/{d.pk}/", {"accion": "listo"})
        c.post(f"/cocina/item/{d.pk}/", {"accion": "preparar"})
        d.refresh_from_db()
        self.assertEqual(d.estado, DetallePedido.Estado.LISTO)

    def test_no_se_marca_listo_sin_pasar_por_preparacion(self):
        d = self.pedido(items=[(self.bistec, 1)]).detalles.get()
        self.cliente(self.chef).post(f"/cocina/item/{d.pk}/", {"accion": "listo"})
        d.refresh_from_db()
        self.assertEqual(d.estado, DetallePedido.Estado.PENDIENTE)

    def test_no_se_prepara_un_platillo_no_enviado(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        nuevo = DetallePedido.objects.create(
            pedido=pedido, producto=self.agua, cantidad=1, precio_unitario=1
        )
        self.cliente(self.chef).post(f"/cocina/item/{nuevo.pk}/", {"accion": "preparar"})
        nuevo.refresh_from_db()
        self.assertEqual(nuevo.estado, DetallePedido.Estado.PENDIENTE)

    def test_mesero_entrega_y_el_pedido_sale_de_la_pantalla(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        d = pedido.detalles.get()
        d.marcar_en_preparacion()
        d.marcar_listo()
        self.assertEqual(len(PedidoKDS.objects.en_cola()), 1)
        self.cliente(self.mesero).post(f"/mesas/item/{d.pk}/entregar/")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ENTREGADO)
        self.assertEqual(PedidoKDS.objects.en_cola(), [])

    def test_tiempo_de_espera_y_niveles_de_alerta(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        casos = [(3, "normal"), (16, "demora"), (30, "critico")]
        for minutos, nivel in casos:
            DetallePedido.objects.filter(pedido=pedido).update(enviado_en=timezone.now() - timedelta(minutes=minutos))
            p = PedidoKDS.objects.en_cola()[0]
            self.assertEqual(p.nivel, nivel, minutos)
            self.assertGreaterEqual(p.espera_min, minutos)

    def test_pantalla_y_refresco_parcial(self):
        self.pedido(items=[(self.bistec, 1, "Sin sal")])
        c = self.cliente(self.chef)
        self.assertContains(c.get("/cocina/"), "Sin sal")
        parcial = c.get("/cocina/parcial/")
        self.assertContains(parcial, "Mesa 1")
        self.assertNotContains(parcial, "<html")
