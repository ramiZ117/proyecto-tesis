from django.db import migrations


def rellenar_envio(apps, schema_editor):
    """Los platillos de pedidos que ya se enviaron a cocina quedan marcados como enviados."""
    Pedido = apps.get_model("mesas", "Pedido")
    DetallePedido = apps.get_model("mesas", "DetallePedido")
    for pedido in Pedido.objects.filter(enviado_cocina_en__isnull=False):
        DetallePedido.objects.filter(pedido=pedido, enviado=False).update(
            enviado=True,
            enviado_en=pedido.enviado_cocina_en,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("mesas", "0002_detallepedido_enviado_detallepedido_enviado_en_and_more"),
    ]

    operations = [
        migrations.RunPython(rellenar_envio, migrations.RunPython.noop),
    ]
