"""La numeración de facturas no se traba aunque los números existentes no sigan el orden de creación."""

from caja.models import Factura
from config.base_pruebas import DatosBase


class NumeracionTests(DatosBase):
    def test_numeros_desordenados_no_impiden_facturar(self):
        # Base con datos importados o corregidos: la última fila no tiene el número más alto.
        viejo = self.pedido(mesa=self.mesa1, items=[(self.agua, 1)])
        f1 = Factura.emitir_desde_pedido(viejo, self.cajero)
        Factura.objects.filter(pk=f1.pk).update(numero="F-000007")
        otro = self.pedido(mesa=self.mesa2, items=[(self.agua, 1)])
        f2 = Factura.emitir_desde_pedido(otro, self.cajero)
        Factura.objects.filter(pk=f2.pk).update(numero="F-000003")
        nuevo = self.pedido(mesa=self.mesa3, items=[(self.agua, 1)])
        f3 = Factura.emitir_desde_pedido(nuevo, self.cajero)
        self.assertEqual(f3.numero, "F-000008")  # sigue al mayor, no al último guardado

    def test_numeros_con_otro_formato_no_rompen(self):
        p = self.pedido(mesa=self.mesa1, items=[(self.agua, 1)])
        f = Factura.emitir_desde_pedido(p, self.cajero)
        Factura.objects.filter(pk=f.pk).update(numero="PRUEBA")
        p2 = self.pedido(mesa=self.mesa2, items=[(self.agua, 1)])
        self.assertEqual(Factura.emitir_desde_pedido(p2, self.cajero).numero, "F-000001")
