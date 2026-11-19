from django.http import HttpResponseBase


class RespuestaParcialMiddleware:
    """Si la pantalla pidió un cambio sin recargar, una redirección vuelve como 204 + X-Next.

    El navegador pide esa dirección con el mismo encabezado y sustituye solo el contenido.
    Sin el encabezado, las redirecciones siguen igual (las pruebas y el navegador sin JS).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.parcial = request.headers.get("X-Partial") == "1"
        response = self.get_response(request)
        if request.parcial and response.status_code in (301, 302, 303, 307, 308):
            destino = response.get("Location", "")
            response.status_code = 204
            if isinstance(response, HttpResponseBase):
                response.content = b""
            # Si Location sigue presente, el navegador navega a esa dirección y la página sube arriba.
            del response["Location"]
            response["X-Next"] = destino
        return response



class ErroresJsonMiddleware:
    """En las llamadas del menú digital (que esperan JSON), un error del servidor responde JSON con el
    motivo, para que el teléfono no lo confunda con «sin conexión». El detalle queda en el registro."""

    RUTAS = ("/menu/",)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if request.method != "POST" or not request.path.startswith(self.RUTAS):
            return None
        import logging

        from django.conf import settings
        from django.http import JsonResponse

        logging.getLogger("el_amir").exception("Error en %s", request.path)
        detalle = f" ({type(exception).__name__}: {exception})" if settings.DEBUG else ""
        return JsonResponse({"error": "El sistema tuvo un problema al procesar tu solicitud. "
                                      "Avisa al personal." + detalle}, status=500)


def csrf_fallido(request, reason=""):
    """Si el navegador no envió la cookie de seguridad (CSRF), se explica en vez de mostrar «sin conexión»."""
    from django.http import JsonResponse
    from django.views.csrf import csrf_failure

    if request.path.startswith("/menu/") or "application/json" in request.headers.get("Content-Type", ""):
        return JsonResponse({"error": "La página quedó desactualizada o el navegador bloqueó la cookie de seguridad "
                                      "del sitio. Recarga la página e intenta de nuevo.", "motivo": reason}, status=403)
    return csrf_failure(request, reason=reason)
