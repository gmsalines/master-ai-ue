# Comisión Supervisora de Entidades Financieras (CSEF) — documento ficticio con fines académicos

## Régimen informativo mensual de saldos y movimientos de cuentas — Diseño de registro, revisión 4

### Introducción

Las entidades alcanzadas deberán remitir, dentro de los diez días hábiles siguientes al cierre de cada mes, un archivo con los saldos de las cuentas de sus clientes y los movimientos registrados en ellas durante el mes.

### Aspectos generales

El archivo será de tipo texto, con registros de 120 posiciones cada uno, separados por un carácter de salto de línea (LF). Las posiciones se expresan en base 1.

Para la definición del formato de cada campo se utiliza la siguiente notación:

- X(n): campo alfanumérico de n posiciones, alineado a la izquierda y completado con espacios.
- 9(n): campo numérico de n posiciones, alineado a la derecha y completado con ceros.
- 9(n)V99: campo numérico con n posiciones enteras y 2 decimales implícitos (sin coma ni punto). Ocupa n + 2 posiciones.

Todos los campos son obligatorios salvo indicación en contrario. Los campos denominados "Filler" no son obligatorios y deben completarse con espacios.

El archivo contendrá, en este orden: un registro de encabezado (CAB), un registro por cada cuenta (CTA), los registros de movimientos (MOV) y un registro de totales de control (TOT). El encabezado deberá ser el primer registro y el de totales el último; ambos se informan una sola vez. Debe informarse al menos una cuenta. Las cuentas sin movimientos en el mes se informan igualmente, sin registros MOV asociados.

### Registro de encabezado (CAB)

| Campo | Descripción | Pos. inicial | Formato | Notas |
|-------|-------------|--------------|---------|-------|
| Tipo de registro | Identificador del registro | 1 | X(3) | Constante "CAB" |
| Código de entidad | Código asignado por la Comisión a la entidad informante | 4 | 9(6) | |
| Fecha de corte | Último día del mes informado | 10 | 9(8) | Formato AAAAMMDD |
| Moneda | Moneda de expresión de los importes | 18 | X(3) | Ver nota (4) |
| Denominación | Razón social de la entidad | 21 | X(60) | |
| Filler | | 81 | X(40) | |

### Registro de cuenta (CTA)

| Campo | Descripción | Pos. inicial | Formato | Notas |
|-------|-------------|--------------|---------|-------|
| Tipo de registro | Identificador del registro | 1 | X(3) | Constante "CTA" |
| Número de cuenta | Identificación interna de la cuenta | 4 | X(20) | |
| Tipo de cuenta | Modalidad de la cuenta | 24 | X(1) | Ver nota (1) |
| Titular | Identificación del titular | 25 | X(15) | |
| Saldo inicial | Saldo al inicio del mes | 40 | 9(13)V99 | |
| Total créditos | Suma de los créditos del mes | 55 | 9(13)V99 | |
| Total débitos | Suma de los débitos del mes | 70 | 9(13)V99 | |
| Saldo final | Saldo a la fecha de corte | 85 | 9(13)V99 | |
| Fecha de vencimiento | Vencimiento del depósito | 100 | 9(8) | Ver nota (2) |
| Filler | | 108 | X(13) | |

### Registro de movimiento (MOV)

| Campo | Descripción | Pos. inicial | Formato | Notas |
|-------|-------------|--------------|---------|-------|
| Tipo de registro | Identificador del registro | 1 | X(3) | Constante "MOV" |
| Número de cuenta | Cuenta en la que se registra el movimiento | 4 | X(20) | |
| Fecha de operación | Fecha en que se registró el movimiento | 24 | 9(8) | Formato AAAAMMDD |
| Tipo de movimiento | Sentido del movimiento | 32 | X(1) | Ver nota (3) |
| Importe | Importe del movimiento | 33 | 9(13)V99 | |
| Referencia | Texto libre de referencia | 48 | X(30) | Opcional |
| Filler | | 78 | X(43) | |

### Registro de totales de control (TOT)

| Campo | Descripción | Pos. inicial | Formato | Notas |
|-------|-------------|--------------|---------|-------|
| Tipo de registro | Identificador del registro | 1 | X(3) | Constante "TOT" |
| Cantidad de cuentas | Cantidad de registros CTA | 4 | 9(7) | |
| Cantidad de movimientos | Cantidad de registros MOV | 11 | 9(9) | |
| Suma de créditos | Suma de los totales de créditos de todas las cuentas | 20 | 9(15)V99 | |
| Suma de débitos | Suma de los totales de débitos de todas las cuentas | 37 | 9(15)V99 | |
| Filler | | 54 | X(67) | |

### Notas

(1) Tipo de cuenta: C = cuenta corriente; A = caja de ahorro; P = depósito a plazo fijo.

(2) Fecha de vencimiento en formato AAAAMMDD. Es obligatoria para los depósitos a plazo fijo y debe dejarse en blanco para los demás tipos de cuenta.

(3) Tipo de movimiento: C = crédito; D = débito.

(4) Moneda: MNL = moneda nacional; USD = dólar estadounidense; EUR = euro.

### Controles de consistencia

La Comisión aplicará los siguientes controles al recibir el archivo. Un archivo que no los supere se considerará no presentado.

Para cada cuenta, el saldo final debe ser igual al saldo inicial más el total de créditos menos el total de débitos. A su vez, el total de créditos de la cuenta debe coincidir con la suma de los importes de los movimientos de crédito registrados en esa cuenta, y el total de débitos con la suma de los importes de sus movimientos de débito.

Cada movimiento debe corresponder a una cuenta informada en un registro CTA del mismo archivo. El importe de un movimiento debe ser siempre mayor que cero, y ningún movimiento puede tener una fecha de operación posterior a la fecha de corte del encabezado.

En el registro de totales, la cantidad de cuentas y la cantidad de movimientos deben coincidir con la cantidad de registros CTA y MOV del archivo, respectivamente. La suma de créditos debe ser igual a la suma del total de créditos de todas las cuentas, y la suma de débitos igual a la suma del total de débitos de todas las cuentas.
