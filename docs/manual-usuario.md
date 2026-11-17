# Manual de usuario · El Amir

Este manual explica el trabajo diario con el sistema, rol por rol. Cada persona entra con su
usuario y contraseña y el sistema la lleva directo a su pantalla.

## Entrar y salir

1. Abre el navegador en la tablet, la PC o el teléfono y entra a la dirección del sistema
   (por ejemplo `http://192.168.1.10:8000`).
2. Escribe tu usuario y contraseña y toca **Entrar**. El botón **Ver** muestra la contraseña.
3. Para salir, toca el ícono de salida junto a tu nombre (abajo en el menú lateral).

Si te equivocas 5 veces seguidas, el acceso se bloquea 5 minutos. Si olvidaste la contraseña,
pídele al administrador que te asigne una nueva.

---

## Mesero

### El salón

La pantalla **Salón** muestra el mapa de mesas y se actualiza sola cada 5 segundos. El color
indica el estado:

| Color | Estado | Qué significa |
|---|---|---|
| Blanco con borde verde | Disponible | Lista para sentar clientes |
| Naranja | Ocupada | Tiene una comanda abierta |
| Azul, con un latido | Por facturar | Pidieron la cuenta; caja ya puede cobrar |
| Gris | Por limpiar | Se cobró; hay que limpiarla |

### Tomar un pedido en mesa

1. Toca la mesa. Si estaba libre se abre su comanda; si ya estaba ocupada, ves la comanda actual.
2. En la carta (abajo a la derecha) busca el plato por nombre o filtra por categoría.
3. **Toca el plato para agregarlo.** Tocarlo otra vez suma una unidad. Con **+** y **−** cambias
   la cantidad mientras no se haya enviado.
4. Si el cliente pide algo especial, escríbelo en **Nota para el plato** *antes* de tocarlo
   (por ejemplo "sin ajo"). La nota viaja a cocina con ese plato.
5. Toca **Enviar a cocina**. Solo se envía lo nuevo; lo que ya estaba en cocina no se repite.

Junto a cada plato ves su estado: *Sin enviar*, *En cola*, *Preparando*, *Listo* o *Entregado*.
Si un plato muestra **Agotado** o **Quedan 3**, es porque los insumos de su receta no alcanzan.

### Servir, pedir la cuenta y liberar la mesa

1. Cuando cocina marca un plato como listo, aparece el botón **Entregar**: tócalo al llevarlo a la mesa.
2. Cuando el cliente pide la cuenta, toca **Pedir cuenta**. La mesa pasa a azul y caja la ve primero.
3. Después del cobro la mesa queda gris (*Por limpiar*). Tócala cuando esté limpia y vuelve a estar disponible.

### Otras acciones de la comanda

- **Mover de mesa:** si los clientes se cambian de mesa, elige la mesa disponible de destino.
- **Nota de la comanda:** una nota general para toda la mesa ("cumpleaños", "alérgico al maní");
  cocina la ve en el ticket.
- **Quitar un plato:** con **✕**, solo mientras cocina no lo haya empezado.
- **Anular pedido:** libera la mesa. Si cocina ya preparó algo, solo el administrador puede anularlo.

### Pedidos para llevar

1. En el recuadro **Para llevar** escribe el nombre del cliente y toca **Nuevo pedido**.
2. Agrega los platos y envíalos a cocina igual que en una mesa.
3. Cuando cocina lo termine, el pedido aparece como *Listo para entregar*. Caja lo cobra cuando
   el cliente lo retira (no hace falta pedir la cuenta).

---

## Cocina

La pantalla de cocina tiene fondo oscuro para leerse bien de lejos y se actualiza sola.

- **Columnas:** *Nuevos* (nadie lo empezó), *Preparando* y *Listos para servir* (esperan al mesero).
- **Reloj de cada ticket:** cuenta el tiempo desde que el plato llegó. Borde amarillo = demora
  (más de 15 minutos); borde rojo que parpadea = crítico (más de 25 minutos).
- **Estaciones:** arriba puedes ver *Todo* o solo *Parrilla*, *Fríos* o *Bebidas*.
- **Sonido:** suena un aviso cuando entra un pedido nuevo. El botón **Sonido** lo apaga o enciende.

