"""
Configuración del proyecto.

Los valores sensibles se leen de variables de entorno (o de un archivo `.env`
en la raíz, ver `.env.example`). Sin variables, el proyecto arranca en modo
desarrollo (DEBUG activo, SQLite).
"""

import os
import sys
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _cargar_env(ruta: Path) -> None:
    """Lector mínimo de `.env` (CLAVE=valor), sin dependencias externas."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env(BASE_DIR / ".env")

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

_CLAVE_DESARROLLO = "django-insecure-solo-para-desarrollo-local-no-usar-en-produccion"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or (_CLAVE_DESARROLLO if DEBUG else None)
if not SECRET_KEY:
    raise ImproperlyConfigured("Define DJANGO_SECRET_KEY cuando DJANGO_DEBUG=0.")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

VERSION_APP = "2.1.0"

# CSRF: orígenes desde los que se aceptan formularios (p. ej. http://192.168.1.10:8000 en la red del local).
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_ORIGINS", "").split(",") if o.strip()]

if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    SESSION_COOKIE_HTTPONLY = True
    # HTTPS: actívalo (DJANGO_HTTPS=1) solo si el sitio se sirve con un certificado válido.
    # En la red interna del restaurante se usa HTTP: marcar las cookies como "solo HTTPS"
    # impediría iniciar sesión, por eso van dentro de esta condición.
    if os.environ.get("DJANGO_HTTPS", "0") == "1":
        SESSION_COOKIE_SECURE = True
        CSRF_COOKIE_SECURE = True
        SECURE_SSL_REDIRECT = True
        SECURE_HSTS_SECONDS = 31536000
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'config',  # comandos de operación: servir, respaldar, medir_rendimiento
    'inventario.apps.InventarioConfig',
    'mesas.apps.MesasConfig',
    'cocina.apps.CocinaConfig',
    'caja.apps.CajaConfig',
    'gestion',  # administración: carta, insumos, proveedores, mesas y menú digital
    'menu',  # menú digital público para los clientes (QR) con asistente de IA
]

MIDDLEWARE = [
    'config.middleware.RespuestaParcialMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # Sirve el CSS, el JS y el logo sin un servidor web aparte (también en producción).
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'config.middleware.ErroresJsonMiddleware',
]
CSRF_FAILURE_VIEW = 'config.middleware.csrf_fallido'

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'config.context_processors.parcial',
                'config.context_processors.roles',
                'config.context_processors.tasa',
                'config.context_processors.alertas_inventario',
                "config.context_processors.pedidos_en_linea",
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Base de datos: SQLite (un solo local, servidor en la intranet, respaldo = copiar un archivo).
#
# SQLite ignora select_for_update, así que la concurrencia se resuelve de otra forma:
# - transaction_mode=IMMEDIATE: cada transacción (transaction.atomic) toma el candado de
#   escritura al empezar. Dos cobros, dos aperturas de mesa o dos cierres simultáneos se
#   ejecutan uno detrás del otro, y el segundo ya ve lo que escribió el primero.
# - journal_mode=WAL: las lecturas (mapa, cocina, reportes) no esperan a las escrituras.
# - timeout: una transacción espera hasta 20 s por el candado en vez de fallar enseguida.
# Las reglas críticas además están protegidas con restricciones de unicidad en la base.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get("DB_PATH") or BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'transaction_mode': 'IMMEDIATE',
            'timeout': 20,
            'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;',
        },
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# En las pruebas automáticas, PBKDF2 con menos iteraciones: el cifrado real es lento a propósito y
# multiplicaba por diez la duración de la suite. Nunca aplica fuera de `manage.py test`.
if len(sys.argv) > 1 and sys.argv[1] == "test":
    PASSWORD_HASHERS = ["config.hashers_pruebas.PBKDF2Rapido"]

LANGUAGE_CODE = 'es-ve'
TIME_ZONE = 'America/Caracas'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / "static"]
# `python manage.py collectstatic` reúne aquí los archivos para producción.
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # En producción los archivos llevan una huella en el nombre (app.3f2a….css) y se
        # comprimen: el navegador nunca usa una versión vieja. En desarrollo y pruebas, normal.
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG or "test" in sys.argv[1:2]
        else "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Fotos de los platos (subidas desde la administración).
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_DIR") or BASE_DIR / "media")
FOTO_MAX_BYTES = 6 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = FOTO_MAX_BYTES + 1024 * 1024

# Menú digital para clientes. Dirección que codifican los QR (la que los teléfonos pueden abrir).
MENU_URL_PUBLICA = os.environ.get("MENU_URL_PUBLICA", "").rstrip("/")

# Datos del local que se muestran en el menú digital (seguir en Instagram, llamar y guardar contacto).
RESTAURANTE_NOMBRE = os.environ.get("RESTAURANTE_NOMBRE", "Comida Árabe El Amir")
RESTAURANTE_TELEFONO = os.environ.get("RESTAURANTE_TELEFONO", "0414-1111222")
RESTAURANTE_WHATSAPP = os.environ.get("RESTAURANTE_WHATSAPP", "584141111222")  # dígitos con código de país; vacío lo oculta
RESTAURANTE_INSTAGRAM = os.environ.get("RESTAURANTE_INSTAGRAM", "comidaarabeelamir_")
RESTAURANTE_DIRECCION = os.environ.get("RESTAURANTE_DIRECCION", "Calle Bolívar, Puerto La Cruz, Anzoátegui")
RESTAURANTE_RIF = os.environ.get("RESTAURANTE_RIF", "J-317688600")  # RIF del local para las facturas

# Asistente de IA del menú. Proveedor: gemini | groq | xai | local (sin conexión).
# Si no se indica, usa el primero que tenga clave; sin claves responde el asistente local.
IA_PROVEEDOR = os.environ.get("IA_PROVEEDOR", "").strip().lower()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Se pueden indicar varios separados por coma: si uno está saturado, se prueba el siguiente.
GEMINI_MODELO = os.environ.get("GEMINI_MODELO", "gemini-flash-lite-latest,gemini-flash-latest")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODELO = os.environ.get("GROQ_MODELO", "llama-3.3-70b-versatile")
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
XAI_MODELO = os.environ.get("XAI_MODELO", "grok-3-mini")
IA_MENSAJES_POR_HORA = int(os.environ.get("IA_MENSAJES_POR_HORA", "30"))

# Respaldos automáticos de la base (comando `respaldar`).
RESPALDOS_DIR = Path(os.environ.get("RESPALDOS_DIR") or BASE_DIR / "respaldos")
RESPALDOS_CONSERVAR = int(os.environ.get("RESPALDOS_CONSERVAR", "30"))

# Registro de errores: consola y archivo rotativo en logs/el_amir.log (5 archivos de 2 MB).
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {
        "consola": {"class": "logging.StreamHandler", "formatter": "simple"},
        "archivo": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOGS_DIR / "el_amir.log",
            "maxBytes": 2 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "simple",
        },
    },
    "root": {"handlers": ["consola", "archivo"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["consola", "archivo"], "level": "WARNING", "propagate": False},
        "el_amir": {"handlers": ["consola", "archivo"], "level": "INFO", "propagate": False},
    },
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "inicio"
LOGOUT_REDIRECT_URL = "login"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Cocina (KDS): minutos de espera para resaltar un pedido como demorado / crítico.
KDS_MINUTOS_ALERTA = int(os.environ.get("KDS_MINUTOS_ALERTA", "15"))
KDS_MINUTOS_CRITICO = int(os.environ.get("KDS_MINUTOS_CRITICO", "25"))

# Las pruebas automáticas nunca llaman a un servicio de IA real (ni gastan la cuota gratuita):
# las que lo necesitan lo simulan.
if len(sys.argv) > 1 and sys.argv[1] == "test":
    IA_PROVEEDOR = ""
    GEMINI_API_KEY = GROQ_API_KEY = XAI_API_KEY = ""

# Tasa oficial del BCV: se consulta sola cada TASA_BCV_HORAS horas (en segundo plano) y con el
# comando `python manage.py actualizar_tasa`. Fuente: DolarApi, que publica la tasa oficial del BCV.
TASA_BCV_AUTOMATICA = os.environ.get("TASA_BCV_AUTOMATICA", "1") == "1" and not (len(sys.argv) > 1 and sys.argv[1] == "test")
TASA_BCV_URL = os.environ.get("TASA_BCV_URL", "https://ve.dolarapi.com/v1/dolares/oficial")
TASA_BCV_HORAS = int(os.environ.get("TASA_BCV_HORAS", "2"))


# ---- Impuestos -------------------------------------------------------------------------------
# Los precios de la carta YA incluyen el IVA: al facturar se separa la base imponible y el impuesto.
IVA_PORCENTAJE = Decimal(os.environ.get("IVA_PORCENTAJE", "16"))
# IGTF: se suma al pago en divisas en efectivo (y a los métodos que se agreguen aquí).
IGTF_PORCENTAJE = Decimal(os.environ.get("IGTF_PORCENTAJE", "3"))
IGTF_METODOS = [m.strip() for m in os.environ.get("IGTF_METODOS", "DIV").split(",") if m.strip()]

# ---- Correo (factura al cliente) ---------------------------------------------------------------
# Cualquier servicio SMTP sirve. Para pruebas gratis: Brevo (300 correos/día) →
#   EMAIL_HOST=smtp-relay.brevo.com  EMAIL_PORT=587  EMAIL_HOST_USER=<login SMTP>  EMAIL_HOST_PASSWORD=<clave SMTP>
#   DEFAULT_FROM_EMAIL="Comida Árabe El Amir <correo-verificado@…>"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Comida Árabe El Amir <no-responder@elamir.local>")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST else "django.core.mail.backends.console.EmailBackend"
)
CORREO_SINCRONO = "test" in sys.argv  # en pruebas se envía en el acto para poder verificarlo

# ---- Archivos privados (comprobantes de pago): NO se publican en /media/ ----------------------
PRIVADO_ROOT = Path(os.environ.get("PRIVADO_ROOT", BASE_DIR / "privado"))
COMPROBANTE_MAX_BYTES = 6 * 1024 * 1024

# Platos que la cocina prepara a la vez (para estimar la espera del cliente).
COCINA_PLATOS_SIMULTANEOS = int(os.environ.get("COCINA_PLATOS_SIMULTANEOS", "3"))
