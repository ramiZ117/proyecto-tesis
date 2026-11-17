"""Tasa oficial del BCV, actualizada automáticamente.

El BCV no publica una API: se consulta DolarApi (ve.dolarapi.com), que lee la tasa oficial del
BCV y la entrega como JSON. Los precios del sistema están en dólares, así que al cambiar la tasa
todos los precios en bolívares (comanda, caja, menú digital) se actualizan solos.

Seguridad: una tasa que no es un número razonable se descarta; si cambia más de un 50 % frente a la
última tasa automática, no se publica sola (se pide confirmarla en la pantalla de tasa).
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections, transaction
from django.utils import timezone

registro = logging.getLogger("el_amir")

CLAVE_REVISION = "tasa:bcv:revisada"
VARIACION_MAXIMA_AUTOMATICA = Decimal("0.50")


@dataclass
class Resultado:
    actualizada: bool
    mensaje: str
    valor: Decimal | None = None


def consultar() -> tuple[Decimal, date | None]:
    """Valor oficial (Bs por USD) y su fecha según la fuente configurada."""
    peticion = urllib.request.Request(settings.TASA_BCV_URL, headers={"User-Agent": "ElAmir-Restaurante/2.1"})
    with urllib.request.urlopen(peticion, timeout=8) as respuesta:
        datos = json.load(respuesta)
    crudo = datos.get("promedio") or datos.get("venta") or datos.get("precio")
    try:
        valor = Decimal(str(crudo)).quantize(Decimal("0.0001"))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"La fuente devolvió una tasa no numérica: {crudo!r}") from exc
    if not valor.is_finite() or valor <= 0 or valor > Decimal("100000000"):
        raise ValueError(f"Tasa fuera de rango: {valor}")
    fecha = None
    texto_fecha = str(datos.get("fechaActualizacion") or "")[:10]
    if texto_fecha:
        try:
            fecha = date.fromisoformat(texto_fecha)
        except ValueError:
            fecha = None
    return valor, fecha


@transaction.atomic
def publicar(valor: Decimal, fecha: date | None, forzar: bool = False) -> Resultado:
    from caja.models import TasaCambio

    vigente = TasaCambio.objects.select_for_update().filter(activa=True).order_by("-vigente_desde").first()
    if vigente and vigente.valor == valor:
        return Resultado(False, f"La tasa del BCV sigue en {valor} Bs; no hay cambios.", valor)
    if vigente and vigente.fuente == TasaCambio.Fuente.BCV and not forzar:
        variacion = abs(valor / vigente.valor - 1)
        if variacion > VARIACION_MAXIMA_AUTOMATICA:
            registro.warning("Tasa BCV %s no publicada: cambia %.0f%% frente a %s.", valor, variacion * 100, vigente.valor)
            return Resultado(False, f"El BCV reporta {valor} Bs, un cambio de {variacion * 100:.0f} %: confírmalo manualmente.", valor)
    TasaCambio.objects.filter(activa=True).update(activa=False)
    TasaCambio.objects.create(valor=valor, fuente=TasaCambio.Fuente.BCV, fecha_valor=fecha, activa=True)
    registro.info("Tasa BCV publicada: %s Bs (fecha valor %s).", valor, fecha)
    return Resultado(True, f"Tasa del BCV actualizada: 1 USD = {valor} Bs.", valor)


def actualizar(forzar: bool = False) -> Resultado:
    """Consulta la fuente y publica la tasa si cambió. Nunca lanza: devuelve el resultado."""
    try:
        valor, fecha = consultar()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        registro.warning("No se pudo consultar la tasa del BCV: %s", exc)
        return Resultado(False, "No se pudo consultar la tasa del BCV (sin conexión o la fuente no respondió). Se mantiene la tasa vigente.")
    return publicar(valor, fecha, forzar)


def revisar_en_segundo_plano() -> None:
    """Si toca revisar (cada TASA_BCV_HORAS), consulta el BCV en un hilo aparte: ninguna pantalla espera."""
    if not settings.TASA_BCV_AUTOMATICA:
        return
    if not cache.add(CLAVE_REVISION, timezone.now().isoformat(), settings.TASA_BCV_HORAS * 3600):
        return  # ya se revisó hace poco (o hay una revisión en curso)

    def tarea():
        try:
            actualizar()
        finally:
            close_old_connections()

    threading.Thread(target=tarea, name="tasa-bcv", daemon=True).start()
