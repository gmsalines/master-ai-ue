# Compilador neuro-simbólico de especificaciones regulatorias

El sistema tiene dos flujos:

| Flujo | Qué hace | ¿Usa IA? |
|---|---|---|
| **1 · Del manual a la especificación** | Lee el manual técnico (MD/TXT/PDF) y produce una especificación formal verificada del archivo regulatorio. | Sí, lo mínimo: primero intenta con código. Se hace una vez por manual. |
| **2 · Conciliación por grupos** | Se arman **grupos de archivos** (CSV/Excel, o TXT/XML regulatorios leídos con la especificación del flujo 1). En cada archivo se define su **llave** (una o varias columnas) y cómo normalizarla (exacta, solo números, sin ceros a la izquierda, normalizada), más filtros de exclusión. Los archivos de un grupo se combinan concatenando, como base + referencia (lookup por llave) o sumando por llave. El cruce es N-way sobre la llave: presencia en cada grupo, diferencias de importe con tolerancia, duplicados, cobertura; exporta a Excel. | No. La llave se sugiere por código (solapamiento de valores y nombre de columna). |

Además, con la especificación el sistema **valida** archivos antes del envío y **genera** el archivo a partir de un CSV, calculando solo los totales de control.

**De la normativa al archivo validado.** Un LLM lee el manual técnico de un archivo regulatorio (layout TXT posicional o XML, en prosa o en tablas) y lo convierte en una **especificación formal ejecutable**. Un verificador comprueba esa especificación con reglas deterministas y, si encuentra errores, se los devuelve al modelo para que los corrija. Con la especificación verificada, el sistema genera el archivo, su XSD y los totales de control, y valida los datos antes de enviarlos.

```
                    ┌──────────────────────── problemas accionables ───────────────────────┐
                    ▼                                                                       │
 manual (md/pdf) ─► LLM ─► JSON ─► VERIFICADOR ─────────────────────────────────────────────┤
                   (salida         0 esquema      3 layout (huecos, solapes, longitud)      │
                    estructurada)  1 estructura   4 reglas (sintaxis, tipos, referencias)   │
                                   2 campos       5 evidencia literal en el manual          │
                                                  6 ejecución (ida y vuelta + XSD) ─► válida
                                                                                            │
      especificación verificada ◄───────────────────────────────────────────────────────────┘
          │
          ├─► generar archivo TXT / XML  (+ totales de control derivados de las reglas)
          ├─► generar XSD
          └─► validar datos antes del envío (línea, campo y regla de cada error)
```

## Por qué es un trabajo de IA y no solo de programación

| Pieza | Qué aporta |
|---|---|
| **LLM con salida estructurada** | Interpreta prosa, tablas heterogéneas, notación COBOL y notas al pie, cosa que un parser por reglas no puede hacer. |
| **DSL + lenguaje de reglas propio** | Es el "objetivo de compilación": acota lo que el modelo puede producir y lo vuelve ejecutable y verificable. |
| **Verificador formal** | Hace de oráculo: detecta alucinaciones y errores de lectura sin que haga falta una respuesta de referencia. |
| **Bucle de autocorrección** | El modelo recibe errores concretos (qué, dónde, cómo corregir) y los corrige. La mejora se mide por iteración. |
| **Anclaje por evidencia** | Cada campo y cada regla deben citar un fragmento literal del manual; si no existe, se trata como alucinación. |
| **Evaluación por comportamiento** | Mide si la especificación extraída *se comporta* como la de referencia sobre una batería de archivos con errores inyectados, con independencia de cómo nombre los campos. |

## Estructura

