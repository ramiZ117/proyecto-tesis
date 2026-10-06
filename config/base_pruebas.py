"""Utilidades compartidas por las pruebas automáticas de todos los módulos."""

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import Client, TestCase

from caja.models import TasaCambio
from config.permisos import GRUPO_ADMIN, GRUPO_CAJA, GRUPO_COCINA, GRUPO_MESERO
from inventario.models import Categoria, Insumo, Producto, Receta
from mesas.models import DetallePedido, Mesa, Pedido


class DatosBase(TestCase):
    """Restaurante mínimo: usuarios por rol, tasa 40, 3 mesas, 3 platillos con receta."""

    PASSWORD = "Prueba-12345"

    @classmethod
    def _usuario(cls, username, grupo):
        user = User.objects.create_user(username=username, password=cls.PASSWORD)
        user.groups.add(Group.objects.get_or_create(name=grupo)[0])
        return user

    @classmethod
    def setUpTestData(cls):
        cls.admin = cls._usuario("admin1", GRUPO_ADMIN)
        cls.mesero = cls._usuario("mesero1", GRUPO_MESERO)
        cls.mesero2 = cls._usuario("mesero2", GRUPO_MESERO)
        cls.chef = cls._usuario("chef1", GRUPO_COCINA)
        cls.cajero = cls._usuario("cajero1", GRUPO_CAJA)
        cls.cajero2 = cls._usuario("cajero2", GRUPO_CAJA)

        cls.tasa = TasaCambio.objects.create(valor=Decimal("40.0000"), creada_por=cls.cajero, activa=True)

        cls.mesa1 = Mesa.objects.create(numero=1, capacidad=2, pos_x=0, pos_y=0)
        cls.mesa2 = Mesa.objects.create(numero=2, capacidad=4, pos_x=1, pos_y=0)
        cls.mesa3 = Mesa.objects.create(numero=3, capacidad=4, pos_x=0, pos_y=1)

        carnes = Categoria.objects.create(nombre="Carnes")
        cls.res = Insumo.objects.create(
            nombre="Res", grupo="CAR", unidad=Insumo.Unidad.KILOGRAMO,
            stock_actual=Decimal("10"), stock_minimo=Decimal("2"),
        )
        cls.pan = Insumo.objects.create(
            nombre="Pan", grupo="VEG", unidad=Insumo.Unidad.UNIDAD,
            stock_actual=Decimal("5"), stock_minimo=Decimal("1"),
        )
        cls.bistec = Producto.objects.create(categoria=carnes, nombre="Bistec", precio_usd=Decimal("10.00"))
        cls.hamburguesa = Producto.objects.create(categoria=carnes, nombre="Hamburguesa", precio_usd=Decimal("8.00"))
        cls.agua = Producto.objects.create(categoria=carnes, nombre="Agua", precio_usd=Decimal("1.00"))
        Receta.objects.create(producto=cls.bistec, insumo=cls.res, cantidad=Decimal("0.500"))
        Receta.objects.create(producto=cls.hamburguesa, insumo=cls.res, cantidad=Decimal("0.200"))
        Receta.objects.create(producto=cls.hamburguesa, insumo=cls.pan, cantidad=Decimal("1.000"))

    def setUp(self):
        cache.clear()

    # ---- ayudas ---------------------------------------------------------
    def cliente(self, usuario) -> Client:
        c = Client()
        c.force_login(usuario)
        return c

    def pedido(self, mesa=None, mesero=None, items=(), enviar=True) -> Pedido:
        """Crea un pedido con `items=[(producto, cantidad[, observaciones])]`."""
        mesa = mesa or self.mesa1
        pedido = Pedido.objects.create(mesa=mesa, mesero=mesero or self.mesero)
        mesa.ocupar()
        for item in items:
            producto, cantidad, *resto = item
            DetallePedido.objects.create(
                pedido=pedido,
                producto=producto,
                cantidad=cantidad,
                precio_unitario=producto.precio_local,
                observaciones=resto[0] if resto else "",
            )
        if enviar and items:
            pedido.enviar_a_cocina()
        return pedido

    def recargar(self, *objetos):
        for o in objetos:
            o.refresh_from_db()
