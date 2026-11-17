# El Amir · Sistema web de gestión operativa

Sistema web transaccional para **Comida Árabe El Amir** (Puerto La Cruz): comandas en el salón y
para llevar, monitor de cocina, cobro bimonetario en bolívares y dólares, inventario por receta y
panel gerencial. Desarrollado con Django y SQLite, funciona dentro de la red local del restaurante.

## Requisitos

- Python 3.12 o superior (probado con 3.14).
- Windows o Linux. No hace falta instalar un motor de base de datos: SQLite viene con Python.

## Instalación

```bash
git clone https://github.com/ramiZ117/proyecto-tesis.git
cd proyecto-tesis
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_restaurante   # carta árabe, insumos, proveedores, mesas y usuarios de prueba
python manage.py runserver
```

Abre <http://127.0.0.1:8000>. Usuarios de prueba (contraseña `tesis1234`; cámbiala con
`python manage.py seed_restaurante --password OTRA --reset-passwords` antes de usarlo de verdad):

| Usuario | Rol | Entra a |
|---|---|---|
| `admin` | Administrador | Panel, todos los módulos, usuarios, carta y proveedores |
| `mesero1` | Mesero | Salón: mapa de mesas, comandas y pedidos para llevar |
| `chef1` | Cocina | Monitor de cocina |
| `cajero1` | Caja | Cuentas, cobro, tasa, cierre, reportes e inventario |

## En el restaurante (producción)

1. Copia `.env.example` como `.env` y completa: `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, y la IP del
   servidor en `DJANGO_ALLOWED_HOSTS` y `DJANGO_CSRF_ORIGINS`.
2. `python manage.py collectstatic --noinput`
3. `python manage.py servir` → las tablets, la PC de caja y la pantalla de cocina entran a
   `http://IP-DEL-SERVIDOR:8000`.
4. Programa `python manage.py respaldar` todos los días.

Los pasos completos, incluido cómo dejarlo como servicio y programar los respaldos, están en el
[manual técnico](docs/manual-tecnico.md).

## Comandos útiles

| Comando | Para qué |
|---|---|
| `python manage.py test` | 190 pruebas automáticas (incluye una en navegador si instalas `requirements-dev.txt`) |
| `python manage.py servir` | Servidor de producción (waitress) para la red local |
| `python manage.py respaldar` | Copia verificada de la base; conserva las 30 más recientes |
| `python manage.py medir_rendimiento` | Prueba de rendimiento y concurrencia sobre una copia de la base |
| `python manage.py seed_restaurante` | Datos de demostración (no borra lo que ya existe) |

## Documentación

- [Manual de usuario](docs/manual-usuario.md): el trabajo diario por rol.
- [Manual técnico](docs/manual-tecnico.md): arquitectura, reglas, despliegue, respaldos y mantenimiento.
- [Resultados de rendimiento](docs/resultados-rendimiento.md): evidencia de RNF-01 y RNF-02.
- [Cambios para el documento de tesis](docs/cambios-para-la-tesis.md).
- [Fase de elaboración](docs/4.2-fase-de-elaboracion.md).
