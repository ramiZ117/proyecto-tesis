# Comparación con sistemas de restaurante del mercado

Comparación del sistema de El Amir con plataformas conocidas, para decidir qué mejorar. Las
funciones de los productos comerciales provienen de sus páginas y de reseñas de 2026; precios y
detalles cambian, así que conviene verificarlos antes de citarlos como dato firme.

## Sistemas revisados

| Sistema | Tipo y mercado | Fortaleza principal |
|---|---|---|
| **Toast** | POS en la nube, EE. UU. | Todo integrado: pedido y pago con QR desde la mesa, modificadores complejos, fidelización, tarjetas de regalo, KDS con enrutamiento por estación y funcionamiento sin internet |
| **Square for Restaurants** | POS en la nube, global | Fácil de empezar (hay plan gratuito); bueno para servicio rápido y cafés |
| **Lightspeed Restaurant** | POS en la nube | Inventario y reportes profundos, varias sucursales |
| **TouchBistro** | POS para iPad | Pensado para servicio en mesa: pedidos junto a la mesa, plano del salón, reservas |
| **Fudo** | POS en la nube, Latinoamérica (más de 30.000 negocios) | Menú QR, app para meseros y comensales, integración con PedidosYa, Rappi y Uber Eats |
| **Loyverse** | POS gratuito, global | Gratis, con KDS e inventario básico; funciones avanzadas en planes pagos |

## Qué tiene cada uno frente a El Amir

✔ = lo tiene · ◐ = parcial · ✘ = no lo tiene

| Función | El Amir | Toast | Square | Fudo | Loyverse |
|---|:-:|:-:|:-:|:-:|:-:|
| Mapa de mesas y comandas | ✔ | ✔ | ✔ | ✔ | ◐ |
| Pedidos para llevar | ✔ | ✔ | ✔ | ✔ | ✔ |
| Monitor de cocina (KDS) con estaciones y tiempos | ✔ | ✔ | ✔ | ◐ | ✔ |
| Inventario por receta con descuento automático | ✔ | ✔ | ◐ | ✔ | ◐ |
| Punto de reposición, mermas y lista de compra | ✔ | ✔ | ◐ | ◐ | ◐ |
| Costo y margen por plato | ✔ | ✔ | ◐ | ◐ | ✘ |
| **Cobro bimonetario Bs/USD con vuelto y tasa del día** | ✔ | ✘ | ✘ | ◐ | ✘ |
| Cuenta dividida por platillo | ✔ | ✔ | ✔ | ✔ | ◐ |
| Cierre de caja por cajero | ✔ | ✔ | ✔ | ✔ | ✔ |
| Panel gerencial | ✔ | ✔ | ✔ | ✔ | ◐ |
| Menú digital por QR con fotos | ✔ | ✔ | ✔ | ✔ | ✘ |
| **Asistente con IA que explica la carta** | ✔ | ✘ | ✘ | ✘ | ✘ |
| Funciona sin internet (red local) | ✔ | ◐ | ◐ | ✘ | ◐ |
| Sin costo de licencia | ✔ | ✘ | ◐ | ✘ | ✔ |
| Pedido y pago del cliente desde el QR | ✘ | ✔ | ✔ | ✔ | ✘ |
| Modificadores con precio (extra queso, tamaño) | ✘ | ✔ | ✔ | ✔ | ✔ |
| Propinas | ✘ | ✔ | ✔ | ✔ | ✘ |
| Impresión de comandas y tickets en impresora térmica | ✘ | ✔ | ✔ | ✔ | ✔ |
| Facturación fiscal (SENIAT, IVA, IGTF) | ✘ | ✘ | ✘ | ◐ | ✘ |
| Delivery integrado (PedidosYa, Rappi) | ✘ | ✔ | ✔ | ✔ | ✘ |
| Fidelización y clientes frecuentes | ✘ | ✔ | ✔ | ◐ | ✔ |
| Descuentos y promociones (2x1, horario feliz) | ✘ | ✔ | ✔ | ✔ | ✔ |
| Reservas y lista de espera | ✘ | ✔ | ◐ | ✘ | ✘ |
| Turnos y asistencia del personal | ✘ | ✔ | ✔ | ◐ | ◐ |
| Varias sucursales | ✘ | ✔ | ✔ | ✔ | ✔ |

## Dónde El Amir ya es mejor

- **Bimonetario de verdad.** Ninguno de los grandes maneja de forma nativa bolívares y dólares en la
  misma cuenta, con vuelto en Bs al cobrar en divisa y tasa congelada en cada factura.
- **Asistente con IA en el menú.** Los menús QR de la competencia muestran la carta; este además
  responde preguntas ("¿qué es el tabule?", "algo vegetariano") con la carta real.
- **Sin internet y sin licencias.** Corre en la red del local; las plataformas en la nube cobran
  mensualidad y dependen de la conexión, un riesgo real con los cortes de luz e internet.
- **Integridad comprobada.** Reglas protegidas en la base de datos y pruebas de concurrencia
  documentadas (`docs/resultados-rendimiento.md`).

## Mejoras recomendadas, por impacto

| Prioridad | Mejora | Por qué | Esfuerzo |
|---|---|---|---|
| 1 | **Modificadores con precio** (extra queso, sin cebolla, tamaño) | Hoy las notas son texto libre y no cambian el precio ni la receta | Medio |
| 2 | **Impresión de comandas y del comprobante** en impresora térmica | En la cocina y la caja de un local real el papel sigue siendo útil como respaldo | Medio |
| 3 | **Descuentos y promociones** con permiso del administrador | Lo pide cualquier operación diaria (cortesías, combos, empleados) | Bajo |
| 4 | **Propinas** en el cobro y en el cierre | Muy común en Venezuela, sobre todo en divisas | Bajo |
| 5 | **Pedido del cliente desde el QR** (sin pago), que llega como comanda pendiente de confirmar por el mesero | Lo que tienen Toast, Square y Fudo; reduce la espera en horas pico | Alto |
| 6 | **Clientes frecuentes** (nombre, teléfono, historial) para pedidos para llevar | Base para promociones por WhatsApp e Instagram | Medio |
| 7 | **Facturación fiscal** (impresora fiscal homologada, IVA e IGTF) | Requisito legal si el sistema reemplaza la facturación actual; hoy queda fuera de alcance | Alto |
| 8 | Integración con **PedidosYa** | El local ya vende por PedidosYa; centralizar esos pedidos en cocina | Alto (depende de la API del proveedor) |

Para la tesis, los puntos 1 a 4 caben en una iteración más de construcción; del 5 al 8 conviene
presentarlos como trabajo futuro.

## Fuentes

- [Toast POS Review 2026 (NerdWallet)](https://www.nerdwallet.com/business/software/reviews/toast-pos)
- [Toast: fast casual POS systems](https://pos.toasttab.com/blog/on-the-line/fast-casual-pos-systems)
- [Lightspeed vs TouchBistro vs Square 2026](https://www.dineopen.com/blog/lightspeed-vs-touchbistro-vs-square-canada-2026)
- [The 7 Best Restaurant POS Systems in 2026](https://dupple.com/learn/best-restaurant-pos-systems)
- [Fudo en Capterra](https://www.capterra.com/p/241757/FUDO/)
- [Fudo en GetApp](https://www.getapp.com/all-software/a/fudo/)
- [Loyverse Review (FitSmallBusiness)](https://fitsmallbusiness.com/loyverse-review/)
- [Loyverse: POS para restaurantes](https://loyverse.com/restaurants-pos)
