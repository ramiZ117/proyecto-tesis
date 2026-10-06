from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from config.permisos import ROLES


class UsuarioForm(forms.Form):
    username = forms.CharField(label="Usuario", max_length=150)
    first_name = forms.CharField(label="Nombre", max_length=150, required=False)
    rol = forms.ChoiceField(label="Rol", choices=ROLES)
    password = forms.CharField(label="Contraseña", widget=forms.PasswordInput, required=False)
    is_active = forms.BooleanField(label="Activo", required=False, initial=True)

    def __init__(self, *args, usuario: User | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        existentes = User.objects.filter(username__iexact=username)
        if self.usuario:
            existentes = existentes.exclude(pk=self.usuario.pk)
        if existentes.exists():
            raise forms.ValidationError("Ya existe un usuario con ese nombre.")
        return username

    def clean(self):
        datos = super().clean()
        password = datos.get("password")
        if not self.usuario and not password:
            self.add_error("password", "La contraseña es obligatoria para un usuario nuevo.")
        if password:
            candidato = self.usuario or User(username=datos.get("username", ""))
            try:
                validate_password(password, candidato)
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        return datos
