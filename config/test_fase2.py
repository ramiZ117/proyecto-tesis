"""Fase 2: pedidos para llevar, salón, inventario ampliado, cocina por estación, panel y validaciones."""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from caja.models import Factura, Pago, TasaCambio
from cocina.models import PedidoKDS
from config.base_pruebas import DatosBase
from config.panel import indicadores
from gestion.forms import InsumoForm
from inventario.models import Categoria, Insumo, MovimientoStock, Producto, Proveedor
from inventario.servicios import (
    costos_de_platos,
    estado_insumos,
    lista_compra,
    registrar_ajuste,
    registrar_entrada,
    registrar_merma,
)
from mesas.models import DetallePedido, Mesa, Pedido


def _cobrar(pedido, cajero, metodo=Pago.Metodo.EFECTIVO):
    factura = Factura.emitir_desde_pedido(pedido, cajero)
    return factura.cobrar(metodo=metodo, monto=factura.total_local)[1]


class ParaLlevarTests(DatosBase):
    def test_exige_nombre_del_cliente(self):
        for nombre in ("", "   "):
            with self.assertRaisesMessage(ValidationError, "nombre del cliente"):
                Pedido.crear_para_llevar(nombre, self.mesero)
        r = self.cliente(self.mesero).post("/mesas/llevar/nuevo/", {"cliente": "  "}, follow=True)
        self.assertContains(r, "nombre del cliente")
        self.assertFalse(Pedido.objects.filter(tipo=Pedido.Tipo.LLEVAR).exists())

    def test_se_crea_sin_mesa_y_con_codigo(self):
        r = self.cliente(self.mesero).post("/mesas/llevar/nuevo/", {"cliente": "  María   Pérez "}, follow=True)
        pedido = Pedido.objects.get(tipo=Pedido.Tipo.LLEVAR)
        self.assertIsNone(pedido.mesa)
        self.assertEqual(pedido.cliente, "María Pérez")
        self.assertEqual(pedido.codigo, f"L-{pedido.pk:03d}")
        self.assertContains(r, pedido.codigo)

    def test_la_base_exige_mesa_en_pedidos_de_salon_y_ninguna_para_llevar(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Pedido.objects.create(tipo=Pedido.Tipo.MESA, mesero=self.mesero)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Pedido.objects.create(tipo=Pedido.Tipo.LLEVAR, mesa=self.mesa1, cliente="X", mesero=self.mesero)

    def test_ciclo_completo_sin_mesa(self):
        c = self.cliente(self.mesero)
        c.post("/mesas/llevar/nuevo/", {"cliente": "Carlos"})
        pedido = Pedido.objects.get(tipo=Pedido.Tipo.LLEVAR)
        c.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.hamburguesa.pk, "cantidad": 2})
        with self.assertRaisesMessage(ValidationError, "se cobran primero"):
            pedido.enviar_a_cocina()
        with self.assertRaisesMessage(ValidationError, "directo en caja"):
            pedido.solicitar_cuenta()
        self.assertNotContains(self.cliente(self.chef).get("/cocina/"), f"Llevar {pedido.codigo}")
        caja = self.cliente(self.cajero).get("/caja/")
        self.assertContains(caja, f"Llevar {pedido.codigo}")
        factura = _cobrar(pedido, self.cajero)
        pedido.refresh_from_db()
        # Cobrado en el mostrador: recién entonces va a cocina, y sigue ahí hasta que se entrega.
        self.assertEqual(factura.estado, Factura.Estado.PAGADA)
        self.assertNotEqual(pedido.estado, Pedido.Estado.CERRADO)
        self.assertContains(self.cliente(self.chef).get("/cocina/"), f"Llevar {pedido.codigo}")
        for d in pedido.detalles.all():
            d.marcar_en_preparacion()
            d.marcar_listo()
            d.marcar_entregado()
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.CERRADO)
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("9.600"))  # receta descontada
        self.assertContains(self.cliente(self.cajero).get(f"/caja/factura/{factura.pk}/"), "Pedido para llevar cobrado y enviado a cocina")

    def test_anular_un_pedido_para_llevar(self):
        pedido = Pedido.crear_para_llevar("Ana", self.mesero)
        r = self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/anular/", follow=True)
        self.assertContains(r, "anulado")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ANULADO)

    def test_otro_mesero_no_lo_modifica(self):
        pedido = Pedido.crear_para_llevar("Ana", self.mesero)
        r = self.cliente(self.mesero2).get(f"/mesas/comanda/{pedido.pk}/", follow=True)
        self.assertContains(r, "la atiende mesero1")


