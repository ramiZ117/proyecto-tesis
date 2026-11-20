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
    from caja.ticket import ticket_factura

    contexto = {
        "titulo": f"Factura {factura.numero}",
        "saludo": f"Gracias por tu visita a {settings.RESTAURANTE_NOMBRE}. Esta es tu factura:",
        "ticket": ticket_factura(factura),
        "pie": settings.RESTAURANTE_DIRECCION,
    }
    mensaje = EmailMultiAlternatives(
        subject=f"Tu factura {factura.numero} · {settings.RESTAURANTE_NOMBRE}",
        body=render_to_string("caja/correo/factura.txt", contexto),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[factura.cliente_correo],
    )
    mensaje.attach_alternative(render_to_string("caja/correo/ticket.html", contexto), "text/html")
    try:
        mensaje.send(fail_silently=False)
    except Exception as exc:  # SMTP caído, credenciales malas, sin internet…
        registro.warning("No se pudo enviar la factura %s: %s", factura.numero, exc)
        Factura.objects.filter(pk=factura.pk).update(correo_error=str(exc)[:200])
        return False, motivo_error_correo(exc)
    Factura.objects.filter(pk=factura.pk).update(correo_enviado_en=timezone.now(), correo_error="")
    registro.info("Factura %s enviada a %s", factura.numero, factura.cliente_correo)
    return True, f"Factura enviada a {factura.cliente_correo}."


def destinos_cierre() -> list[str]:
    """Adónde va la nota de cierre: CIERRE_CORREOS del .env o, si está vacío, los administradores con correo."""
    if settings.CIERRE_CORREOS:
        return list(settings.CIERRE_CORREOS)
    from django.contrib.auth.models import User
    from django.db.models import Q

    from config.permisos import GRUPO_ADMIN

    admins = User.objects.filter(Q(is_superuser=True) | Q(groups__name=GRUPO_ADMIN), is_active=True).exclude(email="")
    return sorted(set(admins.values_list("email", flat=True)))


def enviar_cierre(cierre, destinos: list[str]) -> tuple[bool, str]:
    """Envía la nota de cierre de caja. Devuelve (enviado, mensaje). Nunca lanza."""
    from caja.models import CierreCaja
    from caja.ticket import ticket_cierre

    if not destinos:
        return False, "No hay a quién enviar la nota: escribe un correo."
    cierre = CierreCaja.objects.select_related("cajero").get(pk=cierre.pk)
    contexto = {
        "titulo": f"Cierre de caja {cierre.fecha:%d/%m/%Y}",
        "saludo": f"Nota del cierre de caja n.º {cierre.secuencia} de {cierre.cajero.username} "
                  f"({cierre.fecha:%d/%m/%Y}).",
        "ticket": ticket_cierre(cierre),
        "pie": settings.RESTAURANTE_NOMBRE,
    }
    mensaje = EmailMultiAlternatives(
        subject=f"Cierre de caja {cierre.fecha:%d/%m/%Y} · {cierre.cajero.username} · {settings.RESTAURANTE_NOMBRE}",
        body=render_to_string("caja/correo/factura.txt", contexto),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=destinos,
    )
    mensaje.attach_alternative(render_to_string("caja/correo/ticket.html", contexto), "text/html")
    try:
        mensaje.send(fail_silently=False)
    except Exception as exc:
        registro.warning("No se pudo enviar el cierre %s: %s", cierre.pk, exc)
        CierreCaja.objects.filter(pk=cierre.pk).update(correo_error=str(exc)[:200])
        return False, motivo_error_correo(exc)
    texto = ", ".join(destinos)
    CierreCaja.objects.filter(pk=cierre.pk).update(correo_enviado_a=texto[:300], correo_enviado_en=timezone.now(),
                                                    correo_error="")
    registro.info("Cierre %s enviado a %s", cierre.pk, texto)
    return True, f"Nota de cierre enviada a {texto}."


def cierre_en_segundo_plano(cierre) -> None:
    destinos = destinos_cierre()
    if not destinos:
        return
    if settings.CORREO_SINCRONO:
        enviar_cierre(cierre, destinos)
        return

    def tarea():
        try:
            enviar_cierre(cierre, destinos)
        finally:
            close_old_connections()

    threading.Thread(target=tarea, daemon=True).start()


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



def motivo_error_correo(exc) -> str:
    """Explica en palabras simples por qué el servicio de correo rechazó el envío."""
    import smtplib
    import socket

    texto = str(exc).lower()
    if "testing emails" in texto or "verify a domain" in texto or "own email" in texto:
        return ("Resend está en modo de prueba: solo envía al correo dueño de la cuenta. Para enviar a los "
                "clientes hay que verificar un dominio en Resend.")
    if isinstance(exc, smtplib.SMTPAuthenticationError) or "authentication" in texto or "535" in texto:
        return "El servicio de correo rechazó el usuario o la clave (revisa EMAIL_HOST_USER y EMAIL_HOST_PASSWORD en el .env)."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "El servicio de correo rechazó la dirección del cliente. Revisa que el correo esté bien escrito."
    if isinstance(exc, (socket.gaierror, ConnectionRefusedError, TimeoutError, socket.timeout)) or "timed out" in texto:
        return "No hubo conexión con el servicio de correo (sin internet o el puerto está bloqueado)."
    return f"No se pudo enviar el correo: {str(exc)[:160]}"
