"""Pago mixto, descuentos, propinas, tasa del BCV, promociones y pedidos desde el menú de la mesa."""

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import Client, override_settings

from caja.models import CierreCaja, Factura, Pago, Propina, TasaCambio
from caja import tasa_bcv
from config.base_pruebas import DatosBase
from inventario.models import Insumo, Promocion, PromocionItem
from menu.mesa import codigo
from mesas.models import DetallePedido, Mesa, Pedido, SolicitudCliente


class _ConFactura(DatosBase):
    def factura(self, items=None, mesero=None):
        pedido = self.pedido(items=items or [(self.bistec, 1), (self.agua, 2)], mesero=mesero)
        return Factura.emitir_desde_pedido(pedido, self.cajero)  # 12 USD = Bs 480


class PagoMixtoTests(_ConFactura):
    def test_pago_mixto_cierra_la_cuenta_y_el_vuelto_sale_del_efectivo(self):
        f = self.factura()
        r = self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/pago/", {
            "metodo": [Pago.Metodo.EFECTIVO, Pago.Metodo.PAGO_MOVIL, Pago.Metodo.DIVISA],
            "monto_recibido": ["100", "200", "5"],  # Bs 100 + Bs 200 + $5 (Bs 200) = Bs 500
            "referencia": ["", "1234", ""],
        })
        self.assertEqual(r.status_code, 302)
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.PAGADA)
        self.assertEqual(f.pagos.count(), 3)
        self.assertEqual(sum(p.monto_local for p in f.pagos.all()), Decimal("480.00"))

    def test_si_un_pago_sobra_no_se_registra_ninguno(self):
        f = self.factura()
        self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/pago/", {
            "metodo": [Pago.Metodo.PAGO_MOVIL, Pago.Metodo.TARJETA],
            "monto_recibido": ["480", "50"], "referencia": ["1", "2"],
        })
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.ABIERTA)
        self.assertFalse(f.pagos.exists())

    def test_fila_extra_vacia_se_ignora(self):
        f = self.factura()
        self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/pago/", {
            "metodo": [Pago.Metodo.EFECTIVO, Pago.Metodo.TARJETA], "monto_recibido": ["480", ""], "referencia": ["", ""],
        })
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.PAGADA)
        self.assertEqual(f.pagos.count(), 1)


class DescuentoTests(_ConFactura):
    def test_descuento_en_porcentaje_baja_el_total(self):
        f = self.factura()
        self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/descuento/", {"tipo": "porcentaje", "valor": "10", "motivo": "Cliente frecuente"})
        f.refresh_from_db()
        self.assertEqual(f.descuento_local, Decimal("48.00"))
        self.assertEqual(f.total_local, Decimal("432.00"))
        self.assertEqual(f.descuento_por, self.cajero)

    def test_descuento_exige_motivo_y_no_supera_el_total(self):
        f = self.factura()
        c = self.cliente(self.cajero)
        c.post(f"/caja/factura/{f.pk}/descuento/", {"tipo": "usd", "valor": "2", "motivo": ""})
        c.post(f"/caja/factura/{f.pk}/descuento/", {"tipo": "usd", "valor": "500", "motivo": "x"})
        f.refresh_from_db()
        self.assertEqual(f.descuento_local, Decimal("0.00"))

    def test_no_se_descuenta_con_pagos_y_se_puede_quitar(self):
        f = self.factura()
        c = self.cliente(self.cajero)
        c.post(f"/caja/factura/{f.pk}/descuento/", {"tipo": "bs", "valor": "80", "motivo": "Cortesía"})
        c.post(f"/caja/factura/{f.pk}/descuento/", {"quitar": "1"})
        f.refresh_from_db()
        self.assertEqual(f.total_local, Decimal("480.00"))
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("100"))
        c.post(f"/caja/factura/{f.pk}/descuento/", {"tipo": "bs", "valor": "80", "motivo": "Cortesía"})
        f.refresh_from_db()
        self.assertEqual(f.descuento_local, Decimal("0.00"))

    def test_el_cierre_reporta_los_descuentos(self):
        f = self.factura()
        f.aplicar_descuento(tipo="porcentaje", valor=Decimal("50"), motivo="Promo", usuario=self.cajero)
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("240"))
        datos = CierreCaja.calcular(cajero=self.cajero)
        self.assertEqual(datos["descuentos_local"], Decimal("240.00"))
        self.assertEqual(datos["total_local"], Decimal("240.00"))