```
regspec/
  dsl.py            modelo de la especificación (pydantic)
  expr.py           lenguaje de reglas: chequeo estático de tipos + evaluación segura (sin eval)
  valores.py        conversión campo <-> texto (ancho fijo y XML)
  archivos.py       escritura/lectura TXT y XML, generación de XSD
  validador.py      validación de un archivo de datos (control previo al envío)
  sintetico.py      generación de archivos válidos a partir de la especificación
  verificador.py    verificador formal en 7 etapas
  generador.py      generación del informe con autocompletado de totales de control
  base_sin_ia.py    línea base: parser convencional por reglas (sin IA); también primer paso del modo híbrido
  cruce.py          lectura de archivos, detección de formato y cruce de 2 tablas
  grupos.py         conciliación N-way por grupos de archivos con llaves (flujo 2, sin IA)
  almacen.py        cruces guardados: configuración reutilizable + resultado
  i18n.py           textos de la interfaz en español y portugués
  llm/              proveedores desacoplados, prompts, extractor con bucle
  evaluacion/       batería de mutaciones, métricas, banco de evaluación
manuales/           3 manuales ficticios de dificultad creciente
gold/               especificaciones de referencia escritas a mano (corpus de evaluación)
biblioteca/         formatos reales cargados a mano (sin datos): se reconocen solos al subir un archivo
ejemplos/           CSV para generar un informe; par sistema.csv / presentado.txt para el cruce
tests/              41 tests
app.py              demo en Streamlit
```

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest                                      # 41 tests, < 1 s
```

## Uso

```bash
export GROQ_API_KEY=...        # o OPENAI_API_KEY / ANTHROPIC_API_KEY / Ollama local sin clave

# 1. Compilar un manual -> especificación verificada (muestra cada iteración)
python -m regspec extraer manuales/m2_cuentas.md --proveedor groq -o spec_m2.json

# 2. Usarla
python -m regspec verificar spec_m2.json --manual manuales/m2_cuentas.md
python -m regspec muestra   spec_m2.json -o ejemplo.txt
python -m regspec validar   spec_m2.json ejemplo.txt
python -m regspec xsd       gold/m3_operaciones_xml.json -o esquema.xsd

# 3. Cruce de archivos (sin IA)
python -m regspec cruzar ejemplos/cruce_sistema.csv ejemplos/cruce_presentado.txt --spec-b gold/m1_retenciones.json -o cruce.xlsx

# Conciliación por grupos: en la app, pestaña «Cruzar archivos». Ejemplo en ejemplos/grupos/
# (Grupo 1 = g1_retenciones_sistema.csv + g1_padron_referencia.csv en modo base + referencia; Grupo 2 = g2_reporte_agente.csv)