Para cada plato toca **Empezar** al comenzar y **Listo** al terminar. Los estados no retroceden.
Si al empezar aparece un aviso de insumo crítico, prepara el plato igual y avisa a caja para reponer.

---

## Caja

### Cobrar una cuenta

1. En **Caja** están las cuentas abiertas; las que pidieron la cuenta aparecen primero con un latido.
2. Toca **Emitir factura**. La tasa del día queda congelada en esa factura.
3. Elige el método: **Efectivo** (bolívares), **Pago móvil**, **Divisa** (dólares en efectivo) o **Tarjeta**.
4. Escribe el monto recibido (o toca **Exacto**, **$20**, **$50**…). El sistema muestra al instante
   cuánto se aplica a la cuenta y el **vuelto en bolívares**.
5. Pago móvil y tarjeta piden la **referencia** y no aceptan un monto mayor al saldo (no hay vuelto).
6. Toca **Cobrar y cerrar cuenta**. Si el pago no cubre todo, queda como pago parcial y registras el resto con otro método.

Al saldar aparece **Cuenta saldada** con el vuelto a entregar. La mesa pasa a *Por limpiar* y el
inventario se descuenta solo, según las recetas.

### Dividir la cuenta

Abre **Dividir la cuenta (opcional)**:

- **A partes iguales:** indica entre cuántas personas y el sistema calcula la cuota.
- **Por platillo:** asigna cada plato (o parte) a un comensal por su nombre y cobra a cada uno con
  **Cobrar a…**.

### Anular una factura

- Sin pagos: escribe el motivo y toca **Anular factura**. La mesa vuelve a estar ocupada.
- Con pagos: solo el administrador puede anularla; el sistema registra la devolución de cada pago.

### Tasa de cambio

En **Tasa de cambio** publica la tasa del día (bolívares por dólar). Si la nueva tasa difiere más
de un 20 % de la anterior, el sistema pide confirmarla para evitar errores de tipeo (400 en vez de 40).

### Cierre de caja y reportes

- **Cierre de caja:** muestra lo cobrado desde tu último cierre, por método y moneda, el efectivo
  neto y las divisas que deben estar en la gaveta. Toca **Generar cierre** al terminar el turno.
  El cierre queda guardado tal cual y se puede imprimir o exportar a CSV.
- **Reporte del día:** ventas de una fecha; se imprime o exporta a CSV.

### Inventario

- La tabla muestra cada insumo con una barra de nivel: rojo = **crítico**, amarillo = **reponer**, verde = bien.
- **Disponible** ya descuenta lo pedido en comandas abiertas.
- Con los botones de cada fila registras una **entrada** (compra) o una **merma** (vencido, dañado,
  error de preparación). El formulario de la derecha muestra cómo queda el stock antes de guardar.
- **Lista de compra:** lo que hay que reponer hasta el máximo, agrupado por proveedor; se exporta a CSV.
- **Costo y margen por plato:** cuánto cuesta la receta y qué porcentaje queda de ganancia bruta.

---

## Administrador

El administrador puede hacer todo lo anterior y además:

- **Panel:** ventas de hoy, la semana o el mes comparadas con el período anterior, ventas por hora
  o por día, cobros por método, platos más vendidos, tiempo medio de cocina, alertas de inventario
  y actividad reciente.
- **Usuarios:** crear cuentas, asignar el rol, cambiar contraseñas y activar o desactivar.
  No puedes quitarte el rol de administrador ni desactivarte a ti mismo.
- **Carta y recetas:** platos, precios en dólares, disponibilidad y la receta de cada plato.
- **Proveedores** y la ficha de cada insumo (mínimo, punto de reposición, máximo y costo).
- **Ajuste por conteo** en inventario: cuando el conteo físico no coincide con el sistema.
- **Anular comandas ya preparadas** (lo preparado queda como merma) y **devolver pagos**.
- **Panel técnico:** consulta de facturas, pagos, comandas y movimientos. Esos registros son de
  solo lectura para proteger la auditoría.