class PropinaTests(_ConFactura):
    def test_propina_en_el_cobro_queda_a_favor_del_mesero_que_atendio(self):
        f = self.factura(mesero=self.mesero2)
        self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/pago/", {
            "metodo": Pago.Metodo.PAGO_MOVIL, "monto_recibido": "480", "referencia": "9",
            "propina_monto": "2", "propina_metodo": Pago.Metodo.DIVISA,
        })
        propina = Propina.objects.get()
        self.assertEqual((propina.mesero, propina.monto, propina.monto_local, propina.pagada), (self.mesero2, Decimal("2.00"), Decimal("80.00"), False))
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.PAGADA)
        self.assertEqual(sum(p.monto_local for p in f.pagos.all()), Decimal("480.00"))  # la propina no es venta

    def test_propina_invalida_no_cobra_nada(self):
        f = self.factura()
        self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/pago/", {
            "metodo": Pago.Metodo.EFECTIVO, "monto_recibido": "480", "propina_monto": "-5",
        })
        f.refresh_from_db()
        self.assertEqual(f.estado, Factura.Estado.ABIERTA)
        self.assertFalse(Propina.objects.exists())

    def test_saldo_por_mesero_y_marcar_pagadas(self):
        for mesero, monto in ((self.mesero, "40"), (self.mesero, "60"), (self.mesero2, "20")):
            f = self.factura(mesero=mesero)
            f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("480"))
            Propina.registrar(f, metodo=Pago.Metodo.EFECTIVO, monto=Decimal(monto), usuario=self.cajero)
            f.pedido.mesa.marcar_disponible()
        caja = self.cliente(self.cajero)
        r = caja.get("/caja/propinas/")
        saldos = {s["mesero"].username: s["total_local"] for s in r.context["saldos"]}
        self.assertEqual(saldos, {"mesero1": Decimal("100.00"), "mesero2": Decimal("20.00")})
        caja.post(f"/caja/propinas/{self.mesero.pk}/pagar/")
        self.assertFalse(Propina.objects.filter(mesero=self.mesero, pagada=False).exists())
        self.assertTrue(Propina.objects.filter(mesero=self.mesero2, pagada=False).exists())
        self.assertEqual(Propina.objects.filter(mesero=self.mesero).first().pagada_por, self.cajero)

    def test_el_mesero_ve_solo_sus_propinas_y_no_puede_marcarlas(self):
        f = self.factura(mesero=self.mesero2)
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("480"))
        Propina.registrar(f, metodo=Pago.Metodo.EFECTIVO, monto=Decimal("30"), usuario=self.cajero)
        r = self.cliente(self.mesero).get("/caja/propinas/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.context["saldos"]), [])
        r = self.cliente(self.mesero2).get("/caja/propinas/")
        self.assertEqual(len(r.context["saldos"]), 1)
        self.assertNotContains(r, "Marcar pagadas")
        self.assertEqual(self.cliente(self.mesero2).post(f"/caja/propinas/{self.mesero2.pk}/pagar/").status_code, 403)

    def test_el_cierre_separa_las_propinas(self):
        f = self.factura()
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("480"))
        Propina.registrar(f, metodo=Pago.Metodo.EFECTIVO, monto=Decimal("50"), usuario=self.cajero)
        datos = CierreCaja.calcular(cajero=self.cajero)
        self.assertEqual(datos["propinas_local"], Decimal("50.00"))
        self.assertEqual(datos["total_local"], Decimal("480.00"))