# 4. Demo interactiva
streamlit run app.py
```

Modelo por defecto en Groq: `openai/gpt-oss-120b`. El plan gratuito tiene un cupo diario de tokens por modelo, de unas 6 extracciones por día y por modelo. Cuando se agota, el sistema lo avisa, y se puede seguir con otro modelo (`--modelo openai/gpt-oss-20b`).

**Consumo mínimo de tokens.** La IA solo interviene donde el código no alcanza:

1. **Código primero.** El parser sin IA lee las tablas. Si lee un registro completo y ese registro no remite a notas ni a tablas externas, se acepta sin llamar a la IA. Si remite, la IA recibe el borrador y devuelve solo las correcciones.
2. **Recuperación de fragmentos.** El manual se divide en secciones, y cada paso recibe solo las que necesita: la tabla del registro con sus notas, o el capítulo de validaciones. La búsqueda es léxica, sin embeddings y sin tokens. Esto permite procesar PDF largos.
3. **Prompt de sistema compacto.** Pasó de unos 7.100 caracteres a unos 2.300.
4. **Caché en disco.** La misma pregunta no se paga dos veces. Repetir pruebas y demos cuesta 0 tokens (`--sin-cache` la desactiva). En el banco de evaluación, cada repetición tiene su propia entrada en la caché.
5. **Corte por falta de progreso.** Si una corrección no cambia nada, el bucle se detiene.
6. **Regla contra el borrado de reglas.** Si el modelo elimina una regla que tiene evidencia literal en el manual, en vez de corregirla, el sistema la restaura y le pide la corrección. Este control se agregó porque, en las pruebas, un modelo chico borró reglas para "aprobar" el verificador.

Medición con `gpt-oss-20b` en Groq (tokens totales por extracción):
- m1: de unos 24.000 a unos 9.000.
- m2: de más de 40.000 a unos 12.000.

**Extracción por secciones (modo por defecto).** El sistema no pide la especificación completa en una sola respuesta. La arma en pasos cortos: esqueleto, campos de cada tipo de registro y reglas. Las correcciones también van en lotes, uno por registro, otro para las reglas y otro para los datos globales. Así cada llamada entra en los límites de los planes gratuitos, y el método escala a manuales largos. Con `--modo completo` se usa una sola respuesta.

**Anclaje de atributos.** Además de exigir evidencia literal, el verificador comprueba otras tres cosas. Primero, que la máscara de fecha coincida con la citada en la evidencia. Segundo, que cada valor permitido aparezca en el manual. Tercero, que los ejemplos XML del propio manual resulten válidos con la especificación. Si la evidencia citada no se encuentra, sugiere el fragmento más parecido del manual.

**Corrección localizada.** Cuando el verificador puede ubicar los errores (en qué registro o en qué regla están), el modelo no recibe la especificación completa de nuevo. Recibe solo los fragmentos con problemas y un índice del resto, y devuelve un parche. Así cada iteración consume menos tokens y no se modifica lo que ya estaba bien.

Proveedores soportados: `groq`, `openai`, `openrouter`, `ollama` (local, sin clave) y `anthropic`. Todos pasan por la misma interfaz, así que cambiar de modelo no toca el extractor. Esto mitiga la dependencia de un proveedor externo.

## Publicar la demo (Streamlit Cloud, gratis)

1. Entrá a share.streamlit.io con tu cuenta de GitHub y elegí **New app**: repositorio `gmsalines/master-ai-ue`, rama `main`, archivo `app.py`.
2. En **Advanced settings → Secrets**, pegá `GROQ_API_KEY = "gsk_..."` (ver `.streamlit/secrets.toml.ejemplo`).
3. **Deploy.** La app toma la clave de los *Secrets*: quien la use no necesita cargarla.

## Corpus de evaluación

| Manual | Formato | Qué lo hace difícil |
|---|---|---|
| `m1_retenciones` | TXT 80 pos., CRLF | Tablas limpias con Desde/Hasta/Long. Decimales implícitos, códigos en una tabla aparte, 4 reglas de control. |
| `m2_cuentas` | TXT 120 pos., LF | Sin columna de longitud: hay que deducirla de la notación `X(n)`, `9(n)V99`. Dominios y obligatoriedad en notas al pie, 12 reglas en prosa (sumas por cuenta, integridad referencial, condicionales, comparación con la cabecera). |
| `m3_operaciones_xml` | XML | Todo en prosa, sin tablas. Patrones (tres letras mayúsculas), campos opcionales, 6 reglas. |
| `m4_seguros_pdf` | TXT 100 pos., CRLF | Texto plano como el que sale de un PDF: tablas alineadas con espacios, sin separadores, encabezados y pies de página repetidos, historial de versiones y un **campo obsoleto** que no debe informarse (trampa de alucinación). 10 reglas en prosa. |
| `m5_beneficiarios_inconsistente` | TXT 60 pos., LF | El manual tiene una **errata**: un campo va de la posición 13 a la 42 (30 posiciones) y la columna de longitud dice 28. Una especificación que copia el manual al pie de la letra no es coherente, y el verificador debe detectarlo. |

Los tres manuales son **ficticios**: no reproducen ningún organismo real.

## Protocolo de evaluación

```bash
python -m regspec evaluar --solo-sin-ia                              # referencia + línea base
python -m regspec evaluar --proveedor groq --repeticiones 3          # todas las condiciones
python -m regspec evaluar --proveedor groq --modelo llama-3.1-8b-instant --condiciones llm_1_intento,llm_bucle
```

**Costo y límites.** Cada llamada consume unos 5.000 tokens de entrada más la especificación de salida, y una extracción típica usa entre 1 y 4 llamadas. El banco completo (3 manuales × 4 condiciones LLM × 3 repeticiones) ronda el millón de tokens. Antes de correrlo, revisá los límites diarios del plan gratuito del proveedor, porque pueden no alcanzar. Alternativas: un plan pago del mismo proveedor (el costo con un modelo de 70B es bajo), OpenRouter, o Ollama local.

**Condiciones (ablación):**

| Condición | Descripción |
|---|---|
| `referencia` | La especificación gold. Controla el propio banco: debe dar 100 %. |
| `base_sin_ia` | Parser convencional (regex + tablas + notación COBOL). |
| `llm_1_intento` | LLM con una sola llamada, sin verificador. |
| `llm_bucle` | **Sistema propuesto**: LLM + verificador completo + autocorrección. |
| `llm_bucle_sin_evidencia` | Igual al anterior, sin retroalimentación de evidencia. Mide el aporte del anclaje. |
| `llm_bucle_sin_ejecucion` | Igual al anterior, sin la prueba de ida y vuelta. Mide el aporte de la verificación ejecutable. |

**Métricas:**

- *Estructurales* (contra la gold):
  - F1 de campos (posición y longitud exactas, o etiqueta XML).
  - Exactitud por atributo (tipo, decimales, máscara, obligatoriedad, constante, dominio, patrón).
  - Proporción de campos completamente exactos.
- *Funcionales* (por comportamiento, sobre 37 a 57 casos por manual):
  - Especificidad: acepta los archivos válidos.
  - Sensibilidad: rechaza los archivos con un error inyectado de campo, de estructura o de regla.
  - Exactitud balanceada.
  - Una detección solo cuenta si la especificación acepta el archivo válido del que deriva la mutación, así que rechazar todo no suma.
- *Proceso*: especificación válida (sí/no), iteraciones, tokens, latencia.

**Resultados actuales (sin LLM todavía):**

| manual | condición | spec válida | F1 campos | campos exactos | reglas | especificidad | sensibilidad | exact. balanceada |
|---|---|---|---|---|---|---|---|---|
| m1 | referencia | 1.00 | 1.00 | 1.00 | 4 | 1.00 | 1.00 | 1.00 |
| m1 | base_sin_ia | 1.00 | 1.00 | 0.95 | 0 | 1.00 | 0.84 | 0.92 |
| m2 | referencia | 1.00 | 1.00 | 1.00 | 12 | 1.00 | 1.00 | 1.00 |
| m2 | base_sin_ia | 1.00 | 1.00 | 0.86 | 0 | 0.20 | 0.00 | 0.10 |
| m3 | referencia | 1.00 | 1.00 | 1.00 | 6 | 1.00 | 1.00 | 1.00 |
| m3 | base_sin_ia | 0.00 | 0.00 | 0.00 | 0 | 0.00 | 0.00 | 0.00 |

**Primera corrida con LLM** (27 de septiembre, `qwen/qwen3.8-27b` en Groq, sistema completo, n = 1):

| manual | spec válida | iteraciones | F1 campos | campos exactos | reglas | especificidad | sensibilidad | exact. balanceada |
|---|---|---|---|---|---|---|---|---|
| m1 | 1.00 | 1 | 1.00 | 0.95 | 4 | 1.00 | 1.00 | 1.00 |
| m2 | 1.00 | 1 | 1.00 | 1.00 | 12 | 1.00 | 1.00 | 1.00 |
| m3 | 1.00 | 2 | 1.00 | 1.00 | 6 | 1.00 | 1.00 | 1.00 |

En m3 la primera respuesta no respetaba el esquema (15 errores: longitudes como texto, separador inválido). El verificador los localizó y la corrección por fragmentos los resolvió en una iteración. Falta repetir con varias semillas, correr la ablación y sumar manuales más difíciles: con un modelo de este tamaño, los tres manuales actuales resultan fáciles.

Lectura de la línea base: el parser convencional resuelve bien una tabla limpia (m1), pero no detecta ninguna violación de reglas. En m2 lee bien las posiciones y aun así la especificación es inservible: no puede leer que la fecha de vencimiento es opcional (está en una nota al pie) y rechaza el 80 % de los archivos válidos. En m3, que está todo en prosa, no extrae nada. Las filas de LLM se completan corriendo el banco con una API key.

## Decisiones y supuestos del DSL

- Posiciones 1-based e inclusivas. En los decimales, `longitud` incluye los dígitos decimales.
- Numéricos sin signo, alineados a la derecha con ceros. Alfanuméricos alineados a la izquierda con espacios.
- En ancho fijo, el tipo de registro se identifica con un campo `constante` cuyo valor es el código del registro.
- En XML la estructura es plana: raíz → registros → campos simples.
- Formato `delimitado` (p. ej. campos separados por `;`): los campos van en el orden de la lista y los decimales llevan punto explícito. Con `campos_ancho_fijo: true` cada campo ocupa además un ancho exacto (numéricos con ceros a la izquierda; el ancho de un decimal es `longitud + 1`). `codificacion` indica utf-8 o latin-1.
- Si una regla referencia un dato opcional no informado, la regla no aplica a ese registro.

## Conciliación: sugerencia de llaves y archivos grandes

- La llave se sugiere **por código, en conjunto para todos los archivos**: se prueban las columnas identificadoras del primer archivo (descartando importes, fechas y textos) y, para cada una, la columna de cada otro archivo con mayor contención de valores, con la misma normalización para todos (exacta o solo números). Un filtro previo por perfil de dígitos evita recorrer columnas que no pueden coincidir.
- Excel: se elige la hoja con más datos y se detecta la fila de encabezado (reportes con títulos arriba).
- TXT delimitado de un solo tipo de registro: se lee con pandas; un padrón de ~4,9 millones de líneas (370 MB) se lee en ~8 s y el cruce completo (lookup contra el padrón + 3 grupos) tarda ~5 s. La validación campo a campo se hace en «Validar archivo».
- La app acepta archivos de hasta 1 GB (`.streamlit/config.toml`).

## Cruces guardados e idiomas

- Después de cruzar, «💾 Guardar este cruce» guarda la **configuración** (grupos, llaves, normalización, filtros, columnas traídas, importes y tolerancia) y el **resultado** (resumen + Excel) en `cruces_guardados/` (fuera del repositorio).
- El período siguiente se elige el cruce en «Partir de un cruce guardado» (o «Reutilizar configuración» en la pestaña «Mis cruces»): la configuración se aplica a los archivos nuevos por posición (grupo y orden dentro del grupo) y avisa si falta alguna columna.
- El almacenamiento está detrás de la interfaz `Almacen`; para la versión publicada con usuarios se reemplaza `AlmacenLocal` por una base de datos.
- La interfaz está en **español y portugués** (selector 🌐 en la barra lateral; por defecto, el idioma del navegador). Los textos se escriben en español con `_()` y `regspec/i18n.py` tiene las traducciones, incluidos los mensajes de validación. Un test verifica que todo texto de la app tenga traducción.

## Limitaciones y líneas futuras

- Estructuras XML anidadas y registros jerárquicos (padre/hijo).
- Campos con signo y formatos numéricos con separador explícito en ancho fijo.
- Manuales largos: extracción por secciones y fusión de especificaciones parciales.
- Transmisión a los organismos (API o web service) y catálogo amplio de jurisdicciones.
- Detección de inconsistencias del propio manual (el verificador ya las señala; falta el caso de estudio).
