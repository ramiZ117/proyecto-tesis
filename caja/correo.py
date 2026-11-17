"""Envío de la factura al correo del cliente (SMTP configurable: Brevo, Mailgun, SendGrid, Resend…)."""

import logging
import threading

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections
from django.template.loader import render_to_string
from django.utils import timezone

registro = logging.getLogger("el_amir")


def enviar_factura(factura) -> tuple[bool, str]:
    """Envía la factura a `factura.cliente_correo`. Devuelve (enviado, mensaje). Nunca lanza."""
    from caja.models import Factura

    if not factura.cliente_correo:
        return False, "La factura no tiene correo del cliente."
    factura = (Factura.objects.select_related("pedido__mesa", "pedido__mesero", "cajero")
               .prefetch_related("pagos", "pedido__detalles__producto").get(pk=factura.pk))
    contexto = {
        "factura": factura,
        "detalles": [d for d in factura.pedido.detalles.all() if d.estado != "ANU"],
        "local": settings.RESTAURANTE_NOMBRE,
        "direccion": settings.RESTAURANTE_DIRECCION,
        "telefono": settings.RESTAURANTE_TELEFONO,
        "rif": getattr(settings, "RESTAURANTE_RIF", ""),
    }
    mensaje = EmailMultiAlternatives(
        subject=f"Tu factura {factura.numero} · {settings.RESTAURANTE_NOMBRE}",
        body=render_to_string("caja/correo/factura.txt", contexto),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[factura.cliente_correo],
    )
    mensaje.attach_alternative(render_to_string("caja/correo/factura.html", contexto), "text/html")
    try:
        mensaje.send(fail_silently=False)
    except Exception as exc:  # SMTP caído, credenciales malas, sin internet…
        registro.warning("No se pudo enviar la factura %s: %s", factura.numero, exc)
        Factura.objects.filter(pk=factura.pk).update(correo_error=str(exc)[:200])
        return False, "No se pudo enviar el correo. Revisa la configuración del servicio de correo o el internet."
    Factura.objects.filter(pk=factura.pk).update(correo_enviado_en=timezone.now(), correo_error="")
    registro.info("Factura %s enviada a %s", factura.numero, factura.cliente_correo)
    return True, f"Factura enviada a {factura.cliente_correo}."


def enviar_en_segundo_plano(factura) -> None:
    """Para no hacer esperar a la caja mientras responde el servidor de correo."""
    if not factura.cliente_correo:
        return
    if settings.CORREO_SINCRONO:
        enviar_factura(factura)
        return

    def tarea():
        try:
            enviar_factura(factura)
        finally:
            close_old_connections()

    threading.Thread(target=tarea, daemon=True).start()
