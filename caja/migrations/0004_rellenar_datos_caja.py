from django.db import migrations
from django.db.models import F


def rellenar(apps, schema_editor):
    Pago = apps.get_model("caja", "Pago")
    Factura = apps.get_model("caja", "Factura")
    CierreCaja = apps.get_model("caja", "CierreCaja")

    # Métodos retirados: transferencia equivale a pago móvil; mixto, a efectivo.
    Pago.objects.filter(metodo="TRA").update(metodo="PMO", referencia="(migrado)")
    Pago.objects.filter(metodo="MIX").update(metodo="EFE")
    # La moneda ahora se deduce del método (divisa = USD).
    Pago.objects.filter(moneda="USD").update(metodo="DIV")
    Pago.objects.exclude(moneda="USD").filter(metodo="DIV").update(metodo="EFE")

    Factura.objects.filter(estado="PAG", pagada_en__isnull=True).update(pagada_en=F("emitida_en"))
    CierreCaja.objects.all().update(hasta=F("cerrado_en"))


class Migration(migrations.Migration):
    dependencies = [
        ("caja", "0003_alter_cierrecaja_options_and_more"),
    ]

    operations = [
        migrations.RunPython(rellenar, migrations.RunPython.noop),
    ]
