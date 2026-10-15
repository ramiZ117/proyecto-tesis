"""
Configuración del proyecto.

Los valores sensibles se leen de variables de entorno (o de un archivo `.env`
en la raíz, ver `.env.example`). Sin variables, el proyecto arranca en modo
desarrollo (DEBUG activo, SQLite).
"""

import os
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

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    # HTTPS: actívalo (DJANGO_HTTPS=1) cuando el sitio se sirva detrás de un certificado válido.
    if os.environ.get("DJANGO_HTTPS", "0") == "1":
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
    'inventario.apps.InventarioConfig',
    'mesas.apps.MesasConfig',
    'cocina.apps.CocinaConfig',
    'caja.apps.CajaConfig',
]

MIDDLEWARE = [
    'config.middleware.RespuestaParcialMiddleware',
    'django.middleware.security.SecurityMiddleware',
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
                'config.context_processors.alertas_inventario',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Base de datos: SQLite por defecto; PostgreSQL con DB_ENGINE=postgresql
# (recomendado en producción: SQLite ignora select_for_update).
if os.environ.get("DB_ENGINE") == "postgresql":
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get("DB_NAME", "el_amir_db"),
            'USER': os.environ.get("DB_USER", "postgres"),
            'PASSWORD': os.environ.get("DB_PASSWORD", ""),
            'HOST': os.environ.get("DB_HOST", "localhost"),
            'PORT': os.environ.get("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


LANGUAGE_CODE = 'es-ve'
TIME_ZONE = 'America/Caracas'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / "static"]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "inicio"
LOGOUT_REDIRECT_URL = "login"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Cocina (KDS): minutos de espera para resaltar un pedido como demorado / crítico.
KDS_MINUTOS_ALERTA = int(os.environ.get("KDS_MINUTOS_ALERTA", "15"))
KDS_MINUTOS_CRITICO = int(os.environ.get("KDS_MINUTOS_CRITICO", "25"))