class SalonTests(DatosBase):
    def test_tocar_el_mismo_plato_suma_al_que_no_se_envio(self):
        c = self.cliente(self.mesero)
        c.post(f"/mesas/abrir/{self.mesa1.pk}/")
        pedido = Pedido.objects.get(mesa=self.mesa1)
        for _ in range(2):
            c.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.agua.pk})
        c.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.agua.pk, "observaciones": "Sin hielo"})
        self.assertEqual(sorted(pedido.detalles.values_list("cantidad", flat=True)), [1, 2])

    def test_cantidad_invalida_no_se_agrega(self):
        c = self.cliente(self.mesero)
        c.post(f"/mesas/abrir/{self.mesa1.pk}/")
        pedido = Pedido.objects.get(mesa=self.mesa1)
        for cantidad in ("0", "-2", "abc", "100"):
            r = c.post(f"/mesas/comanda/{pedido.pk}/agregar/", {"producto_id": self.agua.pk, "cantidad": cantidad}, follow=True)
            self.assertContains(r, "cantidad válida", msg_prefix=cantidad)
        self.assertFalse(pedido.detalles.exists())

    def test_sumar_y_restar_antes_de_enviar(self):
        pedido = self.pedido(items=[(self.agua, 1)], enviar=False)
        d = pedido.detalles.get()
        c = self.cliente(self.mesero)
        c.post(f"/mesas/item/{d.pk}/cantidad/", {"delta": "1"})
        d.refresh_from_db()
        self.assertEqual(d.cantidad, 2)
        c.post(f"/mesas/item/{d.pk}/cantidad/", {"delta": "-1"})
        c.post(f"/mesas/item/{d.pk}/cantidad/", {"delta": "-1"})
        self.assertFalse(DetallePedido.objects.filter(pk=d.pk).exists())

    def test_no_se_cambia_la_cantidad_de_lo_ya_enviado(self):
        d = self.pedido(items=[(self.agua, 1)]).detalles.get()
        with self.assertRaisesMessage(ValidationError, "ya se envió"):
            d.cambiar_cantidad(1)
        r = self.cliente(self.mesero).post(f"/mesas/item/{d.pk}/cantidad/", {"delta": "1"}, follow=True)
        self.assertContains(r, "ya se envió")

    def test_sumar_respeta_las_porciones(self):
        Insumo.objects.filter(pk=self.pan.pk).update(stock_actual=Decimal("1"))
        d = self.pedido(items=[(self.hamburguesa, 1)], enviar=False).detalles.get()
        r = self.cliente(self.mesero).post(f"/mesas/item/{d.pk}/cantidad/", {"delta": "1"}, follow=True)
        self.assertContains(r, "No quedan porciones")
        d.refresh_from_db()
        self.assertEqual(d.cantidad, 1)

    def test_mover_a_una_mesa_disponible(self):
        pedido = self.pedido(self.mesa1, items=[(self.agua, 1)])
        r = self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/mover/", {"mesa_id": self.mesa2.pk}, follow=True)
        self.assertContains(r, "movida de Mesa 1 a Mesa 2")
        pedido.refresh_from_db()
        self.recargar(self.mesa1, self.mesa2)
        self.assertEqual(pedido.mesa, self.mesa2)
        self.assertEqual((self.mesa1.estado, self.mesa2.estado), (Mesa.Estado.DISPONIBLE, Mesa.Estado.OCUPADA))

    def test_no_se_mueve_a_una_mesa_ocupada_ni_a_la_misma(self):
        pedido = self.pedido(self.mesa1, items=[(self.agua, 1)])
        self.pedido(self.mesa2, items=[(self.agua, 1)])
        with self.assertRaisesMessage(ValidationError, "no está disponible"):
            pedido.mover_a(self.mesa2.pk)
        with self.assertRaisesMessage(ValidationError, "otra mesa"):
            pedido.mover_a(self.mesa1.pk)
        r = self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/mover/", {"mesa_id": "x"}, follow=True)
        self.assertContains(r, "mesa de destino")
        pedido.refresh_from_db()
        self.assertEqual(pedido.mesa, self.mesa1)

    def test_mover_conserva_el_estado_por_facturar(self):
        pedido = self.pedido(self.mesa1, items=[(self.agua, 1)])
        pedido.solicitar_cuenta()
        pedido.mover_a(self.mesa3.pk)
        self.mesa3.refresh_from_db()
        self.assertEqual(self.mesa3.estado, Mesa.Estado.POR_FACTURAR)

    def test_nota_de_la_comanda(self):
        pedido = self.pedido(items=[(self.agua, 1)])
        c = self.cliente(self.mesero)
        c.post(f"/mesas/comanda/{pedido.pk}/nota/", {"notas": "  Cumpleaños   en la mesa " + "x" * 300})
        pedido.refresh_from_db()
        self.assertTrue(pedido.notas.startswith("Cumpleaños en la mesa"))
        self.assertEqual(len(pedido.notas), 250)
        self.assertContains(self.cliente(self.chef).get("/cocina/"), "Comanda: Cumpleaños")
        Factura.emitir_desde_pedido(pedido, self.cajero)
        r = c.post(f"/mesas/comanda/{pedido.pk}/nota/", {"notas": "otra"}, follow=True)
        self.assertContains(r, "ya fue facturada")

    def test_mapa_marca_la_mesa_elegida(self):
        pedido = self.pedido(self.mesa2, items=[(self.agua, 1)])
        r = self.cliente(self.mesero).get(f"/mesas/parcial/?sel={self.mesa2.pk}")
        self.assertContains(r, 'aria-current="true"')
        self.assertContains(self.cliente(self.mesero).get(f"/mesas/comanda/{pedido.pk}/"), f"?sel={self.mesa2.pk}")
        self.assertEqual(self.cliente(self.mesero).get("/mesas/parcial/?sel=abc").status_code, 200)


