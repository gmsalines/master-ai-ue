"""Prompts del extractor. El ejemplo del prompt pertenece a un manual distinto de los de evaluación."""
from __future__ import annotations

import json

from ..dsl import Especificacion

SISTEMA = """Sos un analista experto en archivos regulatorios. Tu tarea es leer un manual técnico y
convertirlo en una ESPECIFICACIÓN FORMAL en JSON, que luego un programa usará para generar y validar
archivos. La especificación se verifica automáticamente: posiciones, longitudes, tipos y reglas deben
ser exactos.

## Modelo de la especificación

- formato: "ancho_fijo" (TXT posicional) o "xml".
- longitud_registro: largo de cada línea (solo ancho_fijo). separador_lineas: "LF" o "CRLF".
- etiqueta_raiz_xml: elemento raíz (solo xml).
- tipos_registro: lista en el orden del archivo. Cada uno con:
  - codigo: en ancho_fijo, el valor que identifica al registro (p. ej. "01", "CAB"); en xml, el nombre del elemento.
  - min_ocurrencias / max_ocurrencias (null = sin límite); posicion: "primero", "ultimo" o "cualquiera".
  - etiqueta_xml (solo xml), campos.
- campos (en ancho_fijo, en orden de posición y cubriendo TODAS las posiciones, incluidos rellenos/filler):
  - nombre: identificador snake_case sin tildes, único en el registro (se usa en las reglas).
  - descripcion: nombre del campo tal como aparece en el manual.
  - inicio (1-based), longitud y fin (fin solo si el manual lo indica; si no, null). En xml, inicio y fin = null.
  - tipo:
      "constante"    valor fijo (p. ej. el tipo de registro "01"); poné valor_constante.
                     En ancho_fijo el campo que identifica el registro es SIEMPRE constante con valor_constante = codigo.
      "numerico"     entero sin decimales.
      "decimal"      con decimales (implícitos en ancho_fijo): longitud = total de dígitos incluidos decimales; poné decimales.
      "fecha"        poné formato_fecha con AAAA, MM, DD (p. ej. "AAAAMMDD", "AAAA-MM-DD", "AAAAMM").
      "alfanumerico" texto.
  - obligatorio: false solo si el manual lo dice (rellenos/filler, campos opcionales).
  - valores_permitidos: lista cerrada de códigos si el manual la define (solo los códigos, sin descripción).
  - patron: expresión regular solo si el manual describe un formato de texto (p. ej. "tres letras mayúsculas" -> "[A-Z]{3}").
  - etiqueta_xml: nombre exacto del elemento (solo xml).
  - evidencia: fragmento COPIADO LITERALMENTE del manual que respalda el campo (una fila de tabla o una frase).
- reglas: validaciones que el manual exige. Cada una con id ("R1", "R2"...), descripcion, ambito ("registro"
  o "archivo"), tipo_registro (codigo, solo si ambito = registro), expresion y evidencia (frase literal).

## Lenguaje de las expresiones de reglas (sintaxis tipo Python)

- Ámbito "registro": los campos del registro se usan por su nombre. Ámbito "archivo": no hay campos sueltos.
- Operadores: + - * /   == != < <= > >=   and or not   x in ("A", "B")
- Textos entre comillas dobles. Números sin comillas. Las fechas se comparan entre sí.
- Funciones:
    implica(condicion, consecuencia)         "si ... entonces ..."
    vacio(campo)                             campo no informado; usar not vacio(campo) para "obligatorio"
    abs(x), redondear(x, n), longitud(texto)
    contar("TIPO")                           cantidad de registros de un tipo
    contar("TIPO", "campo", valor, ...)      con filtros campo == valor
    sumar("TIPO", "campo")                   suma de un campo numérico de todos los registros de un tipo
    sumar("TIPO", "campo", "campo_filtro", valor, ...)   suma filtrada (p. ej. por la cuenta del registro actual)
    valor("TIPO", "campo")                   valor de un campo de un registro único (cabecera, totales)
    existe("TIPO", "campo", valor)           hay algún registro de TIPO con campo == valor

## Reglas de trabajo

1. Usá SOLO información del manual. No inventes campos, códigos ni reglas. Si un dato no está, usá los valores por defecto.
2. Copiá la evidencia literalmente (se comprobará que existe en el manual).
3. Revisá que inicio + longitud - 1 = fin y que los campos cubran exactamente longitud_registro, sin huecos ni solapamientos.
4. Traducí cada validación del manual a una regla; los totales de control del registro de cierre son reglas de ámbito archivo.
5. Para que la respuesta sea breve, omití los atributos con valor por defecto: los que serían null, decimales 0 y obligatorio true (escribí obligatorio solo cuando es false).
6. En XML, cada tipo de registro y cada campo llevan etiqueta_xml con el nombre exacto del elemento.
7. Respondé ÚNICAMENTE con el objeto JSON, sin texto adicional.
"""

