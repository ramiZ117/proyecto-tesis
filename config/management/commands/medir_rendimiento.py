"""Prueba de rendimiento y concurrencia (tesis 4.5: RNF-01 y RNF-02).

    python manage.py medir_rendimiento                      # 6 usuarios simultáneos, 5 rondas
    python manage.py medir_rendimiento --usuarios 10 --rondas 8 --salida docs/resultados-rendimiento.md

Simula meseros y cajeros trabajando a la vez sobre las mismas mesas: abren la comanda, agregan
platos, la envían a cocina, la caja factura y cobra y el salón libera la mesa. Mide el tiempo de
respuesta de cada operación y, al final, comprueba que la concurrencia no rompió ninguna regla.

Por seguridad trabaja SIEMPRE sobre una copia temporal de la base de datos: tus datos no cambian.
"""

import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Count, Q, Sum
from django.test import Client
from django.utils import timezone

VARIABLE_COPIA = "EL_AMIR_MEDICION_EN_COPIA"


class Command(BaseCommand):
    help = "Mide tiempos de respuesta con usuarios simultáneos y verifica la integridad (sobre una copia de la base)."

    def add_arguments(self, parser):
        parser.add_argument("--usuarios", type=int, default=6, help="Meseros/cajeros trabajando a la vez.")
        parser.add_argument("--rondas", type=int, default=5, help="Ciclos completos de mesa por usuario.")
        parser.add_argument("--salida", default="", help="Archivo Markdown donde guardar el informe.")
        parser.add_argument("--semilla", type=int, default=2026)

    # ---- preparación: copia de la base -------------------------------------------------
    def handle(self, *args, **op):
        if op["usuarios"] < 1 or op["rondas"] < 1:
            raise CommandError("--usuarios y --rondas deben ser al menos 1.")
        if os.environ.get(VARIABLE_COPIA) != "1":
            return self._relanzar_sobre_copia()
        return self._medir(op)

    def _relanzar_sobre_copia(self):
        origen = Path(settings.DATABASES["default"]["NAME"])
        if not origen.exists():
            raise CommandError(f"No existe la base {origen}. Ejecuta migrate y seed_restaurante primero.")
        carpeta = Path(tempfile.mkdtemp(prefix="el_amir_medicion_"))
        copia = carpeta / "medicion.sqlite3"
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(origen)) as fuente, closing(sqlite3.connect(copia)) as destino:
            fuente.backup(destino)
        self.stdout.write(f"Trabajando sobre una copia temporal de la base: {copia}")
        entorno = dict(os.environ, DB_PATH=str(copia), **{VARIABLE_COPIA: "1"})
        try:
            resultado = subprocess.run([sys.executable, "manage.py", *sys.argv[1:]], env=entorno, cwd=settings.BASE_DIR)
        finally:
            shutil.rmtree(carpeta, ignore_errors=True)
        if resultado.returncode:
            raise CommandError("La medición terminó con errores.")

    # ---- medición -----------------------------------------------------------------------
    def _medir(self, op):
        from django.contrib.auth.models import User

        from mesas.models import Mesa

        random.seed(op["semilla"])
        if "testserver" not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.append("testserver")
        try:
            mesero = User.objects.get(username="mesero1")
            cajero = User.objects.get(username="cajero1")
            chef = User.objects.get(username="chef1")
        except User.DoesNotExist as exc:
            raise CommandError("Faltan los usuarios de prueba: ejecuta `python manage.py seed_restaurante`.") from exc
        from inventario.models import Producto

        platos = list(Producto.objects.filter(disponible=True, categoria__activa=True).values_list("pk", flat=True))
        mesas = list(Mesa.objects.filter(activa=True).values_list("pk", flat=True))
        if not platos or not mesas:
            raise CommandError("Faltan platos o mesas: ejecuta `python manage.py seed_restaurante`.")
        Mesa.objects.update(estado=Mesa.Estado.DISPONIBLE)
        connection.close()

        tiempos = defaultdict(list)
        errores = []
        rechazos = defaultdict(int)
        candado = threading.Lock()

        def medir(nombre, funcion):
            inicio = time.perf_counter()
            respuesta = funcion()
            ms = (time.perf_counter() - inicio) * 1000
            with candado:
                tiempos[nombre].append(ms)
                if respuesta.status_code >= 500:
                    errores.append(f"{nombre}: HTTP {respuesta.status_code}")
            return respuesta

        def rechazo(motivo):
            with candado:
                rechazos[motivo] += 1

        barrera = threading.Barrier(op["usuarios"])

        def trabajador(indice):
            try:
                sala, caja, cocina = Client(), Client(), Client()
                sala.force_login(mesero)
                caja.force_login(cajero)
                cocina.force_login(chef)
                # 1) Carrera: todos abren la MISMA mesa en el mismo instante.
                barrera.wait()
                medir("Abrir mesa (carrera)", lambda: sala.post(f"/mesas/abrir/{mesas[0]}/"))
                barrera.wait()
                # 2) Ciclos completos repartidos entre las mesas (varios usuarios por mesa).
                for ronda in range(op["rondas"]):
                    mesa = mesas[(indice + ronda) % len(mesas)]
                    r = medir("Abrir mesa", lambda: sala.post(f"/mesas/abrir/{mesa}/"))
                    destino = r.get("Location", "")
                    if "/mesas/comanda/" not in destino:
                        rechazo("Mesa por limpiar u ocupada por otro ciclo")
                        medir("Liberar mesa", lambda: sala.post(f"/mesas/limpiar/{mesa}/"))
                        continue
                    pedido = destino.rstrip("/").split("/")[-1]
                    for _ in range(2):
                        medir("Agregar plato", lambda: sala.post(f"/mesas/comanda/{pedido}/agregar/",
                                                                {"producto_id": random.choice(platos), "cantidad": 1}))
                    medir("Enviar a cocina", lambda: sala.post(f"/mesas/comanda/{pedido}/enviar/"))
                    medir("Ver monitor de cocina", lambda: cocina.get("/cocina/parcial/"))
                    medir("Ver mapa del salón", lambda: sala.get("/mesas/parcial/"))
                    r = medir("Emitir factura", lambda: caja.post(f"/caja/pedido/{pedido}/facturar/"))
                    destino = r.get("Location", "")
                    if "/caja/factura/" not in destino:
                        rechazo("Factura no emitida (otro cajero ya la emitió o la comanda quedó sin platos)")
                        continue
                    factura = destino.rstrip("/").split("/")[-1]
                    medir("Cobrar", lambda: caja.post(f"/caja/factura/{factura}/pago/",
                                                      {"metodo": "EFE", "monto_recibido": "999999"}))
                    medir("Ver cuentas de caja", lambda: caja.get("/caja/"))
                    medir("Liberar mesa", lambda: sala.post(f"/mesas/limpiar/{mesa}/"))
            except Exception as exc:  # cualquier excepción es un error de la prueba
                with candado:
                    errores.append(f"Usuario {indice}: {exc!r}")
            finally:
                connection.close()

        self.stdout.write(f"Midiendo con {op['usuarios']} usuarios simultáneos × {op['rondas']} rondas…")
        inicio = time.perf_counter()
        hilos = [threading.Thread(target=trabajador, args=(i,)) for i in range(op["usuarios"])]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        duracion = time.perf_counter() - inicio

        verificaciones = self._verificar()
        informe = self._informe(op, tiempos, errores, rechazos, verificaciones, duracion)
        self.stdout.write(informe)
        if op["salida"]:
            ruta = Path(op["salida"])
            if not ruta.is_absolute():
                ruta = settings.BASE_DIR / ruta
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(informe, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Informe guardado en {ruta}"))
        if errores or not all(ok for _, ok, _ in verificaciones):
            raise CommandError("Se encontraron errores o una regla de negocio no se cumplió.")

    # ---- verificación de integridad --------------------------------------------------------
    def _verificar(self):
        from caja.models import Factura
        from inventario.models import Insumo
        from mesas.models import Pedido

        abiertas = (
            Pedido.objects.filter(tipo=Pedido.Tipo.MESA).exclude(estado__in=["CER", "ANU"])
            .values("mesa").annotate(n=Count("id")).filter(n__gt=1).count()
        )
        numeros = list(Factura.objects.values_list("numero", flat=True))
        descuadres = 0
        for f in Factura.objects.filter(estado=Factura.Estado.PAGADA).annotate(pagado=Sum("pagos__monto_local")):
            if (f.pagado or Decimal("0")) != f.total_local:
                descuadres += 1
        vigentes_dobles = (
            Factura.objects.exclude(estado="ANU").values("pedido").annotate(n=Count("id")).filter(n__gt=1).count()
        )
        negativos = Insumo.objects.filter(Q(stock_actual__lt=0)).count()
        return [
            ("Una sola comanda abierta por mesa", abiertas == 0, f"{abiertas} mesas con más de una"),
            ("Números de factura únicos", len(numeros) == len(set(numeros)), f"{len(numeros)} facturas"),
            ("Una factura vigente por pedido", vigentes_dobles == 0, f"{vigentes_dobles} pedidos con dos"),
            ("Pagos = total en facturas pagadas", descuadres == 0, f"{descuadres} descuadres"),
            ("Stock nunca negativo", negativos == 0, f"{negativos} insumos negativos"),
        ]

    # ---- informe -----------------------------------------------------------------------------
    def _informe(self, op, tiempos, errores, rechazos, verificaciones, duracion):
        total = sum(len(v) for v in tiempos.values())
        todas = [ms for v in tiempos.values() for ms in v]
        p95 = lambda xs: sorted(xs)[max(0, int(round(len(xs) * 0.95)) - 1)]  # noqa: E731
        lineas = [
            "# Prueba de rendimiento y concurrencia",
            "",
            f"- Fecha: {timezone.localtime():%d/%m/%Y %H:%M}",
            f"- Usuarios simultáneos: {op['usuarios']} · rondas por usuario: {op['rondas']}",
            f"- Peticiones: {total} en {duracion:.1f} s ({total / duracion:.1f} por segundo)",
            f"- Base de datos: SQLite (modo IMMEDIATE + WAL), servidor de aplicación en el mismo equipo",
            "",
            "## Tiempos de respuesta (RNF-01: menos de 2 s)",
            "",
            "| Operación | Peticiones | Mediana (ms) | P95 (ms) | Máximo (ms) |",
            "|---|---:|---:|---:|---:|",
        ]
        for nombre in sorted(tiempos, key=lambda n: -statistics.median(tiempos[n])):
            v = tiempos[nombre]
            lineas.append(f"| {nombre} | {len(v)} | {statistics.median(v):.0f} | {p95(v):.0f} | {max(v):.0f} |")
        if todas:
            lineas.append(f"| **Todas** | **{len(todas)}** | **{statistics.median(todas):.0f}** | **{p95(todas):.0f}** | **{max(todas):.0f}** |")
            cumple = max(todas) < 2000
            lineas += ["", f"Resultado RNF-01: {'cumple' if cumple else 'NO cumple'} (máximo {max(todas):.0f} ms)."]
        lineas += ["", "## Integridad bajo concurrencia (RNF-02)", "", "| Regla | Resultado | Detalle |", "|---|---|---|"]
        for regla, ok, detalle in verificaciones:
            lineas.append(f"| {regla} | {'cumple' if ok else 'NO cumple'} | {detalle} |")
        lineas += ["", "## Rechazos esperados por las reglas de negocio", ""]
        if rechazos:
            lineas += [f"- {motivo}: {n}" for motivo, n in sorted(rechazos.items())]
        else:
            lineas.append("- Ninguno.")
        lineas += [
            "",
            "Un rechazo esperado es una operación que el sistema negó para proteger los datos (por ejemplo, "
            "dos cajeros intentando facturar la misma mesa): no es un error.",
            "",
            "## Errores",
            "",
        ]
        lineas += [f"- {e}" for e in errores] if errores else ["- Ninguno (sin respuestas 5xx ni excepciones)."]
        return "\n".join(lineas) + "\n"
