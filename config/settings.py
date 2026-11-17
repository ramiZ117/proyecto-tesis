"""
Configuración del proyecto.

Los valores sensibles se leen de variables de entorno (o de un archivo `.env`
en la raíz, ver `.env.example`). Sin variables, el proyecto arranca en modo
desarrollo (DEBUG activo, SQLite).
"""

import os
import sys
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
    'django.contrib.admin',
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
]

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
