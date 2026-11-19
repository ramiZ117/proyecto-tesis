"""Administración propia (carta, insumos, proveedores, mesas) y menú digital público con asistente."""

import json
import shutil
import tempfile
from decimal import Decimal
from io import BytesIO
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from PIL import Image

from config.base_pruebas import DatosBase
from inventario.models import Categoria, Insumo, MovimientoStock, Producto, Receta
from menu import asistente
from mesas.models import Mesa, Pedido

MEDIA_PRUEBAS = tempfile.mkdtemp(prefix="el_amir_media_")


def imagen(ancho=1600, alto=1200, formato="PNG"):
    salida = BytesIO()
    Image.new("RGB", (ancho, alto), (200, 140, 60)).save(salida, format=formato)
    return SimpleUploadedFile(f"foto.{formato.lower()}", salida.getvalue(), content_type=f"image/{formato.lower()}")


@override_settings(MEDIA_ROOT=MEDIA_PRUEBAS)
class GestionTests(DatosBase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_PRUEBAS, ignore_errors=True)

    def datos_plato(self, **extra):
        datos = {
            "nombre": "Shawarma de pollo", "categoria": self.bistec.categoria_id, "precio_usd": "6.00",
            "descripcion": "Pollo, pan árabe y ajo", "disponible": "on", "vegetariano": "",
            "receta-TOTAL_FORMS": "2", "receta-INITIAL_FORMS": "0", "receta-MIN_NUM_FORMS": "0", "receta-MAX_NUM_FORMS": "1000",
            "receta-0-insumo": self.res.pk, "receta-0-cantidad": "0.180",
            "receta-1-insumo": self.pan.pk, "receta-1-cantidad": "1",
        }
        datos.update(extra)
        return datos

    def test_solo_el_administrador_entra(self):
        for ruta in ("/gestion/carta/", "/gestion/carta/plato/nuevo/", "/gestion/insumos/", "/gestion/proveedores/",
                     "/gestion/mesas/", "/gestion/mesas/nueva/", "/gestion/menu-digital/"):
            self.assertEqual(self.cliente(self.cajero).get(ruta).status_code, 403, ruta)
            self.assertEqual(self.cliente(self.admin).get(ruta).status_code, 200, ruta)

    def test_crear_plato_con_foto_y_receta(self):
        r = self.cliente(self.admin).post("/gestion/carta/plato/nuevo/", {**self.datos_plato(), "foto": imagen()})
        self.assertEqual(r.status_code, 302)
        plato = Producto.objects.get(nombre="Shawarma de pollo")
        self.assertEqual(Receta.objects.filter(producto=plato).count(), 2)
        self.assertTrue(plato.foto.name.endswith(".jpg"))
        with Image.open(plato.foto.path) as foto:  # normalizada: JPEG de hasta 1400 px
            self.assertEqual((foto.format, max(foto.size)), ("JPEG", 1400))
        self.assertContains(self.cliente(self.admin).get("/gestion/carta/"), "Shawarma de pollo")

    def test_validaciones_del_plato(self):
        c = self.cliente(self.admin)
        texto = SimpleUploadedFile("foto.jpg", b"no soy una imagen", content_type="image/jpeg")
        for extra, error in (
            ({"foto": texto}, "imagen"),
            ({"foto": imagen(200, 150)}, "muy pequeña"),
            ({"precio_usd": "-3"}, "negativo"),
            ({"nombre": " "}, "obligatorio"),
            ({"receta-1-insumo": self.res.pk}, "aparece dos veces"),
        ):
            r = c.post("/gestion/carta/plato/nuevo/", self.datos_plato(**extra))
            self.assertEqual(r.status_code, 200, error)
            self.assertContains(r, error, msg_prefix=error)
        self.assertFalse(Producto.objects.filter(nombre="Shawarma de pollo").exists())

    def test_editar_plato_y_sacarlo_de_la_carta(self):
        c = self.cliente(self.admin)
        r = c.post(f"/gestion/carta/plato/{self.agua.pk}/", {
            **self.datos_plato(nombre="Agua mineral", precio_usd="1.25"), "receta-TOTAL_FORMS": "0",
        })
        self.assertEqual(r.status_code, 302)
        self.agua.refresh_from_db()
        self.assertEqual((self.agua.nombre, self.agua.precio_usd), ("Agua mineral", Decimal("1.25")))
        c.post(f"/gestion/carta/plato/{self.agua.pk}/disponible/")
        self.agua.refresh_from_db()
        self.assertFalse(self.agua.disponible)
        self.assertNotContains(self.cliente(self.mesero).get(f"/mesas/comanda/{self.pedido(items=[]).pk}/"), "Agua mineral")

    def test_categoria_nueva(self):
        self.cliente(self.admin).post("/gestion/carta/categoria/nueva/", {"nombre": "Postres", "estacion": "FRI", "orden": "4", "activa": "on"})
        self.assertEqual(Categoria.objects.get(nombre="Postres").estacion, "FRI")

    def test_insumo_nuevo_con_stock_inicial_y_niveles(self):
        c = self.cliente(self.admin)
        base = {"nombre": "Tahini", "grupo": "ESP", "unidad": "KG", "costo_unitario": "9.5", "activo": "on", "stock_inicial": "3"}
        r = c.post("/gestion/insumos/nuevo/", {**base, "stock_minimo": "1", "punto_reposicion": "0.5", "stock_maximo": "6"})
        self.assertContains(r, "punto de reposición no puede ser menor")
        c.post("/gestion/insumos/nuevo/", {**base, "stock_minimo": "0.5", "punto_reposicion": "1", "stock_maximo": "6"})
        tahini = Insumo.objects.get(nombre="Tahini")
        self.assertEqual(tahini.stock_actual, Decimal("3.000"))
        mov = MovimientoStock.objects.get(insumo=tahini)
        self.assertEqual((mov.tipo, mov.nota, mov.usuario), ("ENT", "Stock inicial", self.admin))

    def test_proveedores(self):
        c = self.cliente(self.admin)
        c.post("/gestion/proveedores/nuevo/", {"nombre": "Distribuidora Damasco", "contacto": "Sr. Haddad", "telefono": "0281-555 1234", "activo": "on"})
        self.assertContains(c.get("/gestion/proveedores/"), "Distribuidora Damasco")
        r = c.post("/gestion/proveedores/nuevo/", {"nombre": "Distribuidora Damasco", "activo": "on"})
        self.assertContains(r, "Ya existe")

    def test_agregar_mesa_y_validaciones(self):
        c = self.cliente(self.admin)
        r = c.get("/gestion/mesas/nueva/")
        self.assertContains(r, 'value="4"')  # siguiente número sugerido
        c.post("/gestion/mesas/nueva/", {"numero": "4", "capacidad": "6", "pos_x": "3", "pos_y": "1", "activa": "on"})
        mesa = Mesa.objects.get(numero=4)
        self.assertEqual((mesa.pos_x, mesa.pos_y), (2, 0))  # en pantalla empieza en 1
        self.assertContains(self.cliente(self.mesero).get("/mesas/"), "Mesa 4")
        for datos, error in (
            ({"numero": "4", "capacidad": "2", "pos_x": "4", "pos_y": "1", "activa": "on"}, "Ya existe"),
            ({"numero": "9", "capacidad": "2", "pos_x": "1", "pos_y": "1", "activa": "on"}, "ya la usa la Mesa 1"),
            ({"numero": "9", "capacidad": "0", "pos_x": "4", "pos_y": "3", "activa": "on"}, "1 a 30"),
            ({"numero": "9", "capacidad": "2", "pos_x": "0", "pos_y": "3", "activa": "on"}, "Empieza en 1"),
        ):
            self.assertContains(c.post("/gestion/mesas/nueva/", datos), error, msg_prefix=error)

    def test_retirar_mesa_solo_si_esta_libre(self):
        c = self.cliente(self.admin)
        self.pedido(self.mesa2, items=[(self.agua, 1)])
        r = c.post(f"/gestion/mesas/{self.mesa2.pk}/", {"numero": "2", "capacidad": "4", "pos_x": "2", "pos_y": "1"})
        self.assertContains(r, "en servicio")
        c.post(f"/gestion/mesas/{self.mesa3.pk}/", {"numero": "3", "capacidad": "4", "pos_x": "1", "pos_y": "2"})
        self.mesa3.refresh_from_db()
        self.assertFalse(self.mesa3.activa)
        self.assertNotContains(self.cliente(self.mesero).get("/mesas/"), "Mesa 3,")

    @override_settings(MENU_URL_PUBLICA="http://192.168.1.10:8000")
    def test_menu_digital_genera_los_qr(self):
        r = self.cliente(self.admin).get("/gestion/menu-digital/")
        self.assertContains(r, "http://192.168.1.10:8000/menu/")
        self.assertContains(r, 'class="qr"', count=1 + Mesa.objects.filter(activa=True).count())
        self.assertContains(r, "Mesa 2")