EJEMPLO_MANUAL = """Archivo de altas de proveedores. Registros de 30 caracteres, separados por LF.
Registro H (encabezado, único, primero): | Tipo | 1 | 1 | 1 | Valor "H" | ; | Fecha de proceso | 2 | 9 | 8 | AAAAMMDD | ; | Relleno | 10 | 30 | 21 | espacios |
Registro D (uno o más): | Tipo | 1 | 1 | 1 | Valor "D" | ; | Código de proveedor | 2 | 7 | 6 | numérico | ; | Nombre | 8 | 27 | 20 | alfanumérico | ; | Categoría | 28 | 28 | 1 | A, B o C | ; | Relleno | 29 | 30 | 2 | espacios |
Los proveedores de categoría C no pueden tener código menor a 1000."""

EJEMPLO_SALIDA = {
    "nombre": "Altas de proveedores", "version": "1.0", "formato": "ancho_fijo", "longitud_registro": 30,
    "separador_lineas": "LF", "etiqueta_raiz_xml": None,
    "tipos_registro": [
        {"codigo": "H", "nombre": "Encabezado", "min_ocurrencias": 1, "max_ocurrencias": 1, "posicion": "primero", "campos": [
            {"nombre": "tipo", "descripcion": "Tipo", "inicio": 1, "longitud": 1, "fin": 1, "tipo": "constante", "valor_constante": "H", "evidencia": '| Tipo | 1 | 1 | 1 | Valor "H" |'},
            {"nombre": "fecha_proceso", "descripcion": "Fecha de proceso", "inicio": 2, "longitud": 8, "fin": 9, "tipo": "fecha", "formato_fecha": "AAAAMMDD", "evidencia": "| Fecha de proceso | 2 | 9 | 8 | AAAAMMDD |"},
            {"nombre": "relleno", "descripcion": "Relleno", "inicio": 10, "longitud": 21, "fin": 30, "tipo": "alfanumerico", "obligatorio": False, "evidencia": "| Relleno | 10 | 30 | 21 | espacios |"}]},
        {"codigo": "D", "nombre": "Detalle", "min_ocurrencias": 1, "max_ocurrencias": None, "posicion": "cualquiera", "campos": [
            {"nombre": "tipo", "descripcion": "Tipo", "inicio": 1, "longitud": 1, "fin": 1, "tipo": "constante", "valor_constante": "D", "evidencia": '| Tipo | 1 | 1 | 1 | Valor "D" |'},
            {"nombre": "codigo_proveedor", "descripcion": "Código de proveedor", "inicio": 2, "longitud": 6, "fin": 7, "tipo": "numerico", "evidencia": "| Código de proveedor | 2 | 7 | 6 | numérico |"},
            {"nombre": "nombre", "descripcion": "Nombre", "inicio": 8, "longitud": 20, "fin": 27, "tipo": "alfanumerico", "evidencia": "| Nombre | 8 | 27 | 20 | alfanumérico |"},
            {"nombre": "categoria", "descripcion": "Categoría", "inicio": 28, "longitud": 1, "fin": 28, "tipo": "alfanumerico", "valores_permitidos": ["A", "B", "C"], "evidencia": "| Categoría | 28 | 28 | 1 | A, B o C |"},
            {"nombre": "relleno", "descripcion": "Relleno", "inicio": 29, "longitud": 2, "fin": 30, "tipo": "alfanumerico", "obligatorio": False, "evidencia": "| Relleno | 29 | 30 | 2 | espacios |"}]}],
    "reglas": [
        {"id": "R1", "descripcion": "Categoría C con código >= 1000", "ambito": "registro", "tipo_registro": "D",
         "expresion": 'implica(categoria == "C", codigo_proveedor >= 1000)',
         "evidencia": "Los proveedores de categoría C no pueden tener código menor a 1000."}],
}


def mensajes_iniciales(manual: str, con_esquema: bool = True, con_ejemplo: bool = True) -> list[dict]:
    sistema = SISTEMA
    if con_esquema:
        sistema += "\n## Esquema JSON de la salida\n" + json.dumps(Especificacion.model_json_schema(), ensure_ascii=False)
    if con_ejemplo:
        sistema += ("\n\n## Ejemplo (otro manual)\nManual:\n" + EJEMPLO_MANUAL + "\nSalida:\n"
                    + json.dumps(EJEMPLO_SALIDA, ensure_ascii=False))
    return [
        {"role": "system", "content": sistema},
        {"role": "user", "content": "Convertí este manual en la especificación JSON.\n\n=== MANUAL ===\n" + manual + "\n=== FIN DEL MANUAL ==="},
    ]


def mensaje_correccion(problemas_texto: list[str], max_items: int = 40) -> str:
    lista = "\n".join(f"- {p}" for p in problemas_texto[:max_items])
    extra = f"\n(... y {len(problemas_texto) - max_items} problemas más del mismo tipo)" if len(problemas_texto) > max_items else ""
    return (
        "El verificador automático encontró estos problemas en tu especificación:\n"
        f"{lista}{extra}\n\n"
        "Volvé a leer las partes del manual involucradas y devolvé la especificación COMPLETA corregida "
        "(todo el JSON, no solo los cambios). No cambies lo que ya estaba bien. Respondé solo con el JSON."
    )


