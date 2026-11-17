from django.conf import settings
from django.db import models
from django.db.models import Prefetch
from django.utils import timezone

from mesas.models import DetallePedido, Pedido


class PedidoKDSManager(models.Manager):
    def en_cola(self, estacion: str | None = None):
        """Pedidos con platillos enviados y sin entregar, del más antiguo al más reciente.

        Cada pedido trae `items_cocina` (solo lo enviado por el mesero y no entregado, y de la
        estación pedida si se filtra), `desde` (cuándo entró el platillo pendiente más antiguo),
        `espera_min`, `nivel` ("normal", "demora" o "critico" según KDS_MINUTOS_*) y `etapa`
        ("pen" si nada empezó, "prep" si algo está en marcha, "listo" si todo espera al mesero).
        """
        items = (
            DetallePedido.objects.filter(enviado=True)
            .exclude(estado__in=[DetallePedido.Estado.ANULADO, DetallePedido.Estado.ENTREGADO])
            .select_related("producto__categoria")
            .order_by("enviado_en", "pk")
        )
        if estacion:
            items = items.filter(producto__categoria__estacion=estacion)
        pedidos = (
            self.get_queryset()
            .exclude(estado__in=[Pedido.Estado.CERRADO, Pedido.Estado.ANULADO])
            .select_related("mesa", "mesero")
            .prefetch_related(Prefetch("detalles", queryset=items, to_attr="items_cocina"))
        )
        ahora = timezone.now()
        cola = []
        Det = DetallePedido.Estado
        for pedido in pedidos:
            if not pedido.items_cocina:
                continue
            activos = [i.enviado_en for i in pedido.items_cocina if i.estado in {Det.PENDIENTE, Det.EN_PREPARACION} and i.enviado_en]
            fechas = activos or [i.enviado_en for i in pedido.items_cocina if i.enviado_en]
            pedido.desde = min(fechas) if fechas else (pedido.enviado_cocina_en or pedido.creado_en)
            pedido.espera_min = max(int((ahora - pedido.desde).total_seconds() // 60), 0)
            pedido.pendiente_cocina = bool(activos)
            estados = {i.estado for i in pedido.items_cocina}
            if estados == {Det.LISTO}:
                pedido.etapa = "listo"
            elif estados & {Det.EN_PREPARACION, Det.LISTO}:
                pedido.etapa = "prep"
            else:
                pedido.etapa = "pen"
            if not pedido.pendiente_cocina:
                pedido.nivel = "normal"
            elif pedido.espera_min >= settings.KDS_MINUTOS_CRITICO:
                pedido.nivel = "critico"
            elif pedido.espera_min >= settings.KDS_MINUTOS_ALERTA:
                pedido.nivel = "demora"
            else:
                pedido.nivel = "normal"
            cola.append(pedido)
        cola.sort(key=lambda p: p.desde)
        return cola


class PedidoKDS(Pedido):
    """Vista de cocina sobre pedidos ya enviados (sin tabla extra)."""

    objects = PedidoKDSManager()

    class Meta:
        proxy = True
        verbose_name = "pedido de cocina"
        verbose_name_plural = "pedidos de cocina"


class PlatilloKDS(DetallePedido):
    """Ítems individuales en la pantalla KDS."""

    class Meta:
        proxy = True
        verbose_name = "platillo en cocina"
        verbose_name_plural = "platillos en cocina"
