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
