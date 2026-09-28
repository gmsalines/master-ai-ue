# Instituto de Asistencia Social (IAS) — documento ficticio con fines académicos

## Padrón mensual de beneficiarios — Diseño de archivo

### Generalidades

El archivo es de texto con registros de 60 caracteres de longitud fija, separados por salto de línea (LF). Contiene un registro de cabecera (tipo 1, único y primero), uno o más registros de beneficiario (tipo 2) y un registro de cierre (tipo 3, único y último). Los importes se informan con 2 decimales implícitos.

### Registro tipo 1 — Cabecera

| Campo | Desde | Hasta | Long. | Tipo | Observaciones |
|-------|-------|-------|-------|------|---------------|
| Tipo de registro | 1 | 1 | 1 | N | Valor fijo "1" |
| Código de entidad | 2 | 7 | 6 | N | |
| Fecha de proceso | 8 | 15 | 8 | Fecha | Formato AAAAMMDD |
| Relleno | 16 | 60 | 45 | A | Espacios |

### Registro tipo 2 — Beneficiario

| Campo | Desde | Hasta | Long. | Tipo | Observaciones |
|-------|-------|-------|-------|------|---------------|
| Tipo de registro | 1 | 1 | 1 | N | Valor fijo "2" |
| Documento | 2 | 12 | 11 | A | |
| Nombre y apellido | 13 | 42 | 28 | A | |
| Categoría | 43 | 43 | 1 | A | Valores A, B o C |
| Importe | 44 | 55 | 12 | N | 2 decimales implícitos |
| Relleno | 56 | 60 | 5 | A | Espacios |

### Registro tipo 3 — Cierre

| Campo | Desde | Hasta | Long. | Tipo | Observaciones |
|-------|-------|-------|-------|------|---------------|
| Tipo de registro | 1 | 1 | 1 | N | Valor fijo "3" |
| Cantidad de beneficiarios | 2 | 8 | 7 | N | |
| Importe total | 9 | 22 | 14 | N | 2 decimales implícitos |
| Relleno | 23 | 60 | 38 | A | Espacios |

### Validaciones

- La cantidad de beneficiarios del cierre debe ser igual a la cantidad de registros tipo 2.
- El importe total del cierre debe ser igual a la suma de los importes de los registros tipo 2.
- El importe de cada beneficiario debe ser mayor que cero.
- Los beneficiarios de categoría C no pueden percibir un importe superior a 1.000,00.
