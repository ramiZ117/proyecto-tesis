from django.contrib.auth.models import Group
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from config.permisos import GRUPO_ADMIN, GRUPO_CAJA, GRUPO_COCINA, GRUPO_MESERO


@receiver(post_migrate)
def crear_grupos_operativos(sender, **kwargs):
    if sender.name != "mesas":
        return
    for nombre in (GRUPO_ADMIN, GRUPO_MESERO, GRUPO_COCINA, GRUPO_CAJA):
        Group.objects.get_or_create(name=nombre)
