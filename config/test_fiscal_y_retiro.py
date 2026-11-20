"""IVA, IGTF, factura personalizada, envío por correo y pedidos en línea para retirar."""

import json
import shutil
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from PIL import Image

from caja.models import CierreCaja, Devolucion, Factura, Pago
from config.base_pruebas import DatosBase
from gestion.models import AjustesLocal
from inventario.models import Insumo, MovimientoStock
from menu.mesa import codigo
from mesas.models import DetallePedido, Pedido, PedidoEnLinea

PRIVADO_PRUEBAS = tempfile.mkdtemp(prefix="el_amir_privado_")


def captura(formato="PNG"):
    salida = BytesIO()
    Image.new("RGB", (600, 900), (240, 240, 240)).save(salida, format=formato)
    return SimpleUploadedFile(f"pago.{formato.lower()}", salida.getvalue(), content_type=f"image/{formato.lower()}")


class _Base(DatosBase):
    def factura(self, items=None, **kw):
        pedido = self.pedido(items=items or [(self.bistec, 1)], **kw)  # Bs 400 = $10
        return Factura.emitir_desde_pedido(pedido, self.cajero)


# =============================== IVA ===============================
class IvaTests(_Base):
    def test_los_precios_incluyen_iva_y_se_separa_al_facturar(self):
        f = self.factura(items=[(self.bistec, 1), (self.agua, 1)])  # Bs 440
        self.assertEqual((f.iva_porcentaje, f.base_imponible_local, f.iva_local), (Decimal("16"), Decimal("379.31"), Decimal("60.69")))
        self.assertEqual(f.base_imponible_local + f.iva_local, f.total_local)

    def test_el_descuento_recalcula_el_iva(self):
        f = self.factura()
        f.aplicar_descuento(tipo="porcentaje", valor=Decimal("50"), motivo="Cortesía", usuario=self.cajero)
        f.refresh_from_db()
        self.assertEqual((f.total_local, f.base_imponible_local, f.iva_local), (Decimal("200.00"), Decimal("172.41"), Decimal("27.59")))
        f.quitar_descuento()
        f.refresh_from_db()
        self.assertEqual(f.iva_local, Decimal("55.17"))

    def test_iva_en_la_factura_el_cierre_y_el_panel(self):
        f = self.factura()
        r = self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/")
        self.assertContains(r, "IVA G16,00%")
        self.assertContains(r, "BI G16,00%")
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("400"))
        datos = CierreCaja.calcular(cajero=self.cajero)
        self.assertEqual((datos["iva_local"], datos["base_local"]), (Decimal("55.17"), Decimal("344.83")))
        self.assertContains(self.cliente(self.admin).get("/panel/"), "Impuestos del período")


