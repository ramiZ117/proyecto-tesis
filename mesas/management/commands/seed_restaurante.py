from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from caja.models import TasaCambio
from config.permisos import GRUPO_ADMIN, GRUPO_CAJA, GRUPO_COCINA, GRUPO_MESERO
from inventario.models import Categoria, Insumo, Producto, Receta
from mesas.models import Mesa

CLAVE_DEMO = "tesis1234"
TASA_USD_A_LOCAL = Decimal("40.0000")
USUARIOS_ANTIGUOS = ("mesero", "cocinero", "cajero")


class Command(BaseCommand):
    help = "Carga roles, usuarios de prueba, mesas, inventario, menú en USD y tasa de cambio."

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

    def handle(self, *args, **options):
        clave = options["password"]
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

        Mesa.objects.filter(numero__gt=5).update(activa=False)
        for numero, capacidad, x, y in [
            (1, 2, 0, 0),
            (2, 4, 1, 0),
            (3, 4, 2, 0),
            (4, 6, 0, 1),
            (5, 8, 1, 1),
        ]:
            mesa, _ = Mesa.objects.update_or_create(
                numero=numero,
                defaults={
                    "capacidad": capacidad,
                    "pos_x": x,
                    "pos_y": y,
                    "activa": True,
                    "estado": Mesa.Estado.DISPONIBLE,
                },
            )
        self.stdout.write("  5 mesas en estado Disponible.")

        carnes, _ = Categoria.objects.get_or_create(nombre="Carnes")
        bebidas, _ = Categoria.objects.get_or_create(nombre="Bebidas")
        vegetales, _ = Categoria.objects.get_or_create(nombre="Vegetales")

        def insumo(nombre, grupo, unidad, stock, minimo):
            obj, _ = Insumo.objects.update_or_create(
                nombre=nombre,
                defaults={
                    "grupo": grupo,
                    "unidad": unidad,
                    "stock_actual": Decimal(stock),
                    "stock_minimo": Decimal(minimo),
                    "activo": True,
                },
            )
            return obj

        res = insumo("Res (lomito)", "CAR", Insumo.Unidad.KILOGRAMO, "20", "4")
        pollo = insumo("Pechuga de pollo", "CAR", Insumo.Unidad.KILOGRAMO, "18", "4")
        carne_molida = insumo("Carne molida", "CAR", Insumo.Unidad.KILOGRAMO, "12", "3")
        lechuga = insumo("Lechuga", "VEG", Insumo.Unidad.KILOGRAMO, "8", "2")
        tomate = insumo("Tomate", "VEG", Insumo.Unidad.KILOGRAMO, "10", "2")
        papa = insumo("Papa", "VEG", Insumo.Unidad.KILOGRAMO, "15", "3")
        cebolla = insumo("Cebolla", "VEG", Insumo.Unidad.KILOGRAMO, "9", "2")
        naranja = insumo("Naranja", "BEB", Insumo.Unidad.UNIDAD, "40", "10")
        parchita = insumo("Parchita", "BEB", Insumo.Unidad.UNIDAD, "35", "8")
        cafe = insumo("Café molido", "BEB", Insumo.Unidad.KILOGRAMO, "4", "0.5")
        soda = insumo("Soda", "BEB", Insumo.Unidad.LITRO, "30", "6")
        pan = insumo("Pan de hamburguesa", "VEG", Insumo.Unidad.UNIDAD, "40", "10")
        self.stdout.write("  Insumos de Carnes, Vegetales y Bebidas con stock.")

        def platillo(categoria, nombre, usd, descripcion, receta):
            prod, _ = Producto.objects.update_or_create(
                categoria=categoria,
                nombre=nombre,
                defaults={
                    "descripcion": descripcion,
                    "precio_usd": Decimal(usd),
                    "disponible": True,
                },
            )
            Receta.objects.filter(producto=prod).exclude(insumo__in=[item[0] for item in receta]).delete()
            for ins, cantidad in receta:
                Receta.objects.update_or_create(
                    producto=prod,
                    insumo=ins,
                    defaults={"cantidad": Decimal(cantidad)},
                )
            return prod

        platillo(
            carnes,
            "Bistec a la plancha",
            "12.00",
            "Lomito con papas salteadas.",
            [(res, "0.250"), (papa, "0.200"), (cebolla, "0.050")],
        )
        platillo(
            carnes,
            "Pollo asado",
            "9.00",
            "Pechuga asada con vegetales.",
            [(pollo, "0.280"), (tomate, "0.080"), (cebolla, "0.040")],
        )
        platillo(
            carnes,
            "Hamburguesa de la casa",
            "8.00",
            "Carne molida, pan y vegetales.",
            [(carne_molida, "0.180"), (pan, "1"), (lechuga, "0.030"), (tomate, "0.040")],
        )
        platillo(
            vegetales,
            "Ensalada César",
            "6.00",
            "Lechuga, tomate y aderezo de la casa.",
            [(lechuga, "0.120"), (tomate, "0.080")],
        )
        platillo(
            vegetales,
            "Sopa de vegetales",
            "5.00",
            "Papa, tomate y cebolla.",
            [(papa, "0.150"), (tomate, "0.080"), (cebolla, "0.050")],
        )
        platillo(
            bebidas,
            "Jugo de naranja",
            "3.00",
            "Jugo natural.",
            [(naranja, "3")],
        )
        platillo(
            bebidas,
            "Jugo de parchita",
            "3.00",
            "Jugo natural.",
            [(parchita, "2")],
        )
        platillo(
            bebidas,
            "Refresco",
            "2.00",
            "Soda de 350 ml.",
            [(soda, "0.350")],
        )
        platillo(
            bebidas,
            "Café negro",
            "1.50",
            "Café americano.",
            [(cafe, "0.018")],
        )

        vigentes = {
            "Bistec a la plancha",
            "Pollo asado",
            "Hamburguesa de la casa",
            "Ensalada César",
            "Sopa de vegetales",
            "Jugo de naranja",
            "Jugo de parchita",
            "Refresco",
            "Café negro",
        }
        Producto.objects.exclude(nombre__in=vigentes).update(disponible=False)
        Categoria.objects.filter(nombre__in=["Entradas", "Platos fuertes"]).update(activa=False)

        self.stdout.write("  Platillos con precio USD, equivalente en Bs. y recetas.")
        self.stdout.write(self.style.SUCCESS("Carga inicial lista. Usuarios: mesero1, chef1, cajero1 y admin."))
