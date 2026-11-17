from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from caja.models import TasaCambio
from config.permisos import GRUPO_ADMIN, GRUPO_CAJA, GRUPO_COCINA, GRUPO_MESERO
from inventario.models import Categoria, Insumo, Producto, Proveedor, Receta
from mesas.models import Mesa

CLAVE_DEMO = "tesis1234"
TASA_USD_A_LOCAL = Decimal("40.0000")
USUARIOS_ANTIGUOS = ("mesero", "cocinero", "cajero")


class Command(BaseCommand):
    help = "Carga roles, usuarios de prueba, mesas, proveedores, insumos, la carta árabe en USD y la tasa de cambio."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=CLAVE_DEMO,
            help="Contraseña de los usuarios de prueba (por defecto una clave de demostración).",
        )
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Reasigna la contraseña también a usuarios que ya existían.",
        )
        parser.add_argument(
            "--reset-stock",
            action="store_true",
            help="Devuelve el stock de los insumos a los valores de demostración.",
        )

    def handle(self, *args, **options):
        clave = options["password"]
        self.reset_stock = options["reset_stock"]
        grupos = {
            nombre: Group.objects.get_or_create(name=nombre)[0]
            for nombre in (GRUPO_ADMIN, GRUPO_MESERO, GRUPO_COCINA, GRUPO_CAJA)
        }

        usuarios = [
            ("mesero1", GRUPO_MESERO),
            ("chef1", GRUPO_COCINA),
            ("cajero1", GRUPO_CAJA),
            ("admin", GRUPO_ADMIN),
        ]
        for username, grupo in usuarios:
            es_admin = grupo == GRUPO_ADMIN
            user, creado = User.objects.get_or_create(username=username)
            if creado or options["reset_passwords"]:
                user.set_password(clave)
            user.is_active = True
            user.is_staff = es_admin
            user.is_superuser = es_admin
            user.save()
            user.groups.set([grupos[grupo]])
            self.stdout.write(f"  Usuario {username} ({grupo})")
        if clave == CLAVE_DEMO:
            self.stdout.write(
                self.style.WARNING("  Contraseña de demostración en uso: cámbiala con --password antes de entregar.")
            )

        # Usuarios de la primera versión del seed: se desactivan (no se borran para conservar su historial).
        desactivados = User.objects.filter(username__in=USUARIOS_ANTIGUOS, is_active=True).update(is_active=False)
        if desactivados:
            self.stdout.write(f"  Usuarios antiguos desactivados: {desactivados}")

        cajero = User.objects.get(username="cajero1")
        tasa = TasaCambio.objects.filter(activa=True).order_by("-vigente_desde").first()
        if tasa is None:
            tasa = TasaCambio.objects.create(valor=TASA_USD_A_LOCAL, creada_por=cajero, activa=True)
        self.stdout.write(f"  Tasa vigente: 1 USD = {tasa.valor} Bs.")

        self._mesas()
        proveedores = self._proveedores()
        insumos = self._insumos(proveedores)
        self._carta(insumos)
        self.stdout.write(self.style.SUCCESS("Carga inicial lista. Usuarios: mesero1, chef1, cajero1 y admin."))

    # ---- salón -------------------------------------------------------------------
    def _mesas(self):
        plano = [(1, 2, 0, 0), (2, 4, 1, 0), (3, 4, 2, 0), (4, 6, 3, 0),
                 (5, 8, 0, 1), (6, 2, 1, 1), (7, 4, 2, 1), (8, 6, 3, 1)]
        for numero, capacidad, x, y in plano:
            mesa, _ = Mesa.objects.get_or_create(numero=numero, defaults={"capacidad": capacidad, "pos_x": x, "pos_y": y})
            mesa.capacidad, mesa.pos_x, mesa.pos_y, mesa.activa = capacidad, x, y, True
            mesa.save(update_fields=["capacidad", "pos_x", "pos_y", "activa"])
        Mesa.objects.filter(numero__gt=len(plano)).update(activa=False)
        self.stdout.write(f"  {len(plano)} mesas en el plano del salón.")

    # ---- proveedores e insumos ---------------------------------------------------------
    def _proveedores(self):
        datos = [
            ("Carnicería Al Sham", "Carnes de res y cordero"),
            ("Avícola Oriente", "Pollo"),
            ("Distribuidora Damasco", "Granos, tahini, especias, té y café"),
            ("Panadería Beirut", "Pan árabe y masa filo"),
            ("Mercado de Puerto La Cruz", "Vegetales, hierbas y limón"),
            ("Lácteos del Oriente", "Yogur y queso blanco"),
        ]
        proveedores = {}
        for nombre, contacto in datos:
            proveedores[nombre], _ = Proveedor.objects.update_or_create(
                nombre=nombre, defaults={"contacto": contacto, "activo": True}
            )
        self.stdout.write(f"  {len(datos)} proveedores.")
        return proveedores

    def _insumos(self, proveedores):
        U = Insumo.Unidad
        G = Insumo.Grupo
        # nombre, grupo, unidad, stock, mínimo, reposición, máximo, costo USD por unidad, proveedor
        datos = [
            ("Carne de cordero", G.CARNES, U.KILOGRAMO, "14", "4", "6", "25", "11.50", "Carnicería Al Sham"),
            ("Carne de res molida", G.CARNES, U.KILOGRAMO, "12", "3", "5", "20", "6.80", "Carnicería Al Sham"),
            ("Pechuga de pollo", G.CARNES, U.KILOGRAMO, "18", "4", "6", "30", "4.20", "Avícola Oriente"),
            ("Garbanzo", G.GRANOS, U.KILOGRAMO, "22", "5", "8", "40", "2.10", "Distribuidora Damasco"),
            ("Trigo burgol", G.GRANOS, U.KILOGRAMO, "6", "2", "3", "15", "2.60", "Distribuidora Damasco"),
            ("Arroz", G.GRANOS, U.KILOGRAMO, "12", "2", "4", "20", "1.40", "Distribuidora Damasco"),
            ("Pan árabe", G.PANADERIA, U.UNIDAD, "80", "10", "30", "120", "0.25", "Panadería Beirut"),
            ("Masa filo", G.PANADERIA, U.KILOGRAMO, "3", "1", "1.5", "6", "7.00", "Panadería Beirut"),
            ("Perejil", G.VEGETALES, U.KILOGRAMO, "1.1", "0.5", "1", "4", "3.20", "Mercado de Puerto La Cruz"),
            ("Hierbabuena", G.VEGETALES, U.KILOGRAMO, "1.5", "0.3", "0.5", "2", "5.00", "Mercado de Puerto La Cruz"),
            ("Tomate", G.VEGETALES, U.KILOGRAMO, "10", "2", "3", "15", "1.50", "Mercado de Puerto La Cruz"),
            ("Cebolla", G.VEGETALES, U.KILOGRAMO, "9", "2", "3", "15", "1.20", "Mercado de Puerto La Cruz"),
            ("Berenjena", G.VEGETALES, U.KILOGRAMO, "6", "1", "2", "12", "1.80", "Mercado de Puerto La Cruz"),
            ("Limón", G.VEGETALES, U.KILOGRAMO, "6", "2", "3", "12", "1.80", "Mercado de Puerto La Cruz"),
            ("Ajo", G.VEGETALES, U.KILOGRAMO, "2", "0.5", "1", "4", "3.50", "Mercado de Puerto La Cruz"),
            ("Hojas de parra", G.VEGETALES, U.UNIDAD, "120", "30", "50", "300", "0.08", "Distribuidora Damasco"),
            ("Yogur natural", G.LACTEOS, U.LITRO, "8", "2", "4", "15", "3.00", "Lácteos del Oriente"),
            ("Queso blanco", G.LACTEOS, U.KILOGRAMO, "4", "1", "2", "8", "6.50", "Lácteos del Oriente"),
            ("Tahini", G.ESPECIAS, U.KILOGRAMO, "0.6", "0.5", "1", "6", "9.50", "Distribuidora Damasco"),
            ("Aceite de oliva", G.ESPECIAS, U.LITRO, "6", "1", "2", "10", "8.00", "Distribuidora Damasco"),
            ("Pistacho", G.ESPECIAS, U.KILOGRAMO, "2", "0.5", "1", "4", "18.00", "Distribuidora Damasco"),
            ("Azúcar", G.OTROS, U.KILOGRAMO, "10", "2", "4", "20", "1.10", "Mercado de Puerto La Cruz"),
            ("Té negro en hojas", G.BEBIDAS, U.KILOGRAMO, "1.5", "0.3", "0.5", "3", "14.00", "Distribuidora Damasco"),
            ("Café árabe con cardamomo", G.BEBIDAS, U.KILOGRAMO, "1.2", "0.3", "0.5", "3", "16.00", "Distribuidora Damasco"),
        ]
        insumos = {}
        for nombre, grupo, unidad, stock, minimo, rep, maximo, costo, prov in datos:
            insumo, creado = Insumo.objects.get_or_create(nombre=nombre, defaults={"stock_actual": Decimal(stock)})
            insumo.grupo, insumo.unidad = grupo, unidad
            insumo.stock_minimo, insumo.punto_reposicion, insumo.stock_maximo = Decimal(minimo), Decimal(rep), Decimal(maximo)
            insumo.costo_unitario, insumo.proveedor, insumo.activo = Decimal(costo), proveedores[prov], True
            if creado or self.reset_stock:
                insumo.stock_actual = Decimal(stock)
            insumo.save()
            insumos[nombre] = insumo
        retirados = Insumo.objects.exclude(nombre__in=insumos).filter(activo=True).update(activo=False)
        extra = f" ({retirados} insumos anteriores retirados)" if retirados else ""
        self.stdout.write(f"  {len(datos)} insumos con niveles, costo y proveedor{extra}.")
        return insumos

    # ---- carta y recetas ---------------------------------------------------------------
    def _carta(self, i):
        E = Categoria.Estacion
        categorias = {}
        definicion = [("Shawarmas", E.PARRILLA), ("Platos", E.PARRILLA), ("Entradas frías", E.FRIOS),
                      ("Postres", E.FRIOS), ("Bebidas", E.BEBIDAS)]
        for orden, (nombre, estacion) in enumerate(definicion, start=1):
            categorias[nombre], _ = Categoria.objects.update_or_create(
                nombre=nombre, defaults={"estacion": estacion, "orden": orden, "activa": True}
            )
        Categoria.objects.exclude(nombre__in=categorias).update(activa=False)

        carta = [
            ("Shawarmas", "Shawarma de pollo", "6.00", "Pollo marinado, pan árabe, tomate y salsa de ajo.",
             [("Pechuga de pollo", "0.180"), ("Pan árabe", "1"), ("Tomate", "0.030"), ("Ajo", "0.010")]),
            ("Shawarmas", "Shawarma de carne", "7.00", "Carne de res, cebolla, tomate y tahini.",
             [("Carne de res molida", "0.180"), ("Pan árabe", "1"), ("Tomate", "0.030"), ("Cebolla", "0.030"), ("Tahini", "0.020")]),
            ("Shawarmas", "Shawarma mixto", "8.00", "Pollo y carne con tahini.",
             [("Pechuga de pollo", "0.100"), ("Carne de res molida", "0.100"), ("Pan árabe", "1"), ("Tomate", "0.030"), ("Tahini", "0.020")]),
            ("Shawarmas", "Shawarma de falafel", "5.50", "Falafel, tahini y vegetales.",
             [("Garbanzo", "0.100"), ("Pan árabe", "1"), ("Tahini", "0.030"), ("Tomate", "0.030")]),
            ("Platos", "Plato mixto árabe", "14.00", "Cordero, pollo, hummus, tabule y pan árabe.",
             [("Carne de cordero", "0.150"), ("Pechuga de pollo", "0.120"), ("Garbanzo", "0.080"), ("Tahini", "0.030"),
              ("Pan árabe", "2"), ("Perejil", "0.030"), ("Trigo burgol", "0.030")]),
            ("Platos", "Kafta a la parrilla", "10.00", "Carne molida con perejil y cebolla, a la brasa.",
             [("Carne de res molida", "0.250"), ("Cebolla", "0.050"), ("Perejil", "0.020"), ("Pan árabe", "1")]),
            ("Platos", "Kibbe frito (4 und)", "6.00", "Croquetas de trigo burgol rellenas de carne.",
             [("Carne de res molida", "0.150"), ("Trigo burgol", "0.100"), ("Cebolla", "0.030"), ("Aceite de oliva", "0.030")]),
            ("Platos", "Falafel (6 und)", "5.00", "Garbanzo y hierbas, con tahini.",
             [("Garbanzo", "0.150"), ("Perejil", "0.020"), ("Ajo", "0.010"), ("Aceite de oliva", "0.050"), ("Tahini", "0.020")]),
            ("Entradas frías", "Hummus con pan árabe", "4.50", "Puré de garbanzo con tahini y aceite de oliva.",
             [("Garbanzo", "0.120"), ("Tahini", "0.040"), ("Limón", "0.030"), ("Aceite de oliva", "0.020"), ("Pan árabe", "1")]),
            ("Entradas frías", "Tabule", "4.00", "Perejil, burgol, tomate, limón y hierbabuena.",
             [("Perejil", "0.080"), ("Trigo burgol", "0.040"), ("Tomate", "0.060"), ("Limón", "0.040"),
              ("Aceite de oliva", "0.020"), ("Hierbabuena", "0.010")]),
            ("Entradas frías", "Babaganoush", "4.50", "Berenjena asada con tahini.",
             [("Berenjena", "0.250"), ("Tahini", "0.040"), ("Limón", "0.020"), ("Ajo", "0.005")]),
            ("Entradas frías", "Hojas de parra (6 und)", "5.00", "Rellenas de arroz, con limón.",
             [("Hojas de parra", "6"), ("Arroz", "0.060"), ("Limón", "0.020"), ("Aceite de oliva", "0.020")]),
            ("Postres", "Baklava", "3.00", "Masa filo, pistacho y almíbar.",
             [("Masa filo", "0.060"), ("Pistacho", "0.030"), ("Azúcar", "0.030")]),
            ("Postres", "Knafeh", "4.00", "Masa filo y queso dulce.",
             [("Masa filo", "0.080"), ("Queso blanco", "0.080"), ("Azúcar", "0.040")]),
            ("Bebidas", "Té árabe", "1.50", "Té negro con hierbabuena.",
             [("Té negro en hojas", "0.006"), ("Hierbabuena", "0.003"), ("Azúcar", "0.010")]),
            ("Bebidas", "Café árabe", "1.50", "Café con cardamomo.",
             [("Café árabe con cardamomo", "0.010"), ("Azúcar", "0.008")]),
            ("Bebidas", "Limonada con hierbabuena", "2.50", "Limón natural y hierbabuena.",
             [("Limón", "0.120"), ("Hierbabuena", "0.008"), ("Azúcar", "0.030")]),
            ("Bebidas", "Ayran", "2.00", "Bebida de yogur salada.",
             [("Yogur natural", "0.250")]),
        ]
        vigentes = []
        for cat, nombre, usd, descripcion, receta in carta:
            prod, _ = Producto.objects.update_or_create(
                categoria=categorias[cat], nombre=nombre,
                defaults={"descripcion": descripcion, "precio_usd": Decimal(usd), "disponible": True},
            )
            Receta.objects.filter(producto=prod).exclude(insumo__nombre__in=[n for n, _ in receta]).delete()
            for insumo_nombre, cantidad in receta:
                Receta.objects.update_or_create(producto=prod, insumo=i[insumo_nombre], defaults={"cantidad": Decimal(cantidad)})
            vigentes.append(prod.pk)
        Producto.objects.exclude(pk__in=vigentes).update(disponible=False)
        self.stdout.write(f"  Carta árabe: {len(carta)} platos en {len(categorias)} categorías, con recetas.")
