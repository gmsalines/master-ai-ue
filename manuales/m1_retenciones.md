# Autoridad Receptora de Información (ARI) — documento ficticio con fines académicos

## Especificación técnica del archivo "Declaración Informativa de Retenciones Practicadas" — versión 2.1

### 1. Objeto

La presente especificación describe el formato del archivo que los agentes de retención deben presentar mensualmente para informar las retenciones practicadas en el período.

### 2. Características generales del archivo

- El archivo es de texto plano con registros de longitud fija de 80 caracteres.
- Cada registro finaliza con los caracteres de control retorno de carro y salto de línea (CR+LF).
- Los campos numéricos se alinean a la derecha y se completan con ceros a la izquierda.
- Los campos alfanuméricos se alinean a la izquierda y se completan con espacios a la derecha.
- Los importes se informan sin separador decimal, con 2 decimales implícitos.
- El archivo se compone de un registro de cabecera (tipo 01), uno o más registros de detalle (tipo 02) y un registro de cierre (tipo 09).
- El registro de cabecera debe ser el primero del archivo y el registro de cierre debe ser el último.

### 3. Registro tipo 01 — Cabecera

Se informa un único registro de cabecera por archivo.

| Nº | Campo | Desde | Hasta | Long. | Tipo | Oblig. | Observaciones |
|----|-------|-------|-------|-------|------|--------|---------------|
| 1 | Tipo de registro | 1 | 2 | 2 | N | S | Valor fijo "01" |
| 2 | Identificador del agente de retención | 3 | 13 | 11 | N | S | Número de identificación tributaria del agente |
| 3 | Período informado | 14 | 19 | 6 | Fecha | S | Formato AAAAMM |
| 4 | Razón social del agente | 20 | 69 | 50 | A | S | |
| 5 | Versión del formato | 70 | 72 | 3 | A | S | Valor fijo "2.1" |
| 6 | Relleno | 73 | 80 | 8 | A | N | Completar con espacios |

### 4. Registro tipo 02 — Detalle de retenciones

Se informa un registro por cada comprobante de retención emitido en el período.

| Nº | Campo | Desde | Hasta | Long. | Tipo | Oblig. | Observaciones |
|----|-------|-------|-------|-------|------|--------|---------------|
| 1 | Tipo de registro | 1 | 2 | 2 | N | S | Valor fijo "02" |
| 2 | Fecha de la retención | 3 | 10 | 8 | Fecha | S | Formato AAAAMMDD |
| 3 | Tipo de documento del sujeto retenido | 11 | 12 | 2 | N | S | Ver tabla de códigos 1 |
| 4 | Número de documento del sujeto retenido | 13 | 27 | 15 | A | S | |
| 5 | Base imponible | 28 | 42 | 15 | N | S | 2 decimales implícitos |
| 6 | Alícuota aplicada | 43 | 47 | 5 | N | S | Porcentaje con 2 decimales implícitos (ej.: 00350 = 3,50 %) |
| 7 | Importe retenido | 48 | 62 | 15 | N | S | 2 decimales implícitos |
| 8 | Número de comprobante de retención | 63 | 76 | 14 | A | S | |
| 9 | Relleno | 77 | 80 | 4 | A | N | Completar con espacios |

**Tabla de códigos 1 — Tipo de documento:** 01 = identificación tributaria; 02 = documento nacional de identidad; 03 = pasaporte.

### 5. Registro tipo 09 — Cierre

| Nº | Campo | Desde | Hasta | Long. | Tipo | Oblig. | Observaciones |
|----|-------|-------|-------|-------|------|--------|---------------|
| 1 | Tipo de registro | 1 | 2 | 2 | N | S | Valor fijo "09" |
| 2 | Cantidad de registros de detalle | 3 | 10 | 8 | N | S | |
| 3 | Importe total retenido | 11 | 25 | 15 | N | S | 2 decimales implícitos |
| 4 | Relleno | 26 | 80 | 55 | A | N | Completar con espacios |

### 6. Validaciones

La Autoridad Receptora rechazará el archivo cuando no se cumpla alguna de las siguientes condiciones:

1. La cantidad de registros de detalle informada en el registro de cierre debe coincidir con la cantidad de registros tipo 02 del archivo.
2. El importe total retenido del registro de cierre debe ser igual a la suma de los importes retenidos de todos los registros tipo 02.
3. En cada registro de detalle, el importe retenido debe ser igual a la base imponible multiplicada por la alícuota y dividida entre 100, redondeado a dos decimales.
4. La base imponible de cada retención debe ser mayor que cero.