# =============================== IGTF ===============================
class IgtfTests(_Base):
    def test_divisa_exacta_paga_la_cuenta_mas_el_3_por_ciento(self):
        f = self.factura()  # Bs 400
        pago, f = f.cobrar(metodo=Pago.Metodo.DIVISA, monto=Decimal("10.30"))
        self.assertEqual((pago.monto_local, pago.igtf_local, pago.vuelto_local), (Decimal("400.00"), Decimal("12.00"), Decimal("0.00")))
        self.assertEqual(f.estado, Factura.Estado.PAGADA)
        self.assertEqual(f.total_con_igtf_local, Decimal("412.00"))

    def test_divisa_sin_el_igtf_deja_saldo(self):
        f = self.factura()
        pago, f = f.cobrar(metodo=Pago.Metodo.DIVISA, monto=Decimal("10"))
        self.assertEqual((pago.monto_local, pago.igtf_local), (Decimal("388.35"), Decimal("11.65")))
        self.assertEqual(f.saldo_local, Decimal("11.65"))

    def test_divisa_con_exceso_da_vuelto_despues_del_igtf(self):
        f = self.factura()
        pago, _ = f.cobrar(metodo=Pago.Metodo.DIVISA, monto=Decimal("11"))  # Bs 440
        self.assertEqual((pago.igtf_local, pago.vuelto_local), (Decimal("12.00"), Decimal("28.00")))

    def test_pago_movil_y_tarjeta_no_pagan_igtf(self):
        f = self.factura()
        pago, _ = f.cobrar(metodo=Pago.Metodo.PAGO_MOVIL, monto=Decimal("400"), referencia="1234")
        self.assertEqual(pago.igtf_local, Decimal("0.00"))

    def test_mixto_bolivares_y_divisa(self):
        f = self.factura()
        _, f = f.cobrar_mixto([{"metodo": "PMO", "monto": Decimal("200"), "referencia": "77"},
                               {"metodo": "DIV", "monto": Decimal("5.15")}])
        self.assertEqual(f.estado, Factura.Estado.PAGADA)
        self.assertEqual(f.igtf_local, Decimal("6.00"))

    def test_igtf_en_el_cierre_y_devolucion_en_divisa(self):
        f = self.factura()
        pago, f = f.cobrar(metodo=Pago.Metodo.DIVISA, monto=Decimal("10.30"))
        self.assertEqual(CierreCaja.calcular(cajero=self.cajero)["igtf_local"], Decimal("12.00"))
        f.anular_con_devolucion("Error de cobro", self.cajero)
        dev = Devolucion.objects.get()
        self.assertEqual((dev.moneda, dev.monto), ("USD", Decimal("10.30")))  # se devuelve también el IGTF
        self.assertEqual(CierreCaja.calcular(cajero=self.cajero)["igtf_local"], Decimal("0.00"))

    def test_la_factura_muestra_el_igtf(self):
        f = self.factura()
        self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/pago/", {"metodo": "DIV", "monto_recibido": "10.30"})
        r = self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/")
        self.assertContains(r, "IGTF 3,00%")
        self.assertContains(r, "TOTAL PAGADO CON IGTF")


