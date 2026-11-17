# Manual técnico · El Amir

Guía para instalar, operar y mantener el sistema. Complementa el capítulo IV de la tesis
(arquitectura, diccionario de datos y despliegue).

## 1. Arquitectura

- **Patrón:** cliente-servidor sobre HTTP. Dentro del servidor, Modelo–Vista–Plantilla de Django.
- **Clientes:** navegador en las tablets del salón, la PC de caja, la pantalla de cocina y teléfonos.
- **Servidor:** un equipo del restaurante (Windows o Linux) ejecuta la aplicación con **waitress**
  (un proceso, 8 hilos) y la base **SQLite**. **Whitenoise** sirve el CSS, el JS y el logo.
- **Red:** funciona dentro de la red local; no depende de internet (riesgo 2 de la tesis).

| Aplicación | Responsabilidad | Modelos principales |
|---|---|---|
| `config` | Arranque, permisos por rol, panel gerencial, comandos de operación | — |
| `mesas` | Salón, comandas, pedidos para llevar | `Mesa`, `Pedido`, `DetallePedido` |
| `cocina` | Monitor de cocina (sin tablas propias) | proxies `PedidoKDS`, `PlatilloKDS` |
| `caja` | Tasa, facturas, pagos, devoluciones, cuenta dividida, cierres | `TasaCambio`, `Factura`, `Pago`, `Devolucion`, `Asignacion`, `CierreCaja` |
| `inventario` | Carta, recetas, insumos, proveedores, movimientos | `Categoria`, `Producto`, `Receta`, `Insumo`, `Proveedor`, `MovimientoStock` |

Las reglas de negocio viven en los modelos y en `inventario/servicios.py`; las vistas solo
autorizan, leen el formulario y muestran el resultado.

## 2. Reglas protegidas por la base de datos

| Regla | Dónde |
|---|---|
| Una mesa tiene a lo sumo una comanda abierta | `UniqueConstraint uniq_pedido_abierto_por_mesa` + `Pedido.abrir_para` |
| Un pedido en mesa tiene mesa; uno para llevar no | `CheckConstraint pedido_mesa_segun_tipo` |
| Una comanda tiene a lo sumo una factura vigente | `UniqueConstraint uniq_factura_vigente_por_pedido` |
| Número de factura único y correlativo | `numero` único + reintento en `Factura.emitir_desde_pedido` |
| Un cierre por tramo de cada cajero | `UniqueConstraint uniq_cierre_secuencia_por_cajero` |
| El ítem de cocina no retrocede | `DetallePedido.marcar_*` |
| La receta se descuenta una sola vez | `DetallePedido.insumos_descontados` |

**Concurrencia con SQLite.** SQLite ignora `select_for_update`. Por eso la base se abre en modo
`transaction_mode=IMMEDIATE`: cada transacción toma el candado de escritura al empezar, y dos
operaciones simultáneas (dos cajeros, dos meseros en la misma mesa) se atienden en fila. El modo
`WAL` permite que las lecturas (mapa, cocina, reportes) no esperen a las escrituras. La prueba
`medir_rendimiento` lo verifica con usuarios simultáneos (ver `docs/resultados-rendimiento.md`).

**Panel técnico (`/admin/`).** Facturas, pagos, devoluciones, cierres, comandas, tasas y
movimientos de stock son de solo lectura: solo cambian a través de los métodos que aplican las
reglas. La carta, los insumos, los proveedores, las categorías y las mesas sí se editan ahí.

## 3. Variables de configuración (`.env`)

| Variable | Valor en el restaurante | Descripción |
|---|---|---|
| `DJANGO_DEBUG` | `0` | `1` solo para desarrollo |
| `DJANGO_SECRET_KEY` | clave aleatoria | Obligatoria con `DEBUG=0` |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost,192.168.1.10` | Incluye la IP del servidor en la red |
| `DJANGO_CSRF_ORIGINS` | `http://192.168.1.10:8000` | Origen de los formularios |
| `DJANGO_HTTPS` | `0` | `1` solo con certificado válido (activa cookies seguras y HSTS) |
| `DB_PATH` | opcional | Ruta de la base (por defecto `db.sqlite3`) |
| `RESPALDOS_DIR` | otro disco o USB | Carpeta de respaldos (por defecto `respaldos/`) |
| `RESPALDOS_CONSERVAR` | `30` | Cuántos respaldos guardar |
| `KDS_MINUTOS_ALERTA` / `KDS_MINUTOS_CRITICO` | `15` / `25` | Umbrales del monitor de cocina |

`manage.py check --deploy` muestra 4 avisos sobre HTTPS mientras `DJANGO_HTTPS=0`. Son esperables en
una red interna por HTTP y desaparecen al activar HTTPS con certificado.

