"""Formularios de la administración (reemplazan al panel técnico de Django)."""

from decimal import Decimal
from io import BytesIO

from django import forms
from django.conf import settings
from django.core.files.base import ContentFile
from django.forms import inlineformset_factory
from django.utils.text import slugify

from config.formato import num
from inventario.models import Categoria, Insumo, Producto, Promocion, PromocionItem, Proveedor, Receta
from gestion.models import AjustesLocal, GastoExtraordinario, GastoFijo
from mesas.models import Mesa

FOTO_LADO_MAX = 1400


class _Estilo:
    """Marca los campos para la hoja de estilos (los checkbox van aparte)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo.widget, forms.CheckboxInput):
                campo.widget.attrs.setdefault("class", "interruptor")


def procesar_foto(archivo, nombre_base: str) -> ContentFile:
    """Valida y normaliza la foto: JPEG de hasta 1400 px, sin metadatos (ubicación del teléfono, etc.)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if archivo.size > settings.FOTO_MAX_BYTES:
        raise forms.ValidationError(f"La foto pesa más de {settings.FOTO_MAX_BYTES // (1024 * 1024)} MB.")
    try:
        imagen = Image.open(archivo)
        imagen.verify()
        archivo.seek(0)
        imagen = Image.open(archivo)
        imagen = ImageOps.exif_transpose(imagen)  # respeta la orientación con que se tomó
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise forms.ValidationError("El archivo no es una imagen válida (usa JPG, PNG o WEBP).") from exc
    if min(imagen.size) < 300:
        raise forms.ValidationError("La foto es muy pequeña: usa una de al menos 300 × 300 píxeles.")
    imagen = imagen.convert("RGB")
    imagen.thumbnail((FOTO_LADO_MAX, FOTO_LADO_MAX))
    salida = BytesIO()
    imagen.save(salida, format="JPEG", quality=85, optimize=True, progressive=True)
    return ContentFile(salida.getvalue(), name=f"{slugify(nombre_base) or 'plato'}.jpg")