# =============================== factura personalizada y correo ===============================
class FacturaPersonalizadaTests(_Base):
    DATOS = {"nombre": "Inversiones Amir C.A.", "documento": "j123456789", "direccion": "Av. 5 de Julio, Puerto La Cruz",
             "telefono": "0281-2650000", "correo": "compras@amir.com"}

    def test_por_defecto_es_consumidor_final(self):
        f = self.factura()
        self.assertFalse(f.personalizada)
        self.assertContains(self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/"), "Cliente: CONSUMIDOR FINAL")

    def test_datos_del_cliente_validados(self):
        f = self.factura()
        c = self.cliente(self.cajero)
        for malo, texto in (({"documento": "12345"}, "cédula o el RIF"), ({"nombre": "A"}, "nombre"),
                            ({"direccion": ""}, "dirección"), ({"correo": "no-es-correo"}, "correo")):
            r = c.post(f"/caja/factura/{f.pk}/cliente/", {**self.DATOS, **malo}, follow=True)
            self.assertContains(r, texto)
        f.refresh_from_db()
        self.assertFalse(f.personalizada)
        c.post(f"/caja/factura/{f.pk}/cliente/", self.DATOS)
        f.refresh_from_db()
        self.assertEqual((f.cliente_documento, f.cliente_nombre), ("J-123456789", "Inversiones Amir C.A."))
        r = c.get(f"/caja/factura/{f.pk}/")
        for texto in ("Inversiones Amir C.A.", "J-123456789", "Av. 5 de Julio", "0281-2650000", "compras@amir.com"):
            self.assertContains(r, texto)
        c.post(f"/caja/factura/{f.pk}/cliente/", {"tipo": "generica"})
        f.refresh_from_db()
        self.assertEqual((f.cliente_documento, f.cliente_correo), ("", ""))

    def test_al_cobrar_se_envia_la_factura_por_correo(self):
        f = self.factura()
        f.guardar_datos_cliente({**self.DATOS, "documento": "J-123456789"})
        self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/pago/", {"metodo": "EFE", "monto_recibido": "400"})
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ["compras@amir.com"])
        self.assertIn(f.numero, correo.subject)
        html = correo.alternatives[0][0]
        for texto in ("INVERSIONES AMIR C.A.", "J-123456789", "IVA G16,00%", "BISTEC (G)", "RIF J-317688600"):
            self.assertIn(texto, html)
        self.assertNotIn("SENIAT", html)
        f.refresh_from_db()
        self.assertIsNotNone(f.correo_enviado_en)

    def test_consumidor_final_puede_recibir_la_factura_por_correo(self):
        f = self.factura()
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("400"))
        self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/correo/", {"correo": "cliente@correo.com"})
        self.assertEqual(mail.outbox[0].to, ["cliente@correo.com"])
        self.assertIn("CONSUMIDOR FINAL", mail.outbox[0].body)

    def test_si_el_servicio_de_correo_falla_se_avisa_sin_romper(self):
        f = self.factura()
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("400"))
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("SMTP caído")):
            r = self.cliente(self.cajero).post(f"/caja/factura/{f.pk}/correo/", {"correo": "x@y.com"}, follow=True)
        self.assertContains(r, "No se pudo enviar el correo")
        f.refresh_from_db()
        self.assertIn("SMTP", f.correo_error)

    def test_la_mesa_pide_factura_con_sus_datos_desde_el_menu(self):
        AjustesLocal.objects.update_or_create(pk=1, defaults={"pm_banco": "Banesco", "pm_telefono": "0414-1111222",
                                                               "pm_documento": "J-123456789"})
        cache.clear()
        pedido = self.pedido(mesa=self.mesa2, items=[(self.bistec, 1)])
        pedido.detalles.update(estado=DetallePedido.Estado.ENTREGADO)
        publico = Client()
        cuerpo = {"mesa": 2, "codigo": codigo(2), "metodo": "TAR", "factura": {**self.DATOS, "documento": "V-1"}}
        r = publico.post("/menu/cuenta/", json.dumps(cuerpo), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        cuerpo["factura"]["documento"] = "V-12345678"
        self.assertEqual(publico.post("/menu/cuenta/", json.dumps(cuerpo), content_type="application/json").status_code, 200)
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        self.assertEqual((f.cliente_nombre, f.cliente_documento, f.cliente_correo), ("Inversiones Amir C.A.", "V-12345678", "compras@amir.com"))


# =============================== pedidos para retirar ===============================
@override_settings(PRIVADO_ROOT=PRIVADO_PRUEBAS)
class RetiroTests(_Base):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(PRIVADO_PRUEBAS, ignore_errors=True)

    def setUp(self):
        super().setUp()
        AjustesLocal.objects.update_or_create(pk=1, defaults={
            "pm_banco": "Banesco (0134)", "pm_telefono": "0414-1111222", "pm_documento": "J-123456789",
            "binance_id": "pagos@elamir.com", "retiro_activo": True})
        cache.clear()
        self.publico = Client()
        self.items = [{"tipo": "producto", "id": self.bistec.pk, "cantidad": 2, "nota": "bien cocido"},
                      {"tipo": "producto", "id": self.agua.pk, "cantidad": 1}]

    def cotizar(self, metodo="PMO", items=None):
        return self.publico.post("/menu/retiro/cotizar/", json.dumps({"metodo": metodo, "items": items or self.items}),
                                 content_type="application/json")

    def crear(self, archivo="png", **cambios):
        datos = {"nombre": "María Pérez", "telefono": "0414-5551234", "correo": "maria@correo.com", "metodo": "PMO",
                 "referencia": "00451234", "items": self.items, "monto": "840.00", "nota": "", **cambios}
        cuerpo = {"datos": json.dumps(datos)}
        if archivo == "png":
            cuerpo["comprobante"] = captura()
        elif archivo == "falso":
            cuerpo["comprobante"] = SimpleUploadedFile("x.png", b"esto no es una imagen", content_type="image/png")
        return self.publico.post("/menu/retiro/", cuerpo)

    def test_el_enlace_general_ofrece_retirar_solo_con_pagos_digitales(self):
        r = self.publico.get("/menu/")
        self.assertContains(r, "Pagar y retirar en el local")
        self.assertContains(r, '"metodos_retiro": [{"clave": "PMO"')
        self.assertNotContains(r, '"clave": "EFE", "nombre": "Efectivo en bol\\u00edvares", "ayuda": "Pagas en caja", "digital": false}], "cotizar"')
        AjustesLocal.objects.filter(pk=1).update(pm_banco="", binance_id="")
        cache.clear()
        # sin pagos en línea el cliente igual arma su pedido, y se le indica que llame
        self.assertContains(self.publico.get("/menu/"), "Por ahora no estamos recibiendo pagos en línea")
        AjustesLocal.objects.filter(pk=1).update(retiro_activo=False)
        cache.clear()
        self.assertNotContains(self.publico.get("/menu/"), "Pagar y retirar en el local")
        # con el QR de una mesa no se ofrece retiro
        self.assertNotContains(self.publico.get(f"/menu/?mesa=1&c={codigo(1)}"), "Pagar y retirar en el local")

    def test_cotizacion(self):
        d = self.cotizar().json()
        self.assertEqual((d["monto"], d["en_usd"], d["total_usd"]), ("840.00", False, "21,00"))
        self.assertIn({"etiqueta": "Banco", "valor": "Banesco (0134)"}, d["datos"])
        self.assertEqual(self.cotizar(metodo="BIN").json()["monto"], "21.00")
        self.assertEqual(self.cotizar(metodo="EFE").status_code, 400)
        self.assertEqual(self.cotizar(metodo="ZIN").status_code, 400)  # Zinli sin datos
        self.assertEqual(self.cotizar(items=[{"tipo": "producto", "id": 999, "cantidad": 1}]).status_code, 400)
        Insumo.objects.filter(pk=self.res.pk).update(stock_actual=Decimal("0.6"))  # alcanza para 1 bistec
        self.assertEqual(self.cotizar().status_code, 409)

    def test_validaciones_del_pedido(self):
        casos = [
            ({"nombre": ""}, "nombre"), ({"nombre": "María123"}, "nombre"), ({"telefono": "123"}, "teléfono"),
            ({"correo": "malo"}, "correo"), ({"metodo": "EFE"}, "pago móvil"), ({"referencia": "12"}, "referencia"),
            ({"items": []}, "vacío"), ({"factura": {"nombre": "X", "documento": "1", "direccion": ""}}, "nombre"),
        ]
        for cambio, texto in casos:
            r = self.crear(**cambio)
            self.assertEqual(r.status_code, 400, cambio)
            self.assertIn(texto, r.json()["error"], cambio)
        self.assertIn("comprobante", self.crear(archivo=None).json()["error"])
        self.assertIn("imagen", self.crear(archivo="falso").json()["error"])
        self.assertFalse(PedidoEnLinea.objects.exists())

    def test_si_cambio_el_monto_se_pide_revisar(self):
        r = self.crear(monto="800.00")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["cotizacion"]["monto"], "840.00")

    def test_flujo_completo_pago_cocina_aviso_y_entrega(self):
        r = self.crear()
        self.assertEqual(r.status_code, 200)
        web = PedidoEnLinea.objects.get()
        self.assertEqual((web.codigo, web.monto_local, web.etapa), (r.json()["codigo"], Decimal("840.00"), "verificar"))
        # el comprobante está fuera de /media/ y solo caja lo ve
        self.assertTrue(Path(web.comprobante.path).is_relative_to(Path(PRIVADO_PRUEBAS)))
        self.assertEqual(self.cliente(self.mesero).get(f"/caja/en-linea/{web.pk}/comprobante/").status_code, 403)
        respuesta = self.cliente(self.cajero).get(f"/caja/en-linea/{web.pk}/comprobante/")
        self.assertEqual((respuesta.status_code, respuesta["Content-Type"]), (200, "image/jpeg"))
        # seguimiento público
        estado = self.publico.get(r.json()["url"] + "?json=1").json()
        self.assertEqual(estado["etapa"], "verificar")
        caja = self.cliente(self.cajero)
        self.assertContains(caja.get("/caja/en-linea/"), "Verificar pago")
        self.assertContains(caja.get("/caja/en-linea/"), "00451234")

        caja.post(f"/caja/en-linea/{web.pk}/", {"accion": "aceptar"})
        web.refresh_from_db()
        self.assertEqual(web.etapa, "cocina")
        pedido = web.pedido
        self.assertTrue(pedido.en_linea)
        self.assertFalse(pedido.detalles.filter(enviado=False).exists())
        factura = pedido.facturas.get()
        self.assertEqual((factura.estado, factura.total_local), (Factura.Estado.PAGADA, Decimal("840.00")))
        self.assertEqual(factura.pagos.get().referencia, "00451234")
        self.assertEqual(mail.outbox[0].to, ["maria@correo.com"])
        # va a cocina aunque ya esté pagado, y no aparece en las cuentas de caja ni en la lista del mesero
        self.assertContains(self.cliente(self.chef).get("/cocina/"), "María Pérez")
        self.assertNotContains(caja.get("/caja/"), "María Pérez")
        self.assertNotContains(self.cliente(self.mesero).get("/mesas/"), "María Pérez")
        # el inventario se descontó al cobrar
        self.assertEqual(Insumo.objects.get(pk=self.res.pk).stock_actual, Decimal("9.000"))

        for d in pedido.detalles.all():
            d.marcar_en_preparacion()
            d.marcar_listo()
        web.refresh_from_db()
        self.assertEqual(web.etapa, "listo")
        self.assertEqual(self.publico.get(r.json()["url"] + "?json=1").json()["titulo"], "¡Listo para retirar!")
        lista = caja.get("/caja/en-linea/")
        self.assertContains(lista, "https://wa.me/584145551234")
        caja.post(f"/caja/en-linea/{web.pk}/", {"accion": "avisado"})
        caja.post(f"/caja/en-linea/{web.pk}/", {"accion": "entregar"})
        web.refresh_from_db()
        pedido.refresh_from_db()
        self.assertEqual((web.etapa, pedido.estado), ("entregado", Pedido.Estado.CERRADO))
        self.assertIsNotNone(web.avisado_en)

    def test_pago_en_binance_con_la_tasa_del_pedido(self):
        self.crear(metodo="BIN", monto="21.00")
        web = PedidoEnLinea.objects.get()
        from caja.models import TasaCambio

        TasaCambio.objects.update(activa=False)
        TasaCambio.objects.create(valor=Decimal("45"), activa=True)  # la tasa cambió antes de verificar
        factura = web.aceptar(self.cajero)
        self.assertEqual((factura.estado, factura.tasa_cambio), (Factura.Estado.PAGADA, Decimal("40.0000")))

    def test_rechazo_con_motivo(self):
        self.crear()
        web = PedidoEnLinea.objects.get()
        caja = self.cliente(self.cajero)
        caja.post(f"/caja/en-linea/{web.pk}/", {"accion": "rechazar", "motivo": ""})
        web.refresh_from_db()
        self.assertEqual(web.etapa, "verificar")
        caja.post(f"/caja/en-linea/{web.pk}/", {"accion": "rechazar", "motivo": "El pago no llegó"})
        estado = self.publico.get(f"/menu/retiro/{web.token}/?json=1").json()
        self.assertEqual((estado["etapa"], estado["detalle"]), ("rechazado", "El pago no llegó"))

    def test_sin_inventario_no_se_acepta(self):
        self.crear()
        Insumo.objects.filter(pk=self.res.pk).update(stock_actual=Decimal("0.5"))
        r = self.cliente(self.cajero).post(f"/caja/en-linea/{PedidoEnLinea.objects.get().pk}/", {"accion": "aceptar"}, follow=True)
        self.assertContains(r, "No alcanza el inventario")
        self.assertFalse(Pedido.objects.filter(en_linea=True).exists())

    def test_solo_caja_gestiona_los_pedidos_en_linea(self):
        self.assertEqual(self.cliente(self.mesero).get("/caja/en-linea/").status_code, 403)
        self.assertEqual(self.publico.get("/menu/retiro/no-existe/").status_code, 404)


