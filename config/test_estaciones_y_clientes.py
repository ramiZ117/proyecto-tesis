"""Shawarmero y bebidas de nevera, alarma por anulación, clientes guardados, factura en ticket y nota de cierre."""

import json
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import override_settings

from caja.models import CierreCaja, ClienteFiscal, Factura, Pago
from caja.ticket import numero_factura, ticket_cierre, ticket_factura
from cocina import tiempos
from cocina.models import AvisoCocina, PedidoKDS
from config.base_pruebas import DatosBase
from config.permisos import GRUPO_SHAWARMA
from inventario.models import Categoria, Producto
from mesas.models import DetallePedido, Pedido


class _Estaciones(DatosBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.shawarmero = cls._usuario("shawarmero1", GRUPO_SHAWARMA)
        shawarmas = Categoria.objects.create(nombre="Shawarmas", estacion=Categoria.Estacion.SHAWARMA)
        bebidas = Categoria.objects.create(nombre="Bebidas", estacion=Categoria.Estacion.BEBIDAS)
        cls.shawarma = Producto.objects.create(categoria=shawarmas, nombre="Shawarma de pollo", precio_usd=Decimal("6"),
                                               minutos_preparacion=8)
        cls.refresco = Producto.objects.create(categoria=bebidas, nombre="Refresco", precio_usd=Decimal("1.50"),
                                               bebida=Producto.Bebida.NEVERA)
        cls.jugo = Producto.objects.create(categoria=bebidas, nombre="Jugo de naranja", precio_usd=Decimal("2"),
                                           bebida=Producto.Bebida.COCINA)


# =============================== shawarmero y bebidas ===============================
class EstacionesTests(_Estaciones):
    def test_cada_area_ve_solo_lo_suyo(self):
        self.pedido(items=[(self.bistec, 1), (self.shawarma, 2), (self.refresco, 1), (self.jugo, 1)])
        cocina = self.cliente(self.chef).get("/cocina/")
        self.assertContains(cocina, "Bistec")
        self.assertContains(cocina, "Jugo de naranja")  # bebida preparada: va a la cocina
        self.assertNotContains(cocina, "Shawarma de pollo")
        self.assertNotContains(cocina, "Refresco")
        sha = self.cliente(self.shawarmero).get("/cocina/shawarma/")
        self.assertContains(sha, "Shawarma de pollo")
        self.assertNotContains(sha, "Bistec")
        self.assertNotContains(sha, "Refresco")
        self.assertContains(sha, "Shawarmas · El Amir")

    def test_permisos_de_cada_pantalla(self):
        self.assertEqual(self.cliente(self.chef).get("/cocina/shawarma/").status_code, 403)
        self.assertEqual(self.cliente(self.shawarmero).get("/cocina/").status_code, 403)
        self.assertEqual(self.cliente(self.mesero).get("/cocina/shawarma/").status_code, 403)
        self.assertEqual(self.cliente(self.admin).get("/cocina/shawarma/").status_code, 200)
        self.assertRedirects(self.cliente(self.shawarmero).get("/"), "/cocina/shawarma/", fetch_redirect_response=False)

    def test_cada_area_avanza_solo_sus_platos(self):
        pedido = self.pedido(items=[(self.bistec, 1), (self.shawarma, 1)])
        sha = self.cliente(self.shawarmero)
        # el shawarmero no puede usar el botón de la cocina
        self.assertEqual(sha.post(f"/cocina/pedido/{pedido.pk}/", {"accion": "empezar", "area": "COC"}).status_code, 403)
        sha.post(f"/cocina/pedido/{pedido.pk}/", {"accion": "empezar", "area": "SHA"})
        estados = dict(pedido.detalles.values_list("producto__nombre", "estado"))
        self.assertEqual(estados, {"Bistec": "PEN", "Shawarma de pollo": "PRE"})
        sha.post(f"/cocina/pedido/{pedido.pk}/", {"accion": "listo", "area": "SHA"})
        self.cliente(self.chef).post(f"/cocina/pedido/{pedido.pk}/", {"accion": "empezar", "area": "COC"})
        estados = dict(pedido.detalles.values_list("producto__nombre", "estado"))
        self.assertEqual(estados, {"Bistec": "PRE", "Shawarma de pollo": "LIS"})
        # plato suelto de otra área: prohibido
        bistec = pedido.detalles.get(producto=self.bistec)
        self.assertEqual(sha.post(f"/cocina/item/{bistec.pk}/", {"accion": "listo"}).status_code, 403)

    def test_bebida_de_nevera_no_pasa_por_cocina(self):
        pedido = self.pedido(items=[(self.refresco, 2), (self.bistec, 1)])
        refresco = pedido.detalles.get(producto=self.refresco)
        self.assertEqual(refresco.estado, DetallePedido.Estado.LISTO)
        self.assertTrue(refresco.enviado)
        self.assertEqual([d.producto.nombre for p in PedidoKDS.objects.en_cola() for d in p.items_cocina], ["Bistec"])
        comanda = self.cliente(self.mesero).get(f"/mesas/comanda/{pedido.pk}/")
        self.assertContains(comanda, "Nevera")
        self.cliente(self.mesero).post(f"/mesas/item/{refresco.pk}/entregar/")
        refresco.refresh_from_db()
        self.assertEqual(refresco.estado, DetallePedido.Estado.ENTREGADO)

    def test_bebida_de_nevera_se_puede_quitar_si_no_se_entrego(self):
        pedido = self.pedido(items=[(self.refresco, 1), (self.bistec, 1)])
        refresco = pedido.detalles.get(producto=self.refresco)
        self.cliente(self.mesero).post(f"/mesas/item/{refresco.pk}/quitar/")
        refresco.refresh_from_db()
        self.assertEqual(refresco.estado, DetallePedido.Estado.ANULADO)
        self.assertFalse(AvisoCocina.objects.exists())  # la cocina nunca la vio: no hay alarma

    def test_tiempos_separados_por_area(self):
        self.pedido(mesa=self.mesa1, items=[(self.bistec, 3)])  # cocina cargada
        pedido = self.pedido(mesa=self.mesa2, items=[(self.shawarma, 1), (self.refresco, 1)])
        # el shawarma no espera a la cocina y el refresco no suma tiempo
        self.assertEqual(tiempos.estimar_pedido(pedido), 8)

    def test_formulario_del_plato_tiene_el_selector_de_bebida(self):
        r = self.cliente(self.admin).get(f"/gestion/carta/plato/{self.jugo.pk}/")
        self.assertContains(r, "¿Es bebida?")
        self.assertContains(r, "Bebida de nevera (la entrega el mesero)")

    def test_el_rol_shawarmero_se_puede_asignar(self):
        r = self.cliente(self.admin).get("/usuarios/nuevo/")
        self.assertContains(r, "Shawarmero")


# =============================== alarma cuando anulan ===============================
class AvisosDeAnulacionTests(_Estaciones):
    def test_anular_el_pedido_avisa_a_cada_area(self):
        pedido = self.pedido(items=[(self.bistec, 1), (self.shawarma, 2), (self.refresco, 1)])
        self.cliente(self.mesero).post(f"/mesas/comanda/{pedido.pk}/anular/")
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado, Pedido.Estado.ANULADO)
        avisos = {a.area: a.texto for a in AvisoCocina.objects.all()}
        self.assertEqual(set(avisos), {"COC", "SHA"})
        self.assertIn("ANULÓ el pedido", avisos["COC"])
        self.assertIn("1× Bistec", avisos["COC"])
        self.assertIn("2× Shawarma de pollo", avisos["SHA"])
        self.assertNotIn("Refresco", avisos["COC"] + avisos["SHA"])
        cocina = self.cliente(self.chef).get("/cocina/parcial/")
        aviso = AvisoCocina.objects.get(area="COC")
        self.assertContains(cocina, f'data-aviso-ultimo="{aviso.pk}"')
        self.assertContains(cocina, "Entendido")
        self.assertNotContains(self.cliente(self.chef).get("/cocina/"), "Shawarma de pollo")

    def test_entendido_quita_el_aviso(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        pedido.detalles.get().cancelar()
        aviso = AvisoCocina.objects.get()
        self.assertIn("quitó: 1× Bistec", aviso.texto)
        # el shawarmero no puede atender avisos de la cocina
        self.assertEqual(self.cliente(self.shawarmero).post(f"/cocina/aviso/{aviso.pk}/").status_code, 403)
        self.cliente(self.chef).post(f"/cocina/aviso/{aviso.pk}/")
        aviso.refresh_from_db()
        self.assertEqual(aviso.atendido_por, self.chef)
        self.assertContains(self.cliente(self.chef).get("/cocina/parcial/"), 'data-aviso-ultimo="0"')

    def test_lo_que_no_llego_a_cocina_no_suena(self):
        pedido = self.pedido(items=[(self.bistec, 1)], enviar=False)
        pedido.anular()
        self.assertFalse(AvisoCocina.objects.exists())


# =============================== clientes guardados ===============================
class ClientesGuardadosTests(_Estaciones):
    DATOS = {"nombre": "Servicios Navieramar C.A.", "documento": "J302401046", "direccion": "Av. Municipal, Puerto La Cruz",
             "telefono": "0281-2671234", "correo": "pagos@navieramar.com"}

    def factura(self, mesa=None):
        return Factura.emitir_desde_pedido(self.pedido(mesa=mesa, items=[(self.bistec, 1)]), self.cajero)

    def test_la_factura_con_datos_guarda_al_cliente(self):
        f = self.factura()
        caja = self.cliente(self.cajero)
        caja.post(f"/caja/factura/{f.pk}/cliente/", self.DATOS)
        cliente = ClienteFiscal.objects.get()
        self.assertEqual((cliente.documento, cliente.nombre), ("J-302401046", "Servicios Navieramar C.A."))
        # en otra visita se encuentra por parte de la cédula o por el nombre
        for texto in ("30240", "j-3024", "navier"):
            datos = caja.get("/caja/clientes/buscar/", {"q": texto}).json()["clientes"]
            self.assertEqual([c["documento"] for c in datos], ["J-302401046"], texto)
        self.assertEqual(caja.get("/caja/clientes/buscar/", {"q": "zz"}).json()["clientes"], [])
        # si cambia un dato se actualiza, sin duplicarlo
        otra = self.factura(mesa=self.mesa2)
        caja.post(f"/caja/factura/{otra.pk}/cliente/", {**self.DATOS, "telefono": "0414-0000000"})
        self.assertEqual(ClienteFiscal.objects.get().telefono, "0414-0000000")
        r = caja.get("/caja/clientes/")
        self.assertContains(r, "Servicios Navieramar C.A.")
        self.assertContains(r, '<td class="der">2</td>', html=False)

    def test_solo_caja_consulta_los_clientes(self):
        ClienteFiscal.recordar({**self.DATOS, "documento": "J-302401046"})
        for usuario in (self.mesero, self.chef):
            self.assertEqual(self.cliente(usuario).get("/caja/clientes/buscar/", {"q": "302"}).status_code, 403)
        self.assertEqual(self.client.get("/caja/clientes/buscar/", {"q": "302"}).status_code, 302)

    def test_editar_y_borrar_un_cliente(self):
        cliente = ClienteFiscal.recordar({**self.DATOS, "documento": "J-302401046"})
        caja = self.cliente(self.cajero)
        r = caja.post(f"/caja/clientes/{cliente.pk}/", {**self.DATOS, "documento": "123"}, follow=True)
        self.assertContains(r, "no es válido")
        caja.post(f"/caja/clientes/{cliente.pk}/", {**self.DATOS, "nombre": "Navieramar"})
        cliente.refresh_from_db()
        self.assertEqual(cliente.nombre, "Navieramar")
        self.assertEqual(caja.post(f"/caja/clientes/{cliente.pk}/", {"accion": "borrar"}).status_code, 403)
        self.cliente(self.admin).post(f"/caja/clientes/{cliente.pk}/", {"accion": "borrar"})
        self.assertFalse(ClienteFiscal.objects.exists())

    def test_el_cliente_que_pide_factura_desde_la_mesa_tambien_queda_guardado(self):
        f = self.factura()
        f.guardar_datos_cliente({**self.DATOS, "documento": "V-12345678"})
        self.assertTrue(ClienteFiscal.objects.filter(documento="V-12345678").exists())
        f.guardar_datos_cliente(None)  # pasar a consumidor final no borra al cliente
        self.assertEqual(ClienteFiscal.objects.count(), 1)


# =============================== factura en ticket ===============================
class TicketFacturaTests(_Estaciones):
    def test_ticket_como_el_modelo_sin_seniat(self):
        pedido = self.pedido(items=[(self.refresco, 4), (self.shawarma, 18)])
        f = Factura.emitir_desde_pedido(pedido, self.cajero)
        f.guardar_datos_cliente({"nombre": "Servicios Navieramar C.A.", "documento": "J-302401046",
                                 "direccion": "Puerto La Cruz", "telefono": "", "correo": ""})
        f.cobrar(metodo=Pago.Metodo.PAGO_MOVIL, monto=f.total_local, referencia="7788")
        texto = ticket_factura(f)
        lineas = texto.splitlines()
        self.assertNotIn("SENIAT", texto)
        self.assertEqual(lineas[0].strip(), "RIF J-317688600")
        self.assertEqual(lineas[1].strip(), "COMIDA ARABE EL AMIR, C.A")
        for esperado in ("CALLE BOLIVAR LOCAL NRO 133", "SECTOR CENTRO", "PUERTO LA CRUZ - EDO ANZOATEGUI",
                         "Cliente: SERVICIOS NAVIERAMAR C.A.", "CI/RIF: J-302401046", f"{pedido.pk:08d}/CONTADO",
                         "Tasa Ref. Bs./$: 40,00", "4x Bs 60,00", "18x Bs 240,00", "REFRESCO (G)", "SHAWARMA DE POLLO (G)",
                         "BI G16,00%", "IVA G16,00%", "PAGO MOVIL", "Ref. 7788", "TOTAL"):
            self.assertIn(esperado, texto)
        self.assertTrue(all(len(l) <= 40 for l in lineas), [l for l in lineas if len(l) > 40])
        total = next(l for l in lineas if l.startswith("TOTAL "))
        self.assertTrue(total.endswith("Bs 4.560,00"))  # (4 × 1,50 + 18 × 6) × 40
        self.assertEqual(numero_factura(f.numero), "00000001")
        r = self.cliente(self.cajero).get(f"/caja/factura/{f.pk}/")
        self.assertContains(r, 'class="ticket-papel"')
        self.assertContains(r, "Imprimir factura")

    def test_ticket_anulado_y_consumidor_final(self):
        f = Factura.emitir_desde_pedido(self.pedido(items=[(self.bistec, 1)]), self.cajero)
        f.anular("Error de mesa")
        texto = ticket_factura(f)
        self.assertIn("Cliente: CONSUMIDOR FINAL", texto)
        self.assertIn("*** FACTURA ANULADA ***", texto)


# =============================== nota de cierre ===============================
class NotaDeCierreTests(_Estaciones):
    def cobrar_una(self):
        f = Factura.emitir_desde_pedido(self.pedido(items=[(self.bistec, 1)]), self.cajero)
        f.cobrar(metodo=Pago.Metodo.EFECTIVO, monto=Decimal("500"))
        return f

    @override_settings(CIERRE_CORREOS=["dueno@elamir.com"])
    def test_al_generar_el_cierre_sale_la_nota_y_se_envia(self):
        self.cobrar_una()
        caja = self.cliente(self.cajero)
        r = caja.post("/caja/cierre/generar/", follow=True)
        cierre = CierreCaja.objects.get()
        self.assertContains(r, "Imprimir nota de cierre")
        self.assertContains(r, "NOTA DE CIERRE DE CAJA")
        self.assertContains(r, "dueno@elamir.com")
        self.assertEqual(mail.outbox[0].to, ["dueno@elamir.com"])
        cuerpo = mail.outbox[0].body
        for esperado in ("NOTA DE CIERRE DE CAJA", "EFECTIVO BS (1)", "TOTAL COBRADO", "Bs 400,00",
                         "Efectivo en bolivares", "Firma cajero"):
            self.assertIn(esperado, cuerpo)
        cierre.refresh_from_db()
        self.assertEqual(cierre.correo_enviado_a, "dueno@elamir.com")
        self.assertIsNotNone(cierre.correo_enviado_en)
        self.assertTrue(all(len(l) <= 40 for l in ticket_cierre(cierre).splitlines()))

    @override_settings(CIERRE_CORREOS=[])
    def test_sin_configurar_va_a_los_administradores_y_se_puede_reenviar(self):
        self.admin.email = "admin@elamir.com"
        self.admin.save()
        self.cobrar_una()
        caja = self.cliente(self.cajero)
        caja.post("/caja/cierre/generar/")
        self.assertEqual(mail.outbox[0].to, ["admin@elamir.com"])
        cierre = CierreCaja.objects.get()
        r = caja.post(f"/caja/cierre/{cierre.pk}/correo/", {"correos": "uno@correo.com, no-es-correo"}, follow=True)
        self.assertContains(r, "no es válido")
        caja.post(f"/caja/cierre/{cierre.pk}/correo/", {"correos": "uno@correo.com; dos@correo.com"})
        self.assertEqual(mail.outbox[-1].to, ["uno@correo.com", "dos@correo.com"])
        # otro cajero no ve ni envía cierres ajenos
        self.assertEqual(self.cliente(self.cajero2).post(f"/caja/cierre/{cierre.pk}/correo/", {"correos": "x@y.com"}).status_code, 403)


# =============================== teléfono del pago móvil ===============================
class TelefonoPagoMovilTests(_Estaciones):
    def test_el_menu_pide_el_telefono_del_pago_movil(self):
        from menu.mesa import codigo

        r = self.client.get(f"/menu/?mesa=1&c={codigo(1)}")
        self.assertContains(r, "Teléfono desde el que pagaste *")
        self.assertContains(r, 'id="pago-telefono"')
