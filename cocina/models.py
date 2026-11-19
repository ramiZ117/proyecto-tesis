from django.conf import settings
from datetime import timedelta

from django.db import models
from django.db.models import Prefetch, Q
from django.utils import timezone

from mesas.models import DetallePedido, Pedido

AREA_COCINA, AREA_SHAWARMA = "COC", "SHA"


def filtro_area(area: str, prefijo: str = "producto__") -> Q:
    """Platos que prepara cada área. Las bebidas de nevera no llegan a ninguna: las lleva el mesero."""
    sha = Q(**{f"{prefijo}categoria__estacion": "SHA"})
    nevera = Q(**{f"{prefijo}bebida": "NEV"})
    if area == AREA_SHAWARMA:
        return sha & ~nevera
    return ~sha & ~nevera


class PedidoKDSManager(models.Manager):
    def en_cola(self, estacion: str | None = None, area: str = AREA_COCINA):
        """Pedidos con platillos enviados y sin entregar, del más antiguo al más reciente.

        Cada pedido trae `items_cocina` (solo lo enviado por el mesero y no entregado, y de la
        estación pedida si se filtra), `desde` (cuándo entró el platillo pendiente más antiguo),
        `espera_min`, `nivel` ("normal", "demora" o "critico" según KDS_MINUTOS_*) y `etapa`
        ("pen" si nada empezó, "prep" si algo está en marcha, "listo" si todo espera al mesero).
        """
        items = (
            DetallePedido.objects.filter(enviado=True)
            .filter(filtro_area(area))
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
            pedido.por_empezar = sum(1 for i in pedido.items_cocina if i.estado == Det.PENDIENTE)
            pedido.en_marcha = sum(1 for i in pedido.items_cocina if i.estado == Det.EN_PREPARACION)
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


class AvisoCocina(models.Model):
    """Aviso que suena en la cocina o el shawarmero cuando anulan algo que ya les llegó.

    Así no solo desaparece de la pantalla: queda un letrero con alarma hasta que alguien lo marca «Entendido».
    """

    class Area(models.TextChoices):
        COCINA = AREA_COCINA, "Cocina"
        SHAWARMA = AREA_SHAWARMA, "Shawarmero"

    area = models.CharField(max_length=3, choices=Area.choices, default=Area.COCINA)
    pedido = models.ForeignKey(Pedido, on_delete=models.CASCADE, related_name="avisos_cocina")
    texto = models.CharField(max_length=300)
    creado_en = models.DateTimeField(auto_now_add=True)
    atendido_en = models.DateTimeField(null=True, blank=True)
    atendido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    HORAS_VISIBLE = 12

    class Meta:
        verbose_name = "aviso de cocina"
        verbose_name_plural = "avisos de cocina"
        ordering = ["-creado_en", "-id"]

    def __str__(self) -> str:
        return self.texto

    @classmethod
    def pendientes(cls, area: str):
        desde = timezone.now() - timedelta(hours=cls.HORAS_VISIBLE)
        return cls.objects.filter(area=area, atendido_en__isnull=True, creado_en__gte=desde)

    @classmethod
    def por_anulacion(cls, pedido, detalles, todo: bool = False) -> list["AvisoCocina"]:
        """Un aviso por área con lo que se anuló. Solo cuenta lo que ya estaba en su pantalla."""
        from inventario.models import Producto

        por_area: dict[str, list] = {}
        for d in detalles:
            if not d.enviado or d.estado in (DetallePedido.Estado.ANULADO, DetallePedido.Estado.ENTREGADO):
                continue
            area = d.producto.area
            if area == Producto.AREA_NEVERA:
                continue
            por_area.setdefault(area, []).append(d)
        avisos = []
        for area, lineas in por_area.items():
            platos = ", ".join(f"{d.cantidad}× {d.producto.nombre}" for d in lineas)
            accion = "ANULÓ el pedido" if todo else "quitó"
            avisos.append(cls.objects.create(area=area, pedido=pedido, texto=f"{pedido.etiqueta} {accion}: {platos}"[:300]))
        return avisos

    def atender(self, usuario) -> None:
        if self.atendido_en is None:
            self.atendido_en, self.atendido_por = timezone.now(), usuario
            self.save(update_fields=["atendido_en", "atendido_por"])
