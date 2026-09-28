# Unidad de Análisis de Operaciones (UAO) — documento ficticio con fines académicos

## Guía para la presentación del Reporte Periódico de Operaciones — Esquema 3.0

### Presentación

Los sujetos obligados presentarán mensualmente un reporte con la totalidad de las operaciones realizadas por sus clientes en el período. El reporte se remite como un único documento XML codificado en UTF-8. Esta guía describe los elementos que lo componen; en caso de discrepancia entre esta guía y el esquema publicado, prevalece la guía.

### Estructura del documento

El elemento raíz del documento se denomina ReporteOperaciones. Dentro de él se incluyen, en este orden, un único elemento Cabecera, uno o más elementos Operacion (uno por cada operación reportada) y, al final, un único elemento Resumen. Los elementos simples que se describen a continuación deben aparecer en el orden indicado. Salvo que se diga lo contrario, todos los elementos son obligatorios.

### El elemento Cabecera

La cabecera identifica al sujeto obligado y al período. Contiene el identificador del sujeto obligado, informado en el elemento IdSujetoObligado como un número de once dígitos; el período reportado, en el elemento Periodo, con el formato AAAA-MM; la fecha en que se generó el reporte, en el elemento FechaGeneracion, con el formato AAAA-MM-DD; y la versión del esquema utilizada, en el elemento VersionEsquema, que debe contener siempre el texto 3.0.

### El elemento Operacion

Cada operación se describe con los siguientes datos. El identificador único de la operación asignado por el sujeto obligado va en IdOperacion y es un texto de hasta 20 caracteres. La fecha de la operación se informa en FechaOperacion con el formato AAAA-MM-DD.

El tipo de operación se informa en TipoOperacion mediante uno de estos códigos: TRF para transferencias, DEP para depósitos en efectivo, RET para retiros en efectivo y CAM para operaciones de cambio de divisas.

El importe de la operación se consigna en el elemento Monto, como un número de hasta 15 dígitos en total, de los cuales 2 son decimales, usando el punto como separador decimal (por ejemplo, 1500.00). La moneda de la operación va en el elemento Moneda, expresada con un código de tres letras mayúsculas.

El canal por el cual se realizó la operación se indica en el elemento Canal con uno de estos valores: SUC (sucursal), WEB (banca por internet), APP (aplicación móvil) o ATM (cajero automático).

Por último, cuando corresponda, se informan los datos de la contraparte: el país de la contraparte, en PaisContraparte, con un código de dos letras mayúsculas, y la identificación de la contraparte, en IdContraparte, como texto de hasta 30 caracteres. Ambos elementos son opcionales.

### El elemento Resumen

El resumen cierra el documento. En CantidadOperaciones se informa la cantidad de operaciones reportadas, como número entero de hasta 9 dígitos, y en MontoTotal la suma de los montos de todas las operaciones, como número de hasta 17 dígitos con 2 decimales.

### Reglas de validación

Al recibir el reporte, la Unidad verificará que:

- la cantidad de operaciones del resumen coincida con la cantidad de elementos Operacion del documento;
- el monto total del resumen sea igual a la suma de los montos de todas las operaciones;
- el monto de cada operación sea mayor que cero;
- ninguna operación tenga una fecha posterior a la fecha de generación del reporte;
- en las transferencias se informen obligatoriamente el país y la identificación de la contraparte;
- en las operaciones de cambio de divisas la moneda no sea la moneda nacional, cuyo código es MNL.

### Ejemplo

```xml
<ReporteOperaciones>
  <Cabecera>
    <IdSujetoObligado>30123456789</IdSujetoObligado>
    <Periodo>2025-06</Periodo>
    <FechaGeneracion>2025-07-05</FechaGeneracion>
    <VersionEsquema>3.0</VersionEsquema>
  </Cabecera>
  <Operacion>
    <IdOperacion>OP-000001</IdOperacion>
    <FechaOperacion>2025-06-14</FechaOperacion>
    <TipoOperacion>TRF</TipoOperacion>
    <Monto>1500.00</Monto>
    <Moneda>MNL</Moneda>
    <Canal>WEB</Canal>
    <PaisContraparte>XA</PaisContraparte>
    <IdContraparte>CP-7788</IdContraparte>
  </Operacion>
  <Resumen>
    <CantidadOperaciones>1</CantidadOperaciones>
    <MontoTotal>1500.00</MontoTotal>
  </Resumen>
</ReporteOperaciones>
```