# =============================== movimientos de inventario ===============================
class MovimientosInventarioTests(_Base):
    def test_cada_movimiento_guarda_antes_y_despues_y_no_se_modifica(self):
        from inventario.servicios import registrar_ajuste, registrar_entrada, registrar_merma

        e = registrar_entrada(self.res.pk, Decimal("5"), usuario=self.cajero, documento="Fact. 0045")
        m = registrar_merma(self.res.pk, Decimal("1"), "VEN", usuario=self.cajero)
        a = registrar_ajuste(self.res.pk, Decimal("13.5"), usuario=self.admin)
        self.assertEqual((e.stock_anterior, e.stock_resultante, e.documento), (Decimal("10"), Decimal("15"), "Fact. 0045"))
        self.assertEqual((m.stock_anterior, m.variacion), (Decimal("15"), Decimal("-1")))
        self.assertEqual((a.stock_anterior, a.variacion), (Decimal("14"), Decimal("-0.5")))
        from django.core.exceptions import ValidationError

        e.nota = "cambiada"
        with self.assertRaises(ValidationError):
            e.save()
        with self.assertRaises(ValidationError):
            e.delete()

    def test_la_venta_deja_la_salida_con_su_factura(self):
        f = self.factura()
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("400"))
        salida = MovimientoStock.objects.get(tipo=MovimientoStock.Tipo.SALIDA)
        self.assertEqual((salida.documento, salida.stock_anterior, salida.stock_resultante),
                         (f"Factura {f.numero}", Decimal("10"), Decimal("9.5")))

    def test_pantalla_de_movimientos_filtra_resume_y_exporta(self):
        from inventario.servicios import registrar_entrada, registrar_merma

        registrar_entrada(self.res.pk, Decimal("2"), usuario=self.cajero, costo_unitario=Decimal("6"))
        registrar_merma(self.pan.pk, Decimal("1"), "DAN", nota="=HACK()", usuario=self.cajero)
        c = self.cliente(self.admin)
        self.assertEqual(self.cliente(self.cajero).get("/inventario/movimientos/").status_code, 403)
        r = c.get("/inventario/movimientos/")
        self.assertEqual(r.context["total"], 2)
        self.assertContains(r, "Auditoría al día")
        self.assertEqual(c.get("/inventario/movimientos/?tipo=MER").context["total"], 1)
        self.assertEqual(c.get(f"/inventario/movimientos/?insumo={self.res.pk}").context["total"], 1)
        self.assertEqual(c.get("/inventario/movimientos/?q=hack").context["total"], 1)
        entrada = next(t for t in r.context["tarjetas"] if t["tipo"] == "ENT")
        self.assertEqual((entrada["n"], entrada["valor"]), (1, Decimal("12.00")))
        r = c.get("/inventario/movimientos/?desde=2026-13-01")
        self.assertEqual(r.status_code, 200)
        r = c.get("/inventario/movimientos/?desde=2026-11-20&hasta=2026-11-01")
        self.assertEqual(r.status_code, 200)
        csv = c.get("/inventario/movimientos/?formato=csv").content.decode("utf-8")
        self.assertIn("Existencia anterior", csv)
        self.assertIn("'=HACK()", csv)  # una nota no puede convertirse en fórmula de Excel
        self.assertEqual(self.cliente(self.mesero).get("/inventario/movimientos/").status_code, 403)

    def test_kardex_cuadra_con_el_stock(self):
        from inventario.servicios import registrar_entrada, registrar_merma

        registrar_entrada(self.res.pk, Decimal("4"), usuario=self.cajero)
        registrar_merma(self.res.pk, Decimal("1.5"), "VEN", usuario=self.cajero)
        r = self.cliente(self.admin).get(f"/inventario/insumo/{self.res.pk}/kardex/")
        k = r.context["k"]
        self.assertEqual((k["inicial"], k["entradas"], k["salidas"], k["final"]),
                         (Decimal("10"), Decimal("4"), Decimal("1.5"), Decimal("12.5")))
        self.assertTrue(r.context["cuadra"])
        self.assertContains(r, "coincide con el stock registrado")
        csv = self.cliente(self.admin).get(f"/inventario/insumo/{self.res.pk}/kardex/?formato=csv").content.decode("utf-8")
        self.assertIn("Existencia inicial", csv)

    def test_la_auditoria_detecta_stock_cambiado_por_fuera(self):
        from django.core.management import call_command
        from io import StringIO

        from inventario.servicios import auditar_inventario, registrar_entrada

        registrar_entrada(self.res.pk, Decimal("1"), usuario=self.cajero)
        self.assertEqual(auditar_inventario(), [])
        Insumo.objects.filter(pk=self.res.pk).update(stock_actual=Decimal("20"))  # cambio sin movimiento
        problemas = auditar_inventario()
        self.assertEqual((problemas[0]["insumo"], problemas[0]["diferencia"]), (self.res, Decimal("9")))
        salida = StringIO()
        call_command("auditar_inventario", stdout=salida)
        self.assertIn("Res", salida.getvalue())
        self.assertContains(self.cliente(self.admin).get("/inventario/movimientos/"), "no cuadra")
        self.assertFalse(self.cliente(self.admin).get(f"/inventario/insumo/{self.res.pk}/kardex/").context["cuadra"])
