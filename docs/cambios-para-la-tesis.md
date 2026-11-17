# Cambios para el documento de tesis

Lista de ajustes para que el documento (`tesis def.txt`) coincida con el sistema entregado.
Cada punto indica la sección, el problema y un texto propuesto.

## 1. Correcciones de forma

| Dónde | Problema | Corrección |
|---|---|---|
| Portada / objetivo general | El título dice **2026** y el objetivo general y la fecha dicen **2027** | Unificar el año |
| Índice general | Numeración del capítulo IV (4.1 Fase de inicio, 4.2 Elaboración…) distinta del cuerpo (4.1 Diagnosticar… 4.6 Documentación); en el capítulo III también difiere (3.2 Población vs. 3.3) | Regenerar el índice desde los títulos |
| 4.2.4.1 Definición de actores | La frase "En el diagnóstico organizacional, este rol se identificó como mesonero" está en el párrafo del Administrador | Moverla al párrafo del Mesero |
| RF-02 | Lista los estados "libre, ocupada, por facturar" | Agregar "por limpiar" y los pedidos para llevar (ver punto 3) |

## 2. Base de datos: SQLite en lugar de PostgreSQL

Afecta 4.3 (introducción), 4.3.13 (capa de acceso a datos), 4.3.14, 4.3.15 (despliegue), 4.4 y el
cuadro de nodos. Texto propuesto para 4.3.13, en reemplazo de los párrafos sobre PostgreSQL:

> SQLite es el motor de producción. El restaurante opera como un solo local con un servidor en su
> red interna; una base contenida en un archivo elimina la administración de un motor aparte y
> convierte el respaldo en una copia verificable del archivo, lo que también mitiga el riesgo de
> cortes de luz o de internet. La concurrencia se resuelve abriendo cada transacción en modo
> inmediato (`transaction_mode=IMMEDIATE`): la transacción toma el candado de escritura al
> comenzar, de modo que dos aperturas de la misma mesa, dos emisiones de factura o dos cierres
> simultáneos se atienden en fila y la segunda ya ve lo que escribió la primera. El registro por
> adelantado (WAL) permite que las consultas del mapa, la cocina y los reportes no esperen a las
> escrituras. Las reglas críticas quedan además protegidas por restricciones de la base:
> `uniq_pedido_abierto_por_mesa`, `uniq_factura_vigente_por_pedido`, la unicidad del número de
> factura (con reintento en `Factura.emitir_desde_pedido`) y `uniq_cierre_secuencia_por_cajero`.

En **4.3.15** reemplazar Nginx + Gunicorn + PostgreSQL por:

| Nodo | Software | Función |
|---|---|---|
| Estaciones de salón, cocina y caja | Navegador | Igual que antes |
| Servidor del restaurante (Windows o Linux) | Python, Django, **waitress**, **whitenoise** | `python manage.py servir` atiende las peticiones con 8 hilos; whitenoise sirve los archivos estáticos con huella y caché |
| Base de datos | **SQLite** (archivo `db.sqlite3` en el mismo equipo) | Persiste todas las tablas; `python manage.py respaldar` crea copias diarias verificadas |

Variables del despliegue: `DJANGO_DEBUG=0`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`,
`DJANGO_CSRF_ORIGINS`, `DJANGO_HTTPS` (1 solo con certificado), `DB_PATH`, `RESPALDOS_DIR`.

## 3. Funciones nuevas que conviene describir

| Sección | Agregar |
|---|---|
| 4.1.1 (menciona pedidos "para llevar o despachos") y RF-02 | El mesero abre **pedidos para llevar** con el nombre del cliente (código L-001…); siguen el mismo flujo de cocina y se cobran en caja al retirarlos, sin mesa |
| RF-02 / CU-COM-01 | Cambiar la cantidad antes de enviar, **nota general de la comanda**, **mover de mesa** |
| RF-03 / CU-COM-02 | Tablero por columnas (nuevos, preparando, listos), **filtro por estación** (parrilla, fríos, bebidas), cronómetro y **aviso sonoro** |
| RF-04 / CU-INV-01 | **Punto de reposición y stock máximo** (cap. 2.2.2), **merma con motivo**, **ajuste por conteo** (administrador), **proveedor y costo** por insumo, **lista de compra** exportable y **costo y margen por plato** |
| RF-05 / CU-FAC-01 | Calculadora de vuelto en pantalla; confirmación cuando la tasa cambia más de 20 % |
| Nuevo (MIS, cap. 2.2.1) | **Panel del administrador**: ventas por período con comparación, ventas por hora/día, cobros por método, platos más vendidos, tiempo medio de cocina y alertas |
| 4.2.3 Riesgos | Mitigación del riesgo 3 implementada: `respaldar` con verificación de integridad y rotación |

## 4. Diccionario de datos (4.3.11)

| Cuadro | Cambio |
|---|---|
| 15 · Pedido | Agregar `tipo` (en mesa / para llevar), `cliente`; `mesa` pasa a opcional. Restricciones: `uniq_pedido_abierto_por_mesa` (ya descrita) y `pedido_mesa_segun_tipo` |
| 16 · DetallePedido | Agregar `listo_en` (momento en que cocina lo marcó listo; mide el tiempo de cocina) |
| 17 · Producto / Categoría | Categoría: agregar `estacion` y `orden` |
| 19 · Insumo | `grupo`: Carnes, Granos y legumbres, Panadería, Vegetales y hierbas, Lácteos, Especias y salsas, Bebidas, Otros. Agregar `punto_reposicion`, `stock_maximo`, `costo_unitario`, `proveedor` |
| 20 · MovimientoStock | `tipo`: Entrada, Salida (venta), **Merma**, Ajuste. Agregar `motivo` (vencido, dañado, error de preparación, comanda anulada, conteo físico, otro), `costo_unitario`, `usuario` |
| Nuevo · Proveedor | `nombre` (único), `contacto`, `telefono`, `activo` |
| 26 · CierreCaja | `secuencia` ya figura en el cuadro y ahora existe en el sistema; el `desglose` guarda también la foto de las facturas del corte |

## 5. Validación (4.5)

Actualizar el cuadro 27 con la suite actual y agregar los resultados medidos:

- **190 pruebas automáticas**, todas aprobadas, organizadas por módulo (ver manual técnico, sección 7),
  incluida una prueba en un navegador real que recorre el ciclo mesero → cocina → caja.
- **Rendimiento y concurrencia:** `python manage.py medir_rendimiento` con usuarios simultáneos
  sobre una copia de la base. Copiar la tabla de `docs/resultados-rendimiento.md` (mediana y P95 por
  operación; máximo por debajo de 2 s) como evidencia de RNF-01 y RNF-02, junto con las cinco
  verificaciones de integridad.
- Falta la **aceptación con el personal** (índice de aprobación del usuario final del cuadro 2):
  aplicar una planilla breve a los 6 usuarios de la muestra tras usar el sistema una jornada.

## 6. Documentación (4.6)

El manual de usuario y el manual técnico ya existen en `docs/manual-usuario.md` y
`docs/manual-tecnico.md`; los apartados 4.6.1 y 4.6.2 pueden resumirlos y remitir a ellos.
