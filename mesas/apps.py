from django.apps import AppConfig


class MesasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mesas"

    def ready(self):
        from . import signals  # noqa: F401