class ProductoForm(_Estilo, forms.ModelForm):
    class Meta:
        model = Producto
        fields = ["nombre", "categoria", "precio_usd", "descripcion", "foto", "foto_credito",
                  "disponible", "destacado", "vegetariano", "picante", "sin_gluten", "minutos_preparacion", "opciones", "bebida"]
        labels = {"bebida": "¿Es bebida?", "precio_usd": "Precio en USD", "foto_credito": "Crédito de la foto",
                  "destacado": "Recomendado de la casa", "disponible": "Disponible en la carta",
                  "minutos_preparacion": "Minutos de preparación", "opciones": "Personalizaciones permitidas"}
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 3, "maxlength": 400,
                                                 "placeholder": "Lo que verá el cliente en el menú digital"}),
            "foto": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
            "precio_usd": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "minutos_preparacion": forms.NumberInput(attrs={"min": 1, "max": 120}),
            "opciones": forms.Textarea(attrs={"rows": 4, "placeholder": "Sin vegetales\nSin salsa de ajo\nSalsa aparte"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.order_by("orden", "nombre")
        self.fields["nombre"].widget.attrs["maxlength"] = 120
        self.fields["minutos_preparacion"].required = False

    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        if len(nombre) < 2:
            raise forms.ValidationError("Escribe el nombre del plato.")
        return nombre

    def clean(self):
        datos = super().clean()
        nombre, categoria = datos.get("nombre"), datos.get("categoria")
        if nombre and categoria:
            repetido = Producto.objects.filter(categoria=categoria, nombre__iexact=nombre).exclude(pk=self.instance.pk)
            if repetido.exists():
                self.add_error("nombre", f"Ya existe «{nombre}» en {categoria}.")
        return datos

    def validate_unique(self):
        pass  # la unicidad (categoría, nombre) se valida arriba con un mensaje claro

    def clean_precio_usd(self):
        precio = self.cleaned_data["precio_usd"]
        if precio is None or precio < 0:
            raise forms.ValidationError("El precio no puede ser negativo.")
        if precio > Decimal("10000"):
            raise forms.ValidationError("Revisa el precio: es demasiado alto.")
        return precio

    def clean_foto(self):
        foto = self.cleaned_data.get("foto")
        if foto and hasattr(foto, "content_type"):  # archivo recién subido
            return procesar_foto(foto, self.cleaned_data.get("nombre") or self.data.get("nombre", "plato"))
        return foto

    def clean_minutos_preparacion(self):
        minutos = self.cleaned_data.get("minutos_preparacion")
        if minutos is None:
            return self.instance.minutos_preparacion or 10  # si se deja vacío se conserva el valor
        if not 1 <= minutos <= 120:
            raise forms.ValidationError("Entre 1 y 120 minutos.")
        return minutos

    def clean_opciones(self):
        lineas = [" ".join(l.split()) for l in (self.cleaned_data.get("opciones") or "").splitlines() if l.strip()]
        if len(lineas) > 12:
            raise forms.ValidationError("Máximo 12 personalizaciones por plato.")
        largas = [l for l in lineas if len(l) > 40]
        if largas:
            raise forms.ValidationError(f"«{largas[0][:20]}…» es muy larga: usa frases cortas (máximo 40 caracteres).")
        unicas = list(dict.fromkeys(lineas))
        return "\n".join(unicas)


class RecetaForm(forms.ModelForm):
    class Meta:
        model = Receta
        fields = ["insumo", "cantidad"]
        widgets = {"cantidad": forms.NumberInput(attrs={"step": "0.001", "min": "0.001"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["insumo"].queryset = Insumo.objects.filter(activo=True).order_by("nombre")
        self.fields["insumo"].label_from_instance = lambda i: f"{i.nombre} ({i.abreviatura})"


class _RecetaBaseFormSet(forms.BaseInlineFormSet):
    def get_unique_error_message(self, unique_check):
        return "Un insumo aparece dos veces en la receta: deja una sola fila por insumo."

    def clean(self):
        super().clean()
        vistos = set()
        for form in self.forms:
            if not getattr(form, "cleaned_data", None) or form.cleaned_data.get("DELETE"):
                continue
            insumo = form.cleaned_data.get("insumo")
            if insumo in vistos:
                raise forms.ValidationError(f"{insumo.nombre} aparece dos veces en la receta.")
            vistos.add(insumo)


RecetaFormSet = inlineformset_factory(
    Producto, Receta, form=RecetaForm, formset=_RecetaBaseFormSet, extra=2, can_delete=True
)


class CategoriaForm(_Estilo, forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre", "estacion", "orden", "activa"]
        labels = {"estacion": "Dónde se prepara", "orden": "Orden en la carta"}
        error_messages = {"nombre": {"unique": "Ya existe una categoría con ese nombre."}}
        widgets = {"orden": forms.NumberInput(attrs={"min": 0, "max": 99})}


class ProveedorForm(_Estilo, forms.ModelForm):
    class Meta:
        model = Proveedor
        fields = ["nombre", "contacto", "telefono", "activo"]
        labels = {"telefono": "Teléfono"}
        error_messages = {"nombre": {"unique": "Ya existe un proveedor con ese nombre."}}
        widgets = {"telefono": forms.TextInput(attrs={"inputmode": "tel", "pattern": r"[0-9+()\- ]{7,20}",
                                                       "title": "Solo números, espacios, +, - y paréntesis"})}


class InsumoForm(_Estilo, forms.ModelForm):
    """Ficha del insumo. El stock no se edita aquí: cambia con entradas, mermas o ajustes por conteo."""

    stock_inicial = forms.DecimalField(
        label="Stock inicial", min_value=0, max_digits=12, decimal_places=3, required=False,
        help_text="Solo al crear el insumo; queda registrado como una entrada.",
    )

    class Meta:
        model = Insumo
        fields = ["nombre", "grupo", "unidad", "proveedor", "costo_unitario",
                  "stock_minimo", "punto_reposicion", "stock_maximo", "activo"]
        labels = {"costo_unitario": "Costo por unidad (USD)", "stock_minimo": "Stock mínimo",
                  "punto_reposicion": "Punto de reposición", "stock_maximo": "Stock máximo"}
        error_messages = {"nombre": {"unique": "Ya existe un insumo con ese nombre."}}
        widgets = {f: forms.NumberInput(attrs={"step": "0.001", "min": "0"})
                   for f in ("stock_minimo", "punto_reposicion", "stock_maximo")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True)
        self.fields["costo_unitario"].widget.attrs.update({"step": "0.0001", "min": "0"})
        if self.instance.pk:
            del self.fields["stock_inicial"]

    def clean(self):
        datos = super().clean()
        minimo = datos.get("stock_minimo") or Decimal("0")
        reposicion = datos.get("punto_reposicion") or Decimal("0")
        maximo = datos.get("stock_maximo") or Decimal("0")
        if reposicion and reposicion < minimo:
            raise forms.ValidationError("El punto de reposición no puede ser menor que el stock mínimo.")
        if maximo and maximo < reposicion:
            raise forms.ValidationError("El stock máximo no puede ser menor que el punto de reposición.")
        return datos


class MesaForm(_Estilo, forms.ModelForm):
    class Meta:
        model = Mesa
        fields = ["numero", "capacidad", "forma", "pos_x", "pos_y", "activa"]
        labels = {"numero": "Número", "capacidad": "Personas", "pos_x": "Columna en el plano",
                  "pos_y": "Fila en el plano", "activa": "En servicio", "forma": "Forma en el plano"}
        widgets = {
            "numero": forms.NumberInput(attrs={"min": 1, "max": 999}),
            "capacidad": forms.NumberInput(attrs={"min": 1, "max": 30}),
            "pos_x": forms.NumberInput(attrs={"min": 1, "max": 12}),
            "pos_y": forms.NumberInput(attrs={"min": 1, "max": 20}),
            "forma": forms.RadioSelect(attrs={"class": "selector-forma"}),
        }
        help_texts = {"pos_x": "1 = primera columna", "pos_y": "1 = primera fila"}
        error_messages = {"numero": {"unique": "Ya existe una mesa con ese número."}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["forma"].required = False
        # En pantalla la posición empieza en 1; en la base, en 0.
        if self.instance.pk and not self.is_bound:
            self.initial["pos_x"] = self.instance.pos_x + 1
            self.initial["pos_y"] = self.instance.pos_y + 1

    def clean_forma(self):
        return self.cleaned_data.get("forma") or self.instance.forma or Mesa.Forma.ARCO

    def clean_capacidad(self):
        capacidad = self.cleaned_data["capacidad"]
        if not 1 <= capacidad <= 30:
            raise forms.ValidationError("La capacidad va de 1 a 30 personas.")
        return capacidad

    def clean(self):
        datos = super().clean()
        for campo in ("pos_x", "pos_y"):
            if datos.get(campo) is not None:
                if datos[campo] < 1:
                    self.add_error(campo, "Empieza en 1.")
                else:
                    datos[campo] -= 1
        if datos.get("activa") and datos.get("pos_x") is not None and datos.get("pos_y") is not None:
            nueva = Mesa(pos_x=datos["pos_x"], pos_y=datos["pos_y"], forma=datos.get("forma") or Mesa.Forma.ARCO)
            if nueva.pos_x + nueva.ancho > 12:
                raise forms.ValidationError("Una mesa rectangular necesita una columna libre a su derecha (máximo 12 columnas).")
            for otra in Mesa.objects.filter(activa=True).exclude(pk=self.instance.pk):
                if otra.celdas() & nueva.celdas():
                    raise forms.ValidationError(f"Esa posición del plano ya la usa la {otra}. Elige otra columna o fila.")
        if self.instance.pk and self.instance.activa and not datos.get("activa"):
            if self.instance.pedido_activo() is not None or self.instance.estado != Mesa.Estado.DISPONIBLE:
                raise forms.ValidationError("No se puede retirar una mesa que está en servicio; espera a que quede disponible.")
        return datos


class PromocionForm(_Estilo, forms.ModelForm):
    class Meta:
        model = Promocion
        fields = ["nombre", "precio_usd", "descripcion", "foto", "activa", "desde", "hasta", "orden"]
        labels = {"precio_usd": "Precio del combo (USD)", "activa": "Visible para clientes y meseros", "descripcion": "Descripción",
                  "desde": "Desde", "hasta": "Hasta", "orden": "Orden"}
        error_messages = {"nombre": {"unique": "Ya existe una promoción con ese nombre."}}
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 2, "maxlength": 300,
                                                 "placeholder": "Ej.: ideal para compartir, solo fines de semana…"}),
            "precio_usd": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "desde": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "hasta": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "orden": forms.NumberInput(attrs={"min": 0, "max": 99}),
            "foto": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        }

    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        if len(nombre) < 3:
            raise forms.ValidationError("Escribe un nombre para la promoción.")
        return nombre

    def clean_precio_usd(self):
        precio = self.cleaned_data["precio_usd"]
        if precio is None or precio <= 0:
            raise forms.ValidationError("El precio debe ser mayor a cero.")
        if precio > Decimal("10000"):
            raise forms.ValidationError("Revisa el precio: es demasiado alto.")
        return precio

    def clean_foto(self):
        foto = self.cleaned_data.get("foto")
        if foto and hasattr(foto, "content_type"):
            return procesar_foto(foto, self.cleaned_data.get("nombre") or self.data.get("nombre", "promo"))
        return foto

    def clean(self):
        datos = super().clean()
        if datos.get("desde") and datos.get("hasta") and datos["hasta"] < datos["desde"]:
            self.add_error("hasta", "La fecha de fin no puede ser anterior a la de inicio.")
        return datos


class PromocionItemForm(forms.ModelForm):
    class Meta:
        model = PromocionItem
        fields = ["producto", "cantidad"]
        widgets = {"cantidad": forms.NumberInput(attrs={"min": 1, "max": 20})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["producto"].queryset = Producto.objects.select_related("categoria").order_by("categoria__orden", "nombre")
        self.fields["producto"].label_from_instance = lambda p: f"{p.nombre} (${num(p.precio_usd, 2)})"

    def clean_cantidad(self):
        cantidad = self.cleaned_data["cantidad"]
        if not 1 <= cantidad <= 20:
            raise forms.ValidationError("Entre 1 y 20.")
        return cantidad

    def has_changed(self):
        # Una fila nueva sin plato elegido se ignora aunque traiga la cantidad por defecto.
        if not self.instance.pk:
            return bool(self["producto"].value())
        return super().has_changed()


class _PromocionItemsFormSet(forms.BaseInlineFormSet):
    def get_unique_error_message(self, unique_check):
        return "Un plato aparece dos veces: deja una sola fila y ajusta la cantidad."

    def clean(self):
        super().clean()
        vistos, total = set(), 0
        for form in self.forms:
            if not getattr(form, "cleaned_data", None) or form.cleaned_data.get("DELETE"):
                continue
            producto = form.cleaned_data.get("producto")
            if not producto:
                continue
            if producto in vistos:
                raise forms.ValidationError(f"{producto.nombre} aparece dos veces: usa una sola fila con la cantidad.")
            vistos.add(producto)
            total += form.cleaned_data.get("cantidad") or 0
        if not vistos:
            raise forms.ValidationError("Agrega al menos un plato a la promoción.")
        if total > 30:
            raise forms.ValidationError("La promoción tiene demasiados platos (máximo 30 unidades).")


PromocionItemFormSet = inlineformset_factory(
    Promocion, PromocionItem, form=PromocionItemForm, formset=_PromocionItemsFormSet, extra=3, can_delete=True
)


class AjustesLocalForm(_Estilo, forms.ModelForm):
    class Meta:
        model = AjustesLocal
        exclude = ["actualizado_en"]
        widgets = {
            "pm_telefono": forms.TextInput(attrs={"inputmode": "tel", "placeholder": "0414-1234567"}),
            "pm_documento": forms.TextInput(attrs={"placeholder": "J-123456789"}),
            "pm_banco": forms.TextInput(attrs={"placeholder": "Ej.: Banesco (0134)"}),
            "binance_id": forms.TextInput(attrs={"placeholder": "Pay ID o correo"}),
            "pedidosya_url": forms.URLInput(attrs={"placeholder": "https://www.pedidosya.com.ve/…"}),
            "yummy_url": forms.URLInput(attrs={"placeholder": "https://…"}),
        }

    def clean_pm_documento(self):
        return self.cleaned_data["pm_documento"].upper().strip()

    def clean(self):
        datos = super().clean()
        pm = [datos.get("pm_banco"), datos.get("pm_telefono"), datos.get("pm_documento")]
        if any(pm) and not all(pm):
            raise forms.ValidationError("Para activar el pago móvil completa banco, teléfono y cédula/RIF (o déjalos los tres vacíos).")
        for campo in ("pedidosya_url", "yummy_url"):
            url = datos.get(campo)
            if url and not url.startswith("https://"):
                self.add_error(campo, "Usa un enlace seguro que empiece con https://")
        return datos



class GastoFijoForm(_Estilo, forms.ModelForm):
    class Meta:
        model = GastoFijo
        fields = ["nombre", "categoria", "monto_usd", "desde", "hasta"]
        error_messages = {"nombre": {"unique": "Ya existe un gasto fijo con ese nombre."}}
        widgets = {
            "monto_usd": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "desde": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "hasta": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        if len(nombre) < 3:
            raise forms.ValidationError("Escribe un nombre (ej.: Alquiler del local).")
        return nombre

    def clean_monto_usd(self):
        monto = self.cleaned_data["monto_usd"]
        if monto is None or monto <= 0:
            raise forms.ValidationError("El monto debe ser mayor a cero.")
        if monto > Decimal("1000000"):
            raise forms.ValidationError("Revisa el monto: es demasiado alto.")
        return monto

    def clean(self):
        datos = super().clean()
        if datos.get("desde") and datos.get("hasta") and datos["hasta"] < datos["desde"]:
            self.add_error("hasta", "No puede terminar antes de empezar.")
        return datos


class GastoExtraordinarioForm(_Estilo, forms.ModelForm):
    class Meta:
        model = GastoExtraordinario
        fields = ["fecha", "descripcion", "categoria", "monto_usd", "meses"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "monto_usd": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "meses": forms.NumberInput(attrs={"min": 1, "max": 60}),
            "descripcion": forms.TextInput(attrs={"placeholder": "Ej.: Plancha industrial de 90 cm"}),
        }

    def clean_descripcion(self):
        texto = " ".join(self.cleaned_data["descripcion"].split())
        if len(texto) < 3:
            raise forms.ValidationError("Describe el gasto.")
        return texto

    def clean_monto_usd(self):
        monto = self.cleaned_data["monto_usd"]
        if monto is None or monto <= 0:
            raise forms.ValidationError("El monto debe ser mayor a cero.")
        if monto > Decimal("10000000"):
            raise forms.ValidationError("Revisa el monto: es demasiado alto.")
        return monto

    def clean_meses(self):
        meses = self.cleaned_data["meses"]
        if meses is None or not 1 <= meses <= 60:
            raise forms.ValidationError("Entre 1 y 60 meses.")
        return meses

    def clean_fecha(self):
        from django.utils import timezone

        fecha = self.cleaned_data["fecha"]
        if fecha > timezone.localdate():
            raise forms.ValidationError("La fecha no puede ser futura.")
        return fecha
