# Cambios en el documento de tesis

El archivo `tesis def.txt` ya fue actualizado para que describa el sistema entregado. Este
documento resume qué se cambió y qué queda pendiente de hacer a mano en el documento de Word.

## Aplicado en el texto

| Sección | Cambio |
|---|---|
| 4.2.1 Requerimientos funcionales | RF-02 con estados completos, pedidos para llevar, cantidades, nota y mover de mesa. RF-03 con columnas, estaciones y aviso sonoro. RF-04 con reposición, máximo, costo, proveedor, lista de compra, margen, mermas y ajustes. RF-05 con calculadora de vuelto y confirmación de tasa. **Nuevos:** RF-06 (administración y panel gerencial) y RF-07 (menú digital con asistente) |
| 4.2.2 Requerimientos no funcionales | **Nuevos:** RNF-05 (operación local y respaldo) y RNF-06 (asistente acotado y con alternativa) |
| 4.2.4.1 Actores | Frase del "mesonero" movida al mesero; el administrador usa el módulo de administración propio; el cliente participa como visitante anónimo del menú digital |
| 4.2.4.2 | Ocho módulos (se suma el menú digital) |
| 4.3 y 4.3.13 | SQLite en lugar de PostgreSQL, con la explicación de la concurrencia (transacciones inmediatas, WAL y restricciones) |
| CU-INV-01, CU-MES-01, CU-MEN-01, CU-USU-01 | Ajuste por conteo y merma; alta y retiro de mesas por el administrador; foto y etiquetas del plato; sin panel de administración genérico |
| **Nuevo** 4.3.7.3 y Cuadro 13-A | Caso de uso CU-DIG-01 «Consultar el menú digital y conversar con el asistente» |
| 4.3.11 Diccionario de datos | Pedido (tipo, cliente, mesa opcional), DetallePedido (`listo_en`), Producto (foto, crédito, etiquetas), Insumo (grupos, reposición, máximo, costo, proveedor), MovimientoStock (Merma, motivo, costo, usuario). **Nuevos:** Cuadro 20-A Proveedor y 20-B Categoría |
| 4.3.12 y 4.3.13 | Paquetes `gestion` y `menu`; nuevas superficies en la capa de presentación |
| 4.3.14 | Recorrido de la petición hasta SQLite |
| 4.3.15 Despliegue | Windows o Linux, waitress, whitenoise, SQLite, respaldos, teléfonos de los comensales y servicio de IA opcional |
| 4.4 Construcción | Pedidos para llevar, cocina por estación, módulo de inventario, administración y panel, menú digital y asistente |
| 4.5 Validación | Cuadro 27 ampliado, 209 pruebas y **Cuadro 28** con los tiempos medidos y las verificaciones de integridad |
| 4.6 Documentación | Referencias a `docs/manual-usuario.md` y `docs/manual-tecnico.md` |

## Pendiente (hacer en Word)

1. **Año del título.** La portada dice "…Anzoátegui. **2026**." y el objetivo general y la fecha dicen
   **2027**. Hay que decidir cuál es el correcto; no lo cambié porque no sé cuál corresponde.
2. **Índice.** No coincide con los títulos del cuerpo (por ejemplo, "4.1 Fase de inicio" frente a
   "4.1 Diagnosticar…"). En Word se regenera solo: Referencias → Actualizar tabla.
3. **Figuras que hay que rehacer o agregar:**
   - Fig. 3 (casos de uso del sistema): agregar el actor Comensal y el módulo de menú digital.
   - Diagrama de casos de uso del nuevo CU-DIG-01.
   - Fig. 38 (entidad-relación): campos nuevos y las tablas Proveedor y Categoría.
   - Fig. 39 (paquetes): paquetes `gestion` y `menu`.
   - Fig. 42 (despliegue): reemplazar Nginx, Gunicorn, PostgreSQL y Ubuntu por waitress, whitenoise
     y SQLite en un equipo Windows o Linux, y agregar los teléfonos de los comensales y el servicio de IA.
4. **Prueba de aceptación** con los 6 usuarios de la muestra (índice de aprobación del cuadro 2).
5. **Opcional:** la comparación con Toast, Square, Fudo y Loyverse (`docs/comparativa-sistemas.md`)
   puede citarse en el planteamiento o como trabajo futuro.

Hay una copia exacta del archivo anterior en la carpeta de trabajo de esta sesión
(`tesis-def-ORIGINAL.txt`); conviene guardarla también en otro lugar, porque `tesis def.txt` no
está en git.