class TasaBcvTests(DatosBase):
    def test_publica_la_tasa_del_bcv(self):
        with mock.patch.object(tasa_bcv, "consultar", return_value=(Decimal("41.5000"), date(2026, 11, 17))):
            resultado = tasa_bcv.actualizar()
        self.assertTrue(resultado.actualizada)
        vigente = TasaCambio.objects.get(activa=True)
        self.assertEqual((vigente.valor, vigente.fuente, vigente.fecha_valor), (Decimal("41.5000"), "BCV", date(2026, 11, 17)))
        # los precios en bolívares siguen a la tasa nueva
        self.assertEqual(self.bistec.precio_local, Decimal("415.00"))

    def test_sin_cambios_o_cambio_brusco_no_publica(self):
        with mock.patch.object(tasa_bcv, "consultar", return_value=(Decimal("41.0000"), None)):
            tasa_bcv.actualizar()
        with mock.patch.object(tasa_bcv, "consultar", return_value=(Decimal("41.0000"), None)):
            self.assertFalse(tasa_bcv.actualizar().actualizada)
        with mock.patch.object(tasa_bcv, "consultar", return_value=(Decimal("410.0000"), None)):
            self.assertFalse(tasa_bcv.actualizar().actualizada)
            self.assertTrue(tasa_bcv.actualizar(forzar=True).actualizada)
        self.assertEqual(TasaCambio.objects.get(activa=True).valor, Decimal("410.0000"))

    def test_si_la_fuente_falla_se_conserva_la_tasa(self):
        with mock.patch.object(tasa_bcv, "consultar", side_effect=OSError("sin internet")):
            resultado = tasa_bcv.actualizar()
        self.assertFalse(resultado.actualizada)
        self.assertEqual(TasaCambio.objects.get(activa=True).valor, Decimal("40.0000"))

    def test_boton_de_caja(self):
        with mock.patch.object(tasa_bcv, "consultar", return_value=(Decimal("42.0000"), None)):
            r = self.cliente(self.cajero).post("/caja/tasa/bcv/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(TasaCambio.objects.get(activa=True).valor, Decimal("42.0000"))
        self.assertEqual(self.cliente(self.mesero).post("/caja/tasa/bcv/").status_code, 403)

    @override_settings(TASA_BCV_AUTOMATICA=True)
    def test_revision_automatica_una_vez_por_periodo(self):
        cache.clear()
        with mock.patch.object(tasa_bcv.threading, "Thread") as hilo:
            tasa_bcv.revisar_en_segundo_plano()
            tasa_bcv.revisar_en_segundo_plano()
        self.assertEqual(hilo.call_count, 1)


class _ConPromo(DatosBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.promo = Promocion.objects.create(nombre="Dúo", precio_usd=Decimal("15.00"), descripcion="Para dos")
        PromocionItem.objects.create(promocion=cls.promo, producto=cls.hamburguesa, cantidad=2)
        PromocionItem.objects.create(promocion=cls.promo, producto=cls.agua, cantidad=1)  # normal: $17


class PromocionTests(_ConPromo):
    def test_gestion_crea_una_promocion(self):
        r = self.cliente(self.admin).post("/gestion/promociones/nueva/", {
            "nombre": "Combo bistec", "precio_usd": "10.50", "descripcion": "", "activa": "on", "orden": "0",
            "items-TOTAL_FORMS": "3", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
            "items-0-producto": self.bistec.pk, "items-0-cantidad": "1",
            "items-1-producto": self.agua.pk, "items-1-cantidad": "1",
        })
        self.assertEqual(r.status_code, 302)
        promo = Promocion.objects.get(nombre="Combo bistec")
        self.assertEqual((promo.precio_normal_usd, promo.ahorro_usd), (Decimal("11.00"), Decimal("0.50")))

    def test_gestion_valida_platos_y_fechas(self):
        base = {"nombre": "Mala", "precio_usd": "5", "orden": "0", "activa": "on",
                "items-TOTAL_FORMS": "2", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000"}
        c = self.cliente(self.admin)
        r = c.post("/gestion/promociones/nueva/", base)
        self.assertContains(r, "Agrega al menos un plato")
        r = c.post("/gestion/promociones/nueva/", {**base, "items-0-producto": self.agua.pk, "items-0-cantidad": "1",
                                                    "items-1-producto": self.agua.pk, "items-1-cantidad": "1"})
        self.assertContains(r, "aparece dos veces")
        r = c.post("/gestion/promociones/nueva/", {**base, "items-0-producto": self.agua.pk, "items-0-cantidad": "1",
                                                    "desde": "2026-12-10", "hasta": "2026-12-01"})
        self.assertContains(r, "no puede ser anterior")
        self.assertFalse(Promocion.objects.filter(nombre="Mala").exists())
        self.assertEqual(self.cliente(self.cajero).get("/gestion/promociones/").status_code, 403)

    def test_agregar_promocion_a_la_comanda_reparte_el_precio(self):
        pedido = self.pedido(items=[])
        self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/promocion/", {"promocion_id": self.promo.pk})
        lineas = list(pedido.detalles.all())
        self.assertEqual(len(lineas), 3)  # una línea por unidad: cocina las ve como platos normales
        self.assertEqual(sum(d.subtotal for d in lineas), Decimal("600.00"))  # $15 a tasa 40
        self.assertTrue(all(d.promocion_id == self.promo.pk and not d.enviado for d in lineas))
        # quitar una línea quita el combo completo
        self.cliente(self.mesero).post(f"/mesas/item/{lineas[0].pk}/quitar/")
        self.assertFalse(pedido.detalles.exclude(estado=DetallePedido.Estado.ANULADO).exists())

    def test_promocion_vencida_u_oculta_no_se_agrega(self):
        pedido = self.pedido(items=[])
        Promocion.objects.filter(pk=self.promo.pk).update(hasta=date.today() - timedelta(days=1))
        self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/promocion/", {"promocion_id": self.promo.pk})
        self.assertFalse(pedido.detalles.exists())

    def test_promocion_sin_stock_no_se_agrega(self):
        Insumo.objects.filter(pk=self.pan.pk).update(stock_actual=1)  # alcanza para 1 hamburguesa, el combo lleva 2
        pedido = self.pedido(items=[])
        self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/promocion/", {"promocion_id": self.promo.pk})
        self.assertFalse(pedido.detalles.exists())

    def test_el_menu_publico_muestra_las_promociones(self):
        r = Client().get("/menu/")
        self.assertContains(r, "Promociones")
        self.assertContains(r, "Dúo")
        self.assertContains(r, "Ahorras $2,00")
        Promocion.objects.filter(pk=self.promo.pk).update(activa=False)
        self.assertNotContains(Client().get("/menu/"), "Ahorras")


class PedidoDesdeLaMesaTests(_ConPromo):
    def setUp(self):
        super().setUp()
        self.publico = Client(enforce_csrf_checks=False)

    def solicitar(self, items=(), mesa=2, cod=None, nota=""):
        return self.publico.post("/menu/solicitar/", json.dumps({
            "mesa": mesa, "codigo": codigo(mesa) if cod is None else cod, "items": list(items), "nota": nota,
        }), content_type="application/json")

    def test_el_qr_de_la_mesa_habilita_el_pedido(self):
        r = self.publico.get(f"/menu/?mesa=2&c={codigo(2)}")
        self.assertContains(r, "Solicitar mesero")
        self.assertContains(r, "Agregar a mi pedido")
        r = self.publico.get("/menu/?mesa=2&c=falso")
        self.assertNotContains(r, "Solicitar mesero")
        qr = self.cliente(self.admin).get("/gestion/menu-digital/")
        self.assertTrue(any(i["url"].endswith(f"?mesa=2&c={codigo(2)}") for i in qr.context["mesas_qr"]))

    def test_sin_codigo_valido_no_llama_a_la_mesa(self):
        self.assertEqual(self.solicitar(cod="otro").status_code, 403)
        self.assertEqual(self.solicitar(mesa=99, cod=codigo(99)).status_code, 404)
        self.assertFalse(SolicitudCliente.objects.exists())

    def test_rechaza_platos_inexistentes_o_cantidades_raras(self):
        for items in ([{"tipo": "producto", "id": 9999, "cantidad": 1}],
                      [{"tipo": "producto", "id": self.bistec.pk, "cantidad": 0}],
                      [{"tipo": "producto", "id": self.bistec.pk, "cantidad": 21}],
                      [{"tipo": "otro", "id": self.bistec.pk, "cantidad": 1}],
                      "nada"):
            self.assertEqual(self.solicitar(items).status_code, 400, items)
        self.assertFalse(SolicitudCliente.objects.exists())

    def test_flujo_completo_cliente_mesero_cocina(self):
        r = self.solicitar([
            {"tipo": "producto", "id": self.bistec.pk, "cantidad": 2, "nota": "bien cocido"},
            {"tipo": "promocion", "id": self.promo.pk, "cantidad": 1},
        ], nota="Somos 3")
        self.assertEqual(r.status_code, 200)
        solicitud = SolicitudCliente.objects.get()
        self.assertEqual(solicitud.mesa, self.mesa2)
        # otro teléfono de la misma mesa suma a la misma solicitud
        self.solicitar([{"tipo": "producto", "id": self.bistec.pk, "cantidad": 1, "nota": "bien cocido"}])
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.items[0]["cantidad"], 3)
        self.assertEqual(SolicitudCliente.objects.count(), 1)

        # el mapa del salón la muestra
        mesero = self.cliente(self.mesero)
        self.assertContains(mesero.get("/mesas/parcial/"), "Pedido del menú")
        self.assertTrue(self.publico.get(f"/menu/solicitud/estado/?mesa=2&c={codigo(2)}").json()["pendiente"])

        # el mesero toma la mesa: ve el pedido del cliente y lo carga a la comanda
        r = mesero.post(f"/mesas/abrir/{self.mesa2.pk}/", follow=True)
        self.assertContains(r, "Pedido hecho desde el menú")
        pedido = Pedido.objects.get(mesa=self.mesa2)
        mesero.post(f"/mesas/solicitud/{solicitud.pk}/cargar/")
        solicitud.refresh_from_db()
        self.assertEqual((solicitud.estado, solicitud.pedido, solicitud.atendida_por), ("CAR", pedido, self.mesero))
        detalles = pedido.detalles.all()
        self.assertEqual(sum(d.cantidad for d in detalles if not d.promocion_id), 3)
        self.assertEqual(detalles.filter(promocion=self.promo).count(), 3)
        self.assertFalse(detalles.filter(enviado=True).exists())  # nada va a cocina sin que el mesero lo confirme
        pedido.refresh_from_db()
        self.assertIn("Somos 3", pedido.notas)
        self.assertFalse(self.publico.get(f"/menu/solicitud/estado/?mesa=2&c={codigo(2)}").json()["pendiente"])

        # el mesero ajusta y envía
        mesero.post(f"/mesas/comanda/{pedido.pk}/enviar/")
        self.assertFalse(pedido.detalles.filter(enviado=False).exists())

    def test_llamar_al_mesero_sin_pedido(self):
        self.assertEqual(self.solicitar([]).status_code, 200)
        solicitud = SolicitudCliente.objects.get()
        self.assertTrue(solicitud.es_solo_llamado)
        mesero = self.cliente(self.mesero)
        self.assertContains(mesero.get("/mesas/parcial/"), "Llama al mesero")
        mesero.post(f"/mesas/solicitud/{solicitud.pk}/descartar/")
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, SolicitudCliente.Estado.DESCARTADA)
        self.assertNotContains(mesero.get("/mesas/parcial/"), "Llama al mesero")

    def test_otro_mesero_no_carga_la_solicitud_de_una_mesa_ajena(self):
        self.pedido(mesa=self.mesa2, mesero=self.mesero2, items=[(self.agua, 1)])
        self.solicitar([{"tipo": "producto", "id": self.bistec.pk, "cantidad": 1}])
        solicitud = SolicitudCliente.objects.get()
        self.cliente(self.mesero).post(f"/mesas/solicitud/{solicitud.pk}/cargar/")
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, SolicitudCliente.Estado.PENDIENTE)
        self.cliente(self.mesero2).post(f"/mesas/solicitud/{solicitud.pk}/cargar/")
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, SolicitudCliente.Estado.CARGADA)

    def test_plato_que_se_agota_se_avisa_y_no_se_carga(self):
        self.solicitar([{"tipo": "producto", "id": self.hamburguesa.pk, "cantidad": 3}])
        Insumo.objects.filter(pk=self.pan.pk).update(stock_actual=1)
        solicitud = SolicitudCliente.objects.get()
        mesero = self.cliente(self.mesero)
        r = mesero.post(f"/mesas/solicitud/{solicitud.pk}/cargar/", follow=True)
        self.assertContains(r, "solo quedaban 1")
        pedido = Pedido.objects.get(mesa=self.mesa2)
        self.assertEqual(pedido.detalles.get().cantidad, 1)

    def test_limite_de_solicitudes_por_hora(self):
        for _ in range(20):
            self.solicitar([])
        self.assertEqual(self.solicitar([]).status_code, 429)