class MenuPublicoTests(DatosBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.publico = Client()

    def preguntar(self, mensaje, cliente=None, **extra):
        return (cliente or self.publico).post("/menu/asistente/", json.dumps({"mensaje": mensaje, **extra}), content_type="application/json")

    def test_se_abre_sin_iniciar_sesion(self):
        Producto.objects.filter(pk=self.bistec.pk).update(destacado=True, descripcion="Lomito a la plancha")
        r = self.publico.get("/menu/?mesa=2")
        self.assertEqual(r.status_code, 200)
        for texto in ("Bistec", "Lomito a la plancha", "Recomendados de la casa", "Mesa 2", "datos-platos", "1 USD = 40,00 Bs"):
            self.assertContains(r, texto)
        self.assertEqual(self.publico.get("/menu/?mesa=abc").status_code, 200)

    def test_oculta_lo_que_no_esta_en_carta_y_marca_lo_agotado(self):
        Producto.objects.filter(pk=self.agua.pk).update(disponible=False)
        Insumo.objects.filter(pk=self.pan.pk).update(stock_actual=0)
        r = self.publico.get("/menu/")
        self.assertNotContains(r, '"Agua"')
        self.assertContains(r, "Agotado hoy")

    def test_el_asistente_local_conoce_la_carta(self):
        Producto.objects.filter(pk=self.bistec.pk).update(destacado=True)
        Producto.objects.filter(pk=self.agua.pk).update(vegetariano=True)
        casos = {
            "¿Qué me recomiendas?": "Bistec",
            "opciones vegetarianas": "Agua",
            "¿Qué lleva la hamburguesa?": "pan",
            "soy alérgico al maní, ¿el bistec?": "mesonero",
            "quiero pedir": "mesonero",
        }
        for pregunta, esperado in casos.items():
            r = self.preguntar(pregunta)
            self.assertEqual(r.status_code, 200, pregunta)
            self.assertIn(esperado.lower(), r.json()["respuesta"].lower(), pregunta)
            self.assertEqual(r.json()["origen"], "local")
        self.assertIn(self.hamburguesa.pk, self.preguntar("¿Qué es la hamburguesa?").json()["platos"])

    def test_valida_las_preguntas(self):
        for cuerpo in ("no es json", json.dumps([1, 2]), json.dumps({"mensaje": "  "}), json.dumps({"mensaje": "x" * 401})):
            r = self.publico.post("/menu/asistente/", cuerpo, content_type="application/json")
            self.assertEqual(r.status_code, 400, cuerpo[:20])
            self.assertIn("error", r.json())
        self.assertEqual(self.publico.get("/menu/asistente/").status_code, 405)

    @override_settings(IA_MENSAJES_POR_HORA=2)
    def test_limita_las_preguntas_por_hora(self):
        self.assertEqual(self.preguntar("hola").status_code, 200)
        self.assertEqual(self.preguntar("hola").status_code, 200)
        r = self.preguntar("hola")
        self.assertEqual(r.status_code, 429)
        self.assertIn("muchas preguntas", r.json()["error"])

    def test_exige_el_token_csrf(self):
        estricto = Client(enforce_csrf_checks=True)
        self.assertEqual(self.preguntar("hola", cliente=estricto).status_code, 403)

    @override_settings(IA_PROVEEDOR="gemini", GEMINI_API_KEY="clave-falsa", GEMINI_MODELO="modelo-x")
    def test_usa_gemini_y_le_da_la_carta(self):
        respuesta = {"candidates": [{"content": {"parts": [{"text": "El Bistec es lomito a la plancha."}]}}]}
        with mock.patch.object(asistente, "_post_json", return_value=respuesta) as llamada:
            r = self.preguntar("¿Qué es el bistec?", historial=[{"rol": "cliente", "texto": "hola"}, {"rol": "intruso", "texto": "x"}])
        datos = r.json()
        self.assertEqual((datos["origen"], datos["platos"]), ("gemini", [self.bistec.pk]))
        url, cuerpo, cabeceras = llamada.call_args.args
        self.assertIn("modelo-x:generateContent", url)
        self.assertEqual(cabeceras["x-goog-api-key"], "clave-falsa")
        sistema = cuerpo["systemInstruction"]["parts"][0]["text"]
        self.assertIn("Bistec (Carnes): $10,00", sistema)
        self.assertIn("No inventes platos", sistema)
        self.assertEqual([c["role"] for c in cuerpo["contents"]], ["user", "user"])  # el turno inválido se descarta

    @override_settings(IA_PROVEEDOR="groq", GROQ_API_KEY="clave-falsa")
    def test_si_la_ia_falla_responde_el_asistente_local(self):
        with mock.patch.object(asistente, "_post_json", side_effect=TimeoutError("sin red")):
            datos = self.preguntar("¿Qué me recomiendas?").json()
        self.assertEqual(datos["origen"], "local")
        self.assertTrue(datos["respuesta"])

    def test_proveedor_segun_las_claves(self):
        with override_settings(IA_PROVEEDOR="", GEMINI_API_KEY="", GROQ_API_KEY="", XAI_API_KEY=""):
            self.assertEqual(asistente.proveedor_activo()["clave"], "local")
        with override_settings(IA_PROVEEDOR="", GEMINI_API_KEY="", GROQ_API_KEY="g", XAI_API_KEY=""):
            self.assertEqual(asistente.proveedor_activo()["clave"], "groq")
        with override_settings(IA_PROVEEDOR="xai", XAI_API_KEY=""):
            self.assertEqual(asistente.proveedor_activo()["clave"], "local")  # pedido sin clave: local


class ContactoDelLocalTests(DatosBase):
    @override_settings(RESTAURANTE_TELEFONO="0414-1111222", RESTAURANTE_INSTAGRAM="comidaarabeelamir_", RESTAURANTE_WHATSAPP="")
    def test_menu_muestra_instagram_y_telefono(self):
        r = Client().get("/menu/")
        self.assertContains(r, "https://www.instagram.com/comidaarabeelamir_/")
        self.assertContains(r, 'href="tel:+584141111222"')
        self.assertContains(r, "Guardar contacto")
        self.assertNotContains(r, "wa.me")  # sin WhatsApp configurado no se muestra

    @override_settings(RESTAURANTE_TELEFONO="0414-1111222", RESTAURANTE_NOMBRE="Comida Árabe El Amir; Centro")
    def test_tarjeta_de_contacto_para_la_agenda(self):
        r = Client().get("/menu/contacto.vcf")
        self.assertEqual(r["Content-Type"], "text/vcard; charset=utf-8")
        texto = r.content.decode("utf-8")
        self.assertIn("TEL;TYPE=WORK,VOICE:+584141111222", texto)
        self.assertIn("FN:Comida Árabe El Amir\; Centro", texto)  # los separadores se escapan
        self.assertIn("instagram.com/comidaarabeelamir_", texto)


class AsistenteRobustoTests(DatosBase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def preguntar(self, mensaje):
        return Client().post("/menu/asistente/", json.dumps({"mensaje": mensaje}), content_type="application/json").json()

    @override_settings(IA_PROVEEDOR="gemini", GEMINI_API_KEY="k", GEMINI_MODELO="modelo-saturado, modelo-libre")
    def test_si_un_modelo_esta_saturado_usa_el_siguiente(self):
        import urllib.error

        saturado = urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)
        ok = {"candidates": [{"content": {"parts": [{"text": "Te recomiendo el Bistec."}]}}]}
        with mock.patch.object(asistente, "_post_json", side_effect=[saturado, ok]) as llamada:
            datos = self.preguntar("¿Qué me recomiendas?")
        self.assertEqual(datos["origen"], "gemini")
        self.assertIn("modelo-libre:generateContent", llamada.call_args_list[1].args[0])

    @override_settings(IA_PROVEEDOR="gemini", GEMINI_API_KEY="k", GEMINI_MODELO="m1")
    def test_tras_una_falla_responde_local_un_minuto_sin_esperar(self):
        with mock.patch.object(asistente, "_post_json", side_effect=TimeoutError) as llamada:
            self.assertEqual(self.preguntar("hola")["origen"], "local")
            self.assertEqual(self.preguntar("hola")["origen"], "local")
        self.assertEqual(llamada.call_count, 1)  # la segunda pregunta ni siquiera intentó la IA
        cache.delete(f"{asistente.CLAVE_PAUSA}:gemini")
        ok = {"candidates": [{"content": {"parts": [{"text": "Hola"}]}}]}
        with mock.patch.object(asistente, "_post_json", return_value=ok):
            self.assertEqual(self.preguntar("hola")["origen"], "gemini")

    @override_settings(IA_PROVEEDOR="", GEMINI_API_KEY="", GROQ_API_KEY="", XAI_API_KEY="")
    def test_el_asistente_local_entiende_nombres_parciales_y_dietas(self):
        Producto.objects.filter(pk=self.agua.pk).update(vegetariano=True)
        kibbe = Producto.objects.create(categoria=self.bistec.categoria, nombre="Kibbe frito (4 und)",
                                        precio_usd=Decimal("6"), descripcion="Croquetas de trigo burgol rellenas de carne.")
        datos = self.preguntar("¿Qué es el kibbe?")
        self.assertIn("Croquetas de trigo burgol", datos["respuesta"])
        self.assertEqual(datos["platos"], [kibbe.pk])
        sin_carne = self.preguntar("¿Tienen algo sin carne?")["respuesta"]
        self.assertIn("Opciones vegetarianas", sin_carne)
        self.assertIn("Agua", sin_carne)
