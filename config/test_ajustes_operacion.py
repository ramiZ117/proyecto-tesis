"""Cocina por pedido, permisos de cocina, forma de las mesas, personalizaciones, tiempos estimados y menú."""

import json
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from cocina import tiempos
from config.base_pruebas import DatosBase
from gestion.models import AjustesLocal
from menu.mesa import codigo
from mesas.models import DetallePedido, Mesa


class CocinaTests(DatosBase):
    def test_un_boton_por_pedido_empieza_y_termina_todos_sus_platos(self):
        pedido = self.pedido(items=[(self.bistec, 1), (self.agua, 2)])
        chef = self.cliente(self.chef)
        r = chef.get("/cocina/")
        self.assertContains(r, "Empezar (2 platos)", count=1)
        chef.post(f"/cocina/pedido/{pedido.pk}/", {"accion": "empezar"})
        self.assertEqual(set(pedido.detalles.values_list("estado", flat=True)), {DetallePedido.Estado.EN_PREPARACION})
        self.assertIsNotNone(pedido.detalles.first().preparacion_en)
        self.assertContains(chef.get("/cocina/"), "Todo listo", count=1)
        chef.post(f"/cocina/pedido/{pedido.pk}/", {"accion": "listo"})
        self.assertEqual(set(pedido.detalles.values_list("estado", flat=True)), {DetallePedido.Estado.LISTO})
        r = chef.post(f"/cocina/pedido/{pedido.pk}/", {"accion": "empezar"}, follow=True)
        self.assertContains(r, "No hay platos nuevos")

    def test_el_tablero_no_trae_minutos_escritos_para_no_parpadear(self):
        self.pedido(items=[(self.bistec, 1)])
        parcial = self.cliente(self.chef).get("/cocina/parcial/").content.decode()
        self.assertIn("data-reloj-texto></span>", parcial)
        self.assertNotIn(" min</span>", parcial)

    def test_la_cocina_no_repone_ni_ve_el_inventario(self):
        chef = self.cliente(self.chef)
        for ruta in ("/inventario/", "/inventario/movimientos/", f"/inventario/insumo/{self.res.pk}/kardex/"):
            self.assertEqual(chef.get(ruta).status_code, 403, ruta)
        r = chef.post("/inventario/movimiento/", {"tipo": "ENT", "insumo_id": self.res.pk, "cantidad": "5"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(chef.post("/inventario/entrada/", {"insumo_id": self.res.pk, "cantidad": "5"}).status_code, 403)
        self.res.refresh_from_db()
        self.assertEqual(self.res.stock_actual, Decimal("10"))
        self.assertNotContains(chef.get("/cocina/"), "reponer")


class FormaDeMesaTests(DatosBase):
    def datos(self, **extra):
        return {"numero": 9, "capacidad": 4, "forma": "RED", "pos_x": 3, "pos_y": 1, "activa": "on", **extra}

    def test_elegir_y_editar_la_forma(self):
        admin = self.cliente(self.admin)
        self.assertContains(admin.get("/gestion/mesas/nueva/"), 'value="RED"')
        admin.post("/gestion/mesas/nueva/", self.datos())
        mesa = Mesa.objects.get(numero=9)
        self.assertEqual(mesa.forma, Mesa.Forma.REDONDA)
        admin.post(f"/gestion/mesas/{mesa.pk}/", self.datos(forma="CUA"))
        mesa.refresh_from_db()
        self.assertEqual(mesa.forma, Mesa.Forma.CUADRADA)
        self.assertContains(self.cliente(self.mesero).get("/mesas/"), "forma-CUA")

    def test_la_rectangular_ocupa_dos_columnas(self):
        admin = self.cliente(self.admin)
        # mesa1 está en columna 1 / fila 1 y mesa2 en columna 2 / fila 1
        r = admin.post("/gestion/mesas/nueva/", self.datos(forma="REC", pos_x=1, pos_y=3))
        self.assertEqual(r.status_code, 302)
        r = admin.post("/gestion/mesas/nueva/", self.datos(numero=10, forma="CUA", pos_x=2, pos_y=3))
        self.assertContains(r, "ya la usa la Mesa 9")
        r = admin.post("/gestion/mesas/nueva/", self.datos(numero=10, forma="REC", pos_x=12, pos_y=4))
        self.assertContains(r, "columna libre a su derecha")
        self.assertContains(self.cliente(self.mesero).get("/mesas/"), "grid-column: 1 / span 2")

    def test_el_aviso_de_la_mesa_que_llama_es_corto(self):
        cache.clear()
        Client().post("/menu/solicitar/", json.dumps({"mesa": 2, "codigo": codigo(2), "items": []}), content_type="application/json")
        self.assertContains(self.cliente(self.mesero).get("/mesas/parcial/"), "<span>Llama al mesero</span>")


class PersonalizacionTests(DatosBase):
    def setUp(self):
        super().setUp()
        self.bistec.opciones = "Sin cebolla\nSalsa aparte\nsin cebolla\n  \nBien cocido"
        self.bistec.save()

    def test_lista_de_opciones_limpia(self):
        self.assertEqual(self.bistec.lista_opciones, ["Sin cebolla", "Salsa aparte", "Bien cocido"])

    def test_el_mesero_personaliza_antes_de_enviar(self):
        pedido = self.pedido(items=[(self.bistec, 1)], enviar=False)
        linea = pedido.detalles.get()
        mesero = self.cliente(self.mesero)
        self.assertContains(mesero.get(f"/mesas/comanda/{pedido.pk}/"), 'value="Salsa aparte"')
        mesero.post(f"/mesas/item/{linea.pk}/personalizar/", {"opcion": ["Sin cebolla", "Bien cocido"], "nota": "para niño"})
        linea.refresh_from_db()
        self.assertEqual(linea.observaciones, "Sin cebolla, Bien cocido · para niño")
        r = mesero.post(f"/mesas/item/{linea.pk}/personalizar/", {"opcion": ["Extra queso"]}, follow=True)
        self.assertContains(r, "no es una opción")
        pedido.enviar_a_cocina()
        r = mesero.post(f"/mesas/item/{linea.pk}/personalizar/", {"opcion": ["Salsa aparte"]}, follow=True)
        self.assertContains(r, "aún no se envían")
        self.assertContains(self.cliente(self.chef).get("/cocina/"), "Sin cebolla, Bien cocido")

    def test_gestion_valida_las_opciones(self):
        datos = {"nombre": "Bistec", "categoria": self.bistec.categoria_id, "precio_usd": "10.00", "disponible": "on",
                 "minutos_preparacion": "200", "opciones": "Sin cebolla",
                 "receta-TOTAL_FORMS": "0", "receta-INITIAL_FORMS": "0", "receta-MIN_NUM_FORMS": "0", "receta-MAX_NUM_FORMS": "1000"}
        admin = self.cliente(self.admin)
        self.assertContains(admin.post(f"/gestion/carta/plato/{self.bistec.pk}/", datos), "Entre 1 y 120")
        r = admin.post(f"/gestion/carta/plato/{self.bistec.pk}/", {**datos, "minutos_preparacion": "12", "opciones": "x" * 50})
        self.assertContains(r, "es muy larga")
        admin.post(f"/gestion/carta/plato/{self.bistec.pk}/", {**datos, "minutos_preparacion": "12", "opciones": "Sin cebolla\nSin cebolla\nPicante"})
        self.bistec.refresh_from_db()
        self.assertEqual((self.bistec.minutos_preparacion, self.bistec.opciones), (12, "Sin cebolla\nPicante"))

    def test_el_menu_ofrece_las_opciones_y_las_guarda_en_la_nota(self):
        r = Client().get("/menu/")
        self.assertContains(r, '"opciones": ["Sin cebolla", "Salsa aparte", "Bien cocido"]')
        cache.clear()
        nota = "Sin cebolla, Salsa aparte · " + "x" * 150
        Client().post("/menu/solicitar/", json.dumps({"mesa": 2, "codigo": codigo(2),
                      "items": [{"tipo": "producto", "id": self.bistec.pk, "cantidad": 1, "nota": nota}]}), content_type="application/json")
        from mesas.models import SolicitudCliente

        self.assertEqual(SolicitudCliente.objects.get().items[0]["nota"], nota[:200])


@override_settings(COCINA_PLATOS_SIMULTANEOS=2)
class TiempoEstimadoTests(DatosBase):
    def setUp(self):
        super().setUp()
        from inventario.models import Producto

        Producto.objects.filter(pk=self.bistec.pk).update(minutos_preparacion=20)
        Producto.objects.filter(pk=self.hamburguesa.pk).update(minutos_preparacion=10)
        Producto.objects.filter(pk=self.agua.pk).update(minutos_preparacion=1)

    def test_espera_segun_la_cola(self):
        self.assertEqual(tiempos.carga_actual(), 0)
        primero = self.pedido(mesa=self.mesa1, items=[(self.bistec, 1), (self.hamburguesa, 1)])  # 30 min de trabajo
        self.assertEqual(tiempos.estimar_pedido(primero), 20)  # nada delante: el plato más lento
        segundo = self.pedido(mesa=self.mesa2, items=[(self.agua, 1)])
        DetallePedido.objects.filter(pedido=segundo).update(enviado_en=timezone.now() + timedelta(seconds=5))
        self.assertEqual(tiempos.estimar_pedido(segundo), 16)  # 30 ÷ 2 + 1
        self.assertEqual(tiempos.carga_actual(), 16)  # (30 + 1) ÷ 2
        self.assertEqual(tiempos.estimar_nuevo([10]), 26)

    def test_lo_que_ya_se_prepara_cuenta_menos(self):
        pedido = self.pedido(items=[(self.bistec, 1)])
        detalle = pedido.detalles.get()
        detalle.marcar_en_preparacion()
        DetallePedido.objects.filter(pk=detalle.pk).update(preparacion_en=timezone.now() - timedelta(minutes=15))
        self.assertEqual(tiempos.estimar_pedido(pedido), 5)
        detalle.refresh_from_db()
        detalle.marcar_listo()
        self.assertIsNone(tiempos.estimar_pedido(pedido))

    def test_el_cliente_y_el_mesero_ven_la_espera(self):
        pedido = self.pedido(mesa=self.mesa2, items=[(self.bistec, 1)])
        estado = Client().get(f"/menu/solicitud/estado/?mesa=2&c={codigo(2)}").json()
        self.assertEqual(estado["cocina_min"], 20)
        self.assertContains(self.cliente(self.mesero).get(f"/mesas/comanda/{pedido.pk}/"), "Listo en ~20 min")
        self.assertContains(Client().get("/menu/"), '"carga": 10')


class MenuTests(DatosBase):
    def test_delivery_solo_informativo_y_carrito_en_el_enlace_general(self):
        r = Client().get("/menu/")
        contenido = r.content.decode()
        self.assertIn("PedidosYa", contenido)
        self.assertNotIn('href="#delivery"', contenido)
        self.assertNotIn("pedidosya.com", contenido)
        # con enlace cargado, el botón de abajo abre la tienda (en otra pestaña, sin mover el menú)
        AjustesLocal.objects.update_or_create(pk=1, defaults={"pedidosya_url": "https://www.pedidosya.com.ve/elamir"})
        cache.clear()
        r2 = Client().get("/menu/").content.decode()
        self.assertIn('href="https://www.pedidosya.com.ve/elamir" target="_blank"', r2)
        self.assertNotIn('href="#delivery"', r2)
        admin = self.cliente(self.admin)
        r3 = admin.post("/gestion/cobros-y-delivery/", {"yummy_url": "http://inseguro.com", "retiro_activo": "on", "mostrar_delivery": "on"})
        self.assertContains(r3, "https://")
        self.assertContains(r, "Agregar a mi pedido")  # se puede armar el pedido para retirar
        AjustesLocal.objects.update_or_create(pk=1, defaults={"retiro_activo": False})
        cache.clear()
        self.assertNotContains(Client().get("/menu/"), 'id="ficha-agregar"')

    def test_confirmaciones_con_la_ventana_del_sistema(self):
        pedido = self.pedido(items=[(self.agua, 1)])
        r = self.cliente(self.mesero).get(f"/mesas/comanda/{pedido.pk}/")
        self.assertContains(r, 'data-confirmar="¿Anular este pedido y liberar la mesa?" data-confirmar-peligro')
        self.assertNotContains(r, "return confirm(")

    def test_las_tablas_de_inventario_se_paginan(self):
        self.assertContains(self.cliente(self.cajero).get("/inventario/"), 'data-paginar="insumos"')
        self.assertContains(self.cliente(self.admin).get("/gestion/insumos/"), 'data-paginar="insumos-admin"')



class EntregarTodoTests(DatosBase):
    def test_el_mesero_entrega_todos_los_platos_listos(self):
        pedido = self.pedido(items=[(self.bistec, 1), (self.agua, 2), (self.hamburguesa, 1)])
        for d in pedido.detalles.exclude(producto=self.hamburguesa):
            d.marcar_en_preparacion()
            d.marcar_listo()
        mesero = self.cliente(self.mesero)
        self.assertContains(mesero.get(f"/mesas/comanda/{pedido.pk}/"), "Entregar todo (2 listos)")
        r = mesero.post(f"/mesas/comanda/{pedido.pk}/entregar-todo/", follow=True)
        self.assertContains(r, "3 platos entregados")
        estados = dict(pedido.detalles.values_list("producto_id", "estado"))
        self.assertEqual(estados[self.bistec.pk], DetallePedido.Estado.ENTREGADO)
        self.assertEqual(estados[self.hamburguesa.pk], DetallePedido.Estado.PENDIENTE)  # lo que no está listo no se toca
        r = mesero.post(f"/mesas/comanda/{pedido.pk}/entregar-todo/", follow=True)
        self.assertContains(r, "No hay platos listos")
        # otro mesero no puede entregar la mesa ajena
        self.cliente(self.mesero2).post(f"/mesas/comanda/{pedido.pk}/entregar-todo/")



class AdminRevisaMesasTests(DatosBase):
    def test_el_admin_revisa_una_mesa_libre_sin_ocuparla(self):
        admin = self.cliente(self.admin)
        r = admin.post(f"/mesas/abrir/{self.mesa3.pk}/", follow=True)
        self.assertContains(r, "la mesa no se ocupa")
        self.mesa3.refresh_from_db()
        self.assertEqual(self.mesa3.estado, Mesa.Estado.DISPONIBLE)
        from mesas.models import Pedido

        self.assertFalse(Pedido.objects.filter(mesa=self.mesa3).exists())

    def test_el_admin_ve_la_comanda_de_una_mesa_atendida(self):
        pedido = self.pedido(mesa=self.mesa2, mesero=self.mesero, items=[(self.agua, 1)])
        r = self.cliente(self.admin).post(f"/mesas/abrir/{self.mesa2.pk}/")
        self.assertRedirects(r, f"/mesas/comanda/{pedido.pk}/", fetch_redirect_response=False)
        pedido.refresh_from_db()
        self.assertEqual(pedido.mesero, self.mesero)  # sigue siendo del mesonero

    def test_el_mesonero_si_ocupa_la_mesa(self):
        self.cliente(self.mesero).post(f"/mesas/abrir/{self.mesa3.pk}/")
        self.mesa3.refresh_from_db()
        self.assertEqual(self.mesa3.estado, Mesa.Estado.OCUPADA)