def mensaje_json_invalido(error: str) -> str:
    return f"Tu respuesta no es un JSON válido ({error}). Respondé únicamente con el objeto JSON completo."


def mensaje_parche(spec: dict, registros: set, reglas: set, globales: bool, problemas_texto: list[str], compactar) -> str:
    """Corrección localizada: se envían solo los fragmentos con problemas y un índice del resto."""
    trs = spec.get("tipos_registro") or []
    indice = {
        "formato": spec.get("formato"), "longitud_registro": spec.get("longitud_registro"),
        "registros": {str(t.get("codigo")): [c.get("nombre") for c in t.get("campos", []) if isinstance(c, dict)]
                      for t in trs if isinstance(t, dict)},
    }
    frag: dict = {}
    if globales:
        frag["globales"] = {k: spec.get(k) for k in ("nombre", "version", "formato", "longitud_registro", "separador_lineas", "etiqueta_raiz_xml")}
    if registros:
        frag["tipos_registro"] = {str(i): trs[i] for i in sorted(registros) if i < len(trs)}
    if reglas:
        rg = spec.get("reglas") or []
        frag["reglas"] = {str(k): rg[k] for k in sorted(reglas) if k < len(rg)}
    lista = "\n".join(f"- {p}" for p in problemas_texto[:40])
    return (
        "Ya generaste la especificación de este manual. El verificador automático encontró estos problemas:\n"
        f"{lista}\n\n"
        f"Índice de la especificación actual (registros y nombres de campos): {json.dumps(indice, ensure_ascii=False)}\n\n"
        f"Fragmentos con problemas (las claves son posiciones en las listas):\n{compactar(frag)}\n\n"
        "Volvé a leer las partes del manual involucradas y devolvé SOLO los fragmentos corregidos, con esta forma:\n"
        '{"globales": {...}, "tipos_registro": {"<posición>": {"campos": {"<nombre>": {atributos que cambian}}, '
        '"eliminar_campos": ["<nombre>"]}}, '
        '"reglas": {"<posición>": {regla corregida} o null para eliminarla}, "reglas_nuevas": [...]}\n'
        "Incluí solo los fragmentos que cambian. Cada regla corregida debe ir COMPLETA (id, descripcion, ambito, "
        "tipo_registro, expresion, evidencia). No toques lo que no tiene problemas. Respondé solo con el JSON."
    )


# Versión compacta para la extracción por secciones (cada llamada recibe solo fragmentos del manual)
SISTEMA_BREVE = """Convertís fragmentos de un manual técnico de archivos regulatorios en una especificación JSON exacta.
Usá SOLO información del texto recibido; no inventes. Respondé ÚNICAMENTE con JSON.

Campo: {"nombre": snake_case, "descripcion": texto del manual, "inicio": n (1-based, ancho fijo), "longitud": n,
"fin": n|null (solo si el manual lo da), "tipo": "constante|numerico|decimal|fecha|alfanumerico",
"decimales": n (solo decimal; longitud incluye los decimales), "formato_fecha": "AAAAMMDD|AAAA-MM-DD|AAAAMM...",
"obligatorio": false (solo si el manual lo dice), "valores_permitidos": ["códigos"], "valor_constante": "..",
"patron": "regex", "etiqueta_xml": "Elemento" (solo XML), "evidencia": fragmento COPIADO LITERAL del manual}.
Omití atributos con valor por defecto, EXCEPTO "evidencia", que es obligatoria en cada campo y cada regla. Ancho fijo: el campo que identifica el registro es tipo constante con
valor_constante = código del registro; incluí rellenos/filler. Notación X(n)=alfanumérico n; 9(n)=numérico n;
9(n)V99=decimal de n+2 con 2 decimales.
Regla: {"id": "R1", "descripcion": "..", "ambito": "registro|archivo", "tipo_registro": código (solo registro),
"expresion": "..", "evidencia": frase COPIADA LITERAL del manual (obligatoria)}. Expresiones tipo Python: and or not, == != < <= > >=, in ("A","B").
En ámbito registro los campos se usan por nombre; en ámbito archivo NO hay campos sueltos. Funciones:
implica(a, b); vacio(campo); abs(x); redondear(x, n); contar("TIPO"[, "campo", valor]...);
sumar("TIPO", "campo"[, "campo", valor]...); valor("TIPO", "campo") (registro único); existe("TIPO", "campo", valor).
valor() SOLO para registros únicos (cabecera, totales). Una validación que se aplica a cada registro repetido es
de ámbito registro, con sus campos por nombre; para comparar con la cabecera: campo <= valor("CAB", "campo").
No uses && || ni TIPO.campo. La obligatoriedad y el formato van en los campos, no como reglas.
Ej.: {"ambito":"archivo","expresion":"valor(\\"09\\", \\"cantidad\\") == contar(\\"02\\")"};
{"ambito":"registro","tipo_registro":"02","expresion":"importe > 0"};
{"ambito":"registro","tipo_registro":"02","expresion":"implica(tipo == \\"P\\", not vacio(fecha_vto))"}."""