class InventarioAmpliadoTests(DatosBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.proveedor = Proveedor.objects.create(nombre="Carnicería Al Sham")
        Insumo.objects.filter(pk=cls.res.pk).update(
            punto_reposicion=Decimal("4"), stock_maximo=Decimal("20"), costo_unitario=Decimal("6.00"), proveedor=cls.proveedor
        )
        Insumo.objects.filter(pk=cls.pan.pk).update(punto_reposicion=Decimal("2"), stock_maximo=Decimal("10"), costo_unitario=Decimal("0.25"))

    def test_merma_baja_el_stock_y_queda_valorizada(self):
        mov = registrar_merma(self.res.pk, Decimal("1.5"), MovimientoStock.Motivo.VENCIDO, usuario=self.admin)
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("8.500"))
        self.assertEqual((mov.tipo, mov.motivo, mov.usuario, mov.valor_usd), ("MER", "VEN", self.admin, Decimal("9.00")))

    def test_merma_invalida(self):
        with self.assertRaisesMessage(ValidationError, "supera el stock"):
            registrar_merma(self.res.pk, Decimal("11"), "VEN")
        with self.assertRaisesMessage(ValidationError, "motivo"):
            registrar_merma(self.res.pk, Decimal("1"), "XXX")
        with self.assertRaisesMessage(ValidationError, "motivo"):
            registrar_merma(self.res.pk, Decimal("1"), MovimientoStock.Motivo.CONTEO)
        with self.assertRaisesMessage(ValidationError, "mayor a cero"):
            registrar_merma(self.res.pk, Decimal("0"), "VEN")
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("10.000"))
        self.assertFalse(MovimientoStock.objects.exists())

    def test_ajuste_por_conteo(self):
        mov = registrar_ajuste(self.res.pk, Decimal("7.25"), usuario=self.admin)
        self.res.refresh_from_db()
        self.assertEqual((self.res.stock_actual, mov.cantidad, mov.tipo), (Decimal("7.250"), Decimal("2.750"), "AJU"))
        self.assertIn("10 → 7.25", mov.nota)
        with self.assertRaisesMessage(ValidationError, "coincide"):
            registrar_ajuste(self.res.pk, Decimal("7.25"))
        registrar_ajuste(self.res.pk, Decimal("0"))  # se puede contar cero
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("0"))

    def test_entrada_actualiza_el_costo_si_se_indica(self):
        registrar_entrada(self.res.pk, Decimal("5"), costo_unitario=Decimal("6.50"), usuario=self.cajero)
        self.res.refresh_from_db()
        self.assertEqual((self.res.stock_actual, self.res.costo_unitario), (Decimal("15.000"), Decimal("6.5000")))
        with self.assertRaisesMessage(ValidationError, "mayor a cero"):
            registrar_entrada(self.res.pk, Decimal("-1"))

    def test_niveles_reponer_y_critico(self):
        Insumo.objects.filter(pk=self.res.pk).update(stock_actual=Decimal("3.5"))
        Insumo.objects.filter(pk=self.pan.pk).update(stock_actual=Decimal("0.5"))
        niveles = {i.pk: i.nivel for i in estado_insumos()}
        self.assertEqual(niveles, {self.res.pk: "reponer", self.pan.pk: "critico"})
        compra = {f["insumo"].pk: f for f in lista_compra()}
        self.assertEqual(compra[self.res.pk]["cantidad"], Decimal("16.500"))
        self.assertEqual(compra[self.res.pk]["costo_usd"], Decimal("99.00"))
        self.assertEqual(compra[self.res.pk]["proveedor"], "Carnicería Al Sham")

    def test_costo_y_margen_por_plato(self):
        filas = {f["producto"].pk: f for f in costos_de_platos()}
        # Bistec: 0.5 kg × $6 = $3 de costo sobre $10 → 70 %.
        self.assertEqual((filas[self.bistec.pk]["costo_usd"], filas[self.bistec.pk]["margen"]), (Decimal("3.00"), 70))
        self.assertNotIn(self.agua.pk, filas)  # sin receta no hay costo

    def test_pantalla_registra_merma_y_valida(self):
        c = self.cliente(self.admin)
        r = c.post("/inventario/movimiento/", {"tipo": "MER", "insumo_id": self.res.pk, "cantidad": "1,5", "motivo": "DAN"}, follow=True)
        self.assertContains(r, "Merma registrada")
        for datos, texto in (
            ({"tipo": "MER", "insumo_id": self.res.pk, "cantidad": "abc", "motivo": "DAN"}, "cantidad válida"),
            ({"tipo": "MER", "insumo_id": self.res.pk, "cantidad": "99", "motivo": "DAN"}, "supera el stock"),
            ({"tipo": "MER", "insumo_id": self.res.pk, "cantidad": "1", "motivo": ""}, "motivo"),
            ({"tipo": "ENT", "insumo_id": "9999", "cantidad": "1"}, "Elige un insumo"),
            ({"tipo": "ENT", "insumo_id": self.res.pk, "cantidad": "0"}, "mayor a cero"),
        ):
            self.assertContains(c.post("/inventario/movimiento/", datos, follow=True), texto, msg_prefix=str(datos))
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("8.500"))

    def test_solo_el_admin_ajusta_por_conteo(self):
        r = self.cliente(self.cajero).post("/inventario/movimiento/", {"tipo": "AJU", "insumo_id": self.res.pk, "cantidad": "3"})
        self.assertEqual(r.status_code, 403)  # el inventario es solo del administrador
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("10.000"))
        r = self.cliente(self.admin).post("/inventario/movimiento/", {"tipo": "AJU", "insumo_id": self.res.pk, "cantidad": "3"}, follow=True)
        self.assertContains(r, "Conteo guardado")
        self.assertEqual(self.cliente(self.mesero).post("/inventario/movimiento/", {"tipo": "ENT"}).status_code, 403)

    def test_pantalla_muestra_resumen_lista_y_margenes(self):
        Insumo.objects.filter(pk=self.res.pk).update(stock_actual=Decimal("3.5"))
        r = self.cliente(self.admin).get("/inventario/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["por_reponer"], 1)
        self.assertContains(r, "Lista de compra")
        self.assertContains(r, "Carnicería Al Sham")
        self.assertContains(r, "Costo y margen por plato")

    def test_exportar_lista_de_compra(self):
        Insumo.objects.filter(pk=self.res.pk).update(stock_actual=Decimal("3.5"))
        r = self.cliente(self.admin).get("/inventario/compra.csv")
        texto = r.content.decode("utf-8-sig")
        self.assertTrue(r["Content-Type"].startswith("text/csv"))
        self.assertIn("Carnicería Al Sham;Res;16.5;kg;99.00", texto)

    def test_anular_lo_preparado_queda_como_merma(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        pedido.detalles.get().marcar_en_preparacion()
        pedido.anular(forzar=True)
        mov = MovimientoStock.objects.get()
        self.assertEqual((mov.tipo, mov.motivo), (MovimientoStock.Tipo.MERMA, MovimientoStock.Motivo.ANULACION))

    def test_el_panel_tecnico_valida_los_niveles(self):
        base = {"nombre": "Prueba", "grupo": "OTR", "unidad": "KG", "stock_actual": "1", "costo_unitario": "1", "activo": True}
        form = InsumoForm(data={**base, "stock_minimo": "5", "punto_reposicion": "2", "stock_maximo": "10"})
        self.assertIn("punto de reposición", str(form.errors))
        form = InsumoForm(data={**base, "stock_minimo": "1", "punto_reposicion": "5", "stock_maximo": "3"})
        self.assertIn("stock máximo", str(form.errors))
        form = InsumoForm(data={**base, "stock_minimo": "1", "punto_reposicion": "2", "stock_maximo": "3"})
        self.assertTrue(form.is_valid(), form.errors)


class CocinaPorEstacionTests(DatosBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        bebidas = Categoria.objects.create(nombre="Bebidas", estacion=Categoria.Estacion.BEBIDAS)
        cls.te = Producto.objects.create(categoria=bebidas, nombre="Té árabe", precio_usd=Decimal("1.50"))

    def test_filtra_por_estacion(self):
        self.pedido(items=[(self.bistec, 1), (self.te, 2)])
        c = self.cliente(self.chef)
        todo = c.get("/cocina/")
        self.assertContains(todo, "Bistec")
        self.assertContains(todo, "Té árabe")
        bebidas = c.get("/cocina/?estacion=BEB")
        self.assertNotContains(bebidas, "Bistec")
        self.assertContains(bebidas, "Té árabe")
        self.assertContains(bebidas, "/cocina/parcial/?estacion=BEB")
        self.assertEqual(c.get("/cocina/?estacion=XYZ").context["estacion"], "")

    def test_el_tablero_ubica_cada_pedido_en_su_etapa(self):
        a = self.pedido(self.mesa1, items=[(self.bistec, 1)])
        b = self.pedido(self.mesa2, items=[(self.agua, 1)])
        b.detalles.get().marcar_en_preparacion()
        etapas = {p.pk: p.etapa for p in PedidoKDS.objects.en_cola()}
        self.assertEqual(etapas, {a.pk: "pen", b.pk: "prep"})
        d = b.detalles.get()
        d.marcar_listo()
        d.refresh_from_db()
        self.assertIsNotNone(d.listo_en)
        columnas = {col["clave"]: [p.pk for p in col["pedidos"]] for col in self.cliente(self.chef).get("/cocina/").context["columnas"]}
        self.assertEqual(columnas, {"pen": [a.pk], "prep": [], "listo": [b.pk]})

    def test_tras_una_accion_vuelve_a_la_misma_estacion(self):
        d = self.pedido(items=[(self.te, 1)]).detalles.get()
        r = self.cliente(self.chef).post(f"/cocina/item/{d.pk}/", {"accion": "preparar", "estacion": "BEB"})
        self.assertEqual(r["Location"], "/cocina/?estacion=BEB")
        r = self.cliente(self.chef).post(f"/cocina/item/{d.pk}/", {"accion": "listo", "estacion": "<script>"})
        self.assertEqual(r["Location"], "/cocina/")


class PanelTests(DatosBase):
    def test_solo_el_administrador(self):
        self.assertEqual(self.cliente(self.cajero).get("/panel/").status_code, 403)
        self.assertEqual(self.cliente(self.admin).get("/").status_code, 200)

    def test_indicadores_reflejan_las_ventas(self):
        pedido = self.pedido(items=[(self.bistec, 2), (self.agua, 1)])
        d = pedido.detalles.get(producto=self.bistec)
        d.marcar_en_preparacion()
        d.marcar_listo()
        _cobrar(pedido, self.cajero)
        datos = indicadores("hoy")
        self.assertEqual(datos["ventas_usd"], Decimal("21.00"))
        self.assertEqual(datos["facturas"], 1)
        self.assertEqual(datos["ticket_usd"], Decimal("21.00"))
        self.assertEqual(datos["top"][0]["nombre"], "Bistec")
        self.assertEqual([m["nombre"] for m in datos["metodos"]], ["Efectivo (Bs.)"])
        self.assertEqual(datos["metodos"][0]["pct"], 100)
        self.assertIsNotNone(datos["minutos_cocina"])
        self.assertEqual(sum(1 for b in datos["barras"] if b["pico"]), 1)

    def test_periodos_y_valores_invalidos(self):
        c = self.cliente(self.admin)
        for periodo in ("hoy", "semana", "mes", "cualquiera"):
            r = c.get(f"/panel/?periodo={periodo}")
            self.assertEqual(r.status_code, 200, periodo)
        self.assertEqual(r.context["periodo"], "hoy")
        self.assertEqual(len(c.get("/panel/?periodo=semana").context["barras"]), 7)

    def test_compara_con_el_periodo_anterior(self):
        pedido = self.pedido(items=[(self.agua, 1)])
        factura = _cobrar(pedido, self.cajero)
        ayer = timezone.now() - timedelta(days=1)
        Pago.objects.filter(factura=factura).update(registrado_en=ayer)
        Factura.objects.filter(pk=factura.pk).update(pagada_en=ayer)
        _cobrar(self.pedido(self.mesa2, items=[(self.agua, 2)]), self.cajero)
        self.assertEqual(indicadores("hoy")["delta"], 100)


class TasaYValidacionesTests(DatosBase):
    def test_un_cambio_grande_de_tasa_pide_confirmacion(self):
        c = self.cliente(self.cajero)
        r = c.post("/caja/tasa/guardar/", {"valor": "400"}, follow=True)
        self.assertContains(r, "Confírmala")
        self.assertContains(r, "Sí, publicar esta tasa")
        self.assertEqual(TasaCambio.vigente().valor, Decimal("40.0000"))
        c.post("/caja/tasa/guardar/", {"valor": "400", "confirmado": "1"})
        self.assertEqual(TasaCambio.vigente().valor, Decimal("400.0000"))

    def test_un_cambio_pequeno_se_publica_directo(self):
        self.cliente(self.cajero).post("/caja/tasa/guardar/", {"valor": "42,5"})
        self.assertEqual(TasaCambio.vigente().valor, Decimal("42.5000"))
        self.assertEqual(TasaCambio.objects.filter(activa=True).count(), 1)

    def test_tasa_invalida(self):
        c = self.cliente(self.cajero)
        for valor in ("", "0", "-5", "abc", "1e9", "NaN"):
            r = c.post("/caja/tasa/guardar/", {"valor": valor}, follow=True)
            self.assertContains(r, "mayor a cero", msg_prefix=valor)
        self.assertEqual(TasaCambio.objects.count(), 1)

    def test_el_pago_rechaza_montos_invalidos(self):
        factura = Factura.emitir_desde_pedido(self.pedido(items=[(self.agua, 1)]), self.cajero)
        c = self.cliente(self.cajero)
        for monto in ("", "0", "-10", "abc", "99999999"):
            r = c.post(f"/caja/factura/{factura.pk}/pago/", {"metodo": "EFE", "monto_recibido": monto}, follow=True)
            self.assertContains(r, "monto válido", msg_prefix=monto)
        r = c.post(f"/caja/factura/{factura.pk}/pago/", {"metodo": "PMO", "monto_recibido": "50"}, follow=True)
        self.assertContains(r, "no se entrega vuelto")  # sin referencia sí se acepta, pero no puede sobrar
        r = c.post(f"/caja/factura/{factura.pk}/pago/", {"metodo": "ZZZ", "monto_recibido": "40"}, follow=True)
        self.assertContains(r, "Método de pago no válido")
        self.assertFalse(factura.pagos.exists())

    def test_saldar_muestra_el_vuelto(self):
        factura = Factura.emitir_desde_pedido(self.pedido(items=[(self.agua, 1)]), self.cajero)
        r = self.cliente(self.cajero).post(f"/caja/factura/{factura.pk}/pago/", {"metodo": "EFE", "monto_recibido": "50"}, follow=True)
        self.assertContains(r, "Cuenta saldada")
        self.assertContains(r, "Entregar vuelto: Bs 10,00")

    def test_la_interfaz_usa_la_marca_y_el_modo_cocina(self):
        r = self.cliente(self.chef).get("/cocina/")
        self.assertContains(r, 'class="modo-cocina"')
        self.assertContains(r, "logo-el-amir-icono.jpg")
        login = self.client.get("/login/")
        self.assertContains(login, "El Amir")
        self.assertContains(login, 'required')
