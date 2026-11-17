# Prueba de rendimiento y concurrencia

- Fecha: 17/11/2026 09:33
- Usuarios simultáneos: 6 · rondas por usuario: 5
- Peticiones: 232 en 2.9 s (79.4 por segundo)
- Base de datos: SQLite (modo IMMEDIATE + WAL), servidor de aplicación en el mismo equipo

## Tiempos de respuesta (RNF-01: menos de 2 s)

| Operación | Peticiones | Mediana (ms) | P95 (ms) | Máximo (ms) |
|---|---:|---:|---:|---:|
| Agregar plato | 46 | 67 | 130 | 270 |
| Abrir mesa (carrera) | 6 | 62 | 116 | 116 |
| Cobrar | 17 | 38 | 77 | 82 |
| Emitir factura | 23 | 37 | 872 | 1426 |
| Ver cuentas de caja | 17 | 26 | 37 | 38 |
| Enviar a cocina | 23 | 22 | 90 | 159 |
| Ver mapa del salón | 23 | 21 | 78 | 104 |
| Abrir mesa | 30 | 20 | 106 | 166 |
| Ver monitor de cocina | 23 | 18 | 161 | 218 |
| Liberar mesa | 24 | 11 | 132 | 155 |
| **Todas** | **232** | **27** | **135** | **1426** |

Resultado RNF-01: cumple (máximo 1426 ms).

## Integridad bajo concurrencia (RNF-02)

| Regla | Resultado | Detalle |
|---|---|---|
| Una sola comanda abierta por mesa | cumple | 0 mesas con más de una |
| Números de factura únicos | cumple | 17 facturas |
| Una factura vigente por pedido | cumple | 0 pedidos con dos |
| Pagos = total en facturas pagadas | cumple | 0 descuadres |
| Stock nunca negativo | cumple | 0 insumos negativos |

## Rechazos esperados por las reglas de negocio

- Factura no emitida (otro cajero ya la emitió o la comanda quedó sin platos): 6
- Mesa por limpiar u ocupada por otro ciclo: 7

Un rechazo esperado es una operación que el sistema negó para proteger los datos (por ejemplo, dos cajeros intentando facturar la misma mesa): no es un error.

## Errores

- Ninguno (sin respuestas 5xx ni excepciones).