## 4. Instalación en el servidor del restaurante

### Windows

```bat
cd C:\ElAmir
python -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env          &:: editar: DJANGO_DEBUG=0, clave, IP
venv\Scripts\python manage.py migrate
venv\Scripts\python manage.py seed_restaurante --password CLAVE-SEGURA
venv\Scripts\python manage.py collectstatic --noinput
venv\Scripts\python manage.py servir
```

Permitir el puerto en el firewall (una vez, como administrador):

```bat
netsh advfirewall firewall add rule name="El Amir" dir=in action=allow protocol=TCP localport=8000
```

Arrancar el sistema al encender el equipo y respaldar cada noche (Programador de tareas):

```bat
schtasks /Create /SC ONSTART /RU SYSTEM /TN "El Amir - servidor" /TR "C:\ElAmir\venv\Scripts\python.exe C:\ElAmir\manage.py servir"
schtasks /Create /SC DAILY /ST 23:30 /RU SYSTEM /TN "El Amir - respaldo" /TR "C:\ElAmir\venv\Scripts\python.exe C:\ElAmir\manage.py respaldar"
```

### Linux (Ubuntu)

```bash
cd /opt/elamir
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # editar
venv/bin/python manage.py migrate && venv/bin/python manage.py collectstatic --noinput
```

`/etc/systemd/system/elamir.service`:

```ini
[Unit]
Description=El Amir
After=network.target

[Service]
WorkingDirectory=/opt/elamir
ExecStart=/opt/elamir/venv/bin/python manage.py servir
Restart=always
User=elamir

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now elamir
( crontab -l; echo "30 23 * * * /opt/elamir/venv/bin/python /opt/elamir/manage.py respaldar" ) | crontab -
```

## 5. Respaldos y restauración

- `python manage.py respaldar` crea `respaldos/el_amir_AAAA-MM-DD_HHMMSS.sqlite3` con la API de
  respaldo de SQLite (copia consistente aunque el sistema esté en uso), verifica su integridad y
  conserva las 30 más recientes.
- Conviene que `RESPALDOS_DIR` apunte a otro disco o a una memoria USB, y copiar alguno fuera del local.
- **Restaurar:** detener el servidor, copiar el respaldo elegido como `db.sqlite3` (borrar
  `db.sqlite3-wal` y `db.sqlite3-shm` si existen) y volver a arrancar.

## 6. Actualizar a una versión nueva

```bash
python manage.py respaldar
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
# reiniciar el servicio
```

Las migraciones que agregan columnas completan los datos existentes (por ejemplo, numeran los
cierres o calculan el punto de reposición de los insumos).

## 7. Pruebas

```bash
python manage.py test                        # 190 pruebas, ~25 s
pip install -r requirements-dev.txt          # opcional: pruebas en navegador (Edge/Chrome)
python manage.py medir_rendimiento --usuarios 8 --rondas 6 --salida docs/resultados-rendimiento.md
```

| Archivo | Qué cubre |
|---|---|
| `mesas/tests.py`, `cocina/tests.py`, `caja/tests.py`, `inventario/tests.py` | Casos de uso por módulo |
| `config/tests.py` | Autenticación, roles, bloqueo por intentos, usuarios |
| `config/test_flujo_completo.py` | Ciclo completo de una mesa |
| `config/test_integridad.py` | Concurrencia, restricciones, admin de solo lectura |
| `config/test_fase2.py` | Para llevar, inventario ampliado, cocina por estación, panel y validaciones |
| `config/test_operacion.py` | Respaldos y configuración de producción |
| `config/test_navegador.py` | Interfaz real en el navegador (JavaScript) |

## 8. Registros y solución de problemas

- Los errores se guardan en `logs/el_amir.log` (rotativo, 5 archivos de 2 MB).

| Síntoma | Causa y solución |
|---|---|
| Las tablets no abren la página | Falta la IP en `DJANGO_ALLOWED_HOSTS` o el puerto en el firewall |
| "CSRF verification failed" al enviar un formulario | Agrega `http://IP:8000` a `DJANGO_CSRF_ORIGINS` |
| No se puede iniciar sesión con `DEBUG=0` | `DJANGO_HTTPS=1` sin certificado: déjalo en `0` en la red interna |
| La página se ve sin estilos | Ejecuta `collectstatic --noinput` y reinicia |
| Los meseros no pueden cargar platos | No hay tasa vigente: caja debe publicarla |
| Un plato aparece "Agotado" | El stock disponible de algún insumo de su receta no alcanza |
| Bloqueo de inicio de sesión | Tras 5 intentos fallidos; se libera solo a los 5 minutos |
