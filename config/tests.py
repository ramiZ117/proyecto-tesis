from django.contrib.auth.models import Group, User
from django.test import Client

from config.permisos import GRUPO_ADMIN, GRUPO_MESERO
from config.base_pruebas import DatosBase


class AutenticacionYRolesTests(DatosBase):
    """RF-01: autenticaciÃ³n con credenciales Ãºnicas y acceso segÃºn el rol."""

    def login(self, usuario, clave=None, **extra):
        return Client().post("/login/", {"username": usuario, "password": clave or self.PASSWORD}, **extra)

    def test_login_valido_lleva_al_modulo_del_rol(self):
        destinos = {"mesero1": "/mesas/", "chef1": "/cocina/", "cajero1": "/caja/"}
        for usuario, destino in destinos.items():
            c = Client()
            r = c.post("/login/", {"username": usuario, "password": self.PASSWORD}, follow=True)
            self.assertEqual(r.redirect_chain[-1][0], destino, usuario)

    def test_contrasena_guardada_cifrada(self):
        self.assertTrue(self.mesero.password.startswith("pbkdf2_"))
        self.assertNotIn(self.PASSWORD, self.mesero.password)

    def test_credenciales_invalidas_no_inician_sesion(self):
        r = self.login("mesero1", "incorrecta")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", r.wsgi_request.session)

    def test_usuario_inactivo_no_entra(self):
        User.objects.filter(pk=self.mesero.pk).update(is_active=False)
        r = self.login("mesero1")
        self.assertEqual(r.status_code, 200)

    def test_bloqueo_tras_cinco_intentos_fallidos(self):
        c = Client()
        for _ in range(5):
            c.post("/login/", {"username": "mesero1", "password": "mala"})
        r = c.post("/login/", {"username": "mesero1", "password": self.PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Demasiados intentos")

    def test_sin_sesion_se_redirige_al_login(self):
        for url in ("/mesas/", "/cocina/", "/caja/", "/usuarios/", "/inventario/"):
            r = Client().get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertTrue(r["Location"].startswith("/login/"), url)

    def test_cada_rol_solo_entra_a_su_modulo(self):
        casos = [
            (self.mesero, {"/mesas/": 200, "/cocina/": 403, "/caja/": 403, "/usuarios/": 403}),
            (self.chef, {"/mesas/": 403, "/cocina/": 200, "/caja/": 403, "/usuarios/": 403}),
            (self.cajero, {"/mesas/": 403, "/cocina/": 403, "/caja/": 200, "/usuarios/": 403}),
        ]
        for usuario, esperados in casos:
            c = self.cliente(usuario)
            for url, codigo in esperados.items():
                self.assertEqual(c.get(url).status_code, codigo, f"{usuario.username} {url}")

    def test_administrador_accede_a_todo(self):
        c = self.cliente(self.admin)
        for url in ("/mesas/", "/cocina/", "/caja/", "/usuarios/", "/inventario/", "/caja/reporte/"):
            self.assertEqual(c.get(url).status_code, 200, url)

    def test_is_staff_sin_grupo_no_da_acceso(self):
        extra = User.objects.create_user("staff1", password=self.PASSWORD, is_staff=True)
        c = self.cliente(extra)
        for url in ("/mesas/", "/cocina/", "/caja/", "/usuarios/"):
            self.assertEqual(c.get(url).status_code, 403, url)

    def test_administrador_crea_usuario_con_rol_y_puede_ingresar(self):
        c = self.cliente(self.admin)
        r = c.post(
            "/usuarios/nuevo/",
            {"username": "nuevo1", "first_name": "Nuevo", "rol": GRUPO_MESERO,
             "password": "Contrasena-Segura-77", "is_active": "on"},
        )
        self.assertEqual(r.status_code, 302)
        nuevo = User.objects.get(username="nuevo1")
        self.assertTrue(nuevo.groups.filter(name=GRUPO_MESERO).exists())
        self.assertTrue(nuevo.password.startswith("pbkdf2_"))
        r = Client().post("/login/", {"username": "nuevo1", "password": "Contrasena-Segura-77"})
        self.assertEqual(r.status_code, 302)

    def test_creacion_exige_contrasena_valida(self):
        c = self.cliente(self.admin)
        r = c.post("/usuarios/nuevo/", {"username": "debil", "rol": GRUPO_MESERO, "password": "123"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="debil").exists())

    def test_cambio_de_rol_y_desactivacion(self):
        c = self.cliente(self.admin)
        c.post(
            f"/usuarios/{self.mesero.pk}/",
            {"username": "mesero1", "rol": GRUPO_ADMIN, "is_active": "on"},
        )
        self.assertTrue(self.mesero.groups.filter(name=GRUPO_ADMIN).exists())
        self.assertFalse(self.mesero.groups.filter(name=GRUPO_MESERO).exists())
        c.post(f"/usuarios/{self.chef.pk}/alternar/")
        self.chef.refresh_from_db()
        self.assertFalse(self.chef.is_active)

    def test_administrador_no_puede_desactivarse_a_si_mismo(self):
        c = self.cliente(self.admin)
        c.post(f"/usuarios/{self.admin.pk}/alternar/")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_solo_administrador_gestiona_usuarios(self):
        c = self.cliente(self.cajero)
        self.assertEqual(c.post("/usuarios/nuevo/", {"username": "x"}).status_code, 403)

    def test_grupos_operativos_existen(self):
        for nombre in (GRUPO_ADMIN, GRUPO_MESERO):
            self.assertTrue(Group.objects.filter(name=nombre).exists())
