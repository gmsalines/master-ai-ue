# Compilador neuro-simbólico de especificaciones regulatorias y conciliación de archivos

*Estado al 1 de octubre de 2026. Rama `siguiente-paso` del repositorio `gmsalines/master-ai-ue`. App publicada en https://conciliacion-tfm.streamlit.app/.*

El sistema tiene dos flujos:

| Flujo | Qué hace | ¿Usa IA? |
|---|---|---|
| **1 · Del manual a la especificación** | Lee el manual técnico (MD/TXT/PDF) y produce una especificación formal verificada del archivo regulatorio. | Sí, lo mínimo: primero intenta con código. Se hace una vez por manual. |
| **2 · Conciliación por grupos** | Se arman **grupos de archivos** (CSV/Excel, o TXT/XML regulatorios leídos con la especificación del flujo 1). En cada archivo se define su **llave** (una o varias columnas) y cómo normalizarla (exacta, solo números, sin ceros a la izquierda, normalizada), más filtros de exclusión. Los archivos de un grupo se combinan concatenando, como base + referencia (lookup por llave) o sumando por llave. El cruce es N-way sobre la llave: presencia en cada grupo, diferencias de importe con tolerancia, duplicados, cobertura; exporta a Excel. | Opcional. La llave se sugiere por código y la IA solo entra como respaldo, con validación sobre los datos. La configuración también puede describirse en texto. |

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

Secuencia del compilador: parser sin IA → LLM por secciones → verificador de 7 etapas → autocorrección localizada (hasta 4 iteraciones; corta si una corrección no produce cambios).

## Por qué es un trabajo de IA y no solo de programación

| Pieza | Qué aporta |
|---|---|
| **LLM con salida estructurada** | Interpreta prosa, tablas heterogéneas, notación COBOL y notas al pie, cosa que un parser por reglas no puede hacer. |
| **DSL + lenguaje de reglas propio** | Es el "objetivo de compilación": acota lo que el modelo puede producir y lo vuelve ejecutable y verificable. |
| **Verificador formal** | Hace de oráculo: detecta alucinaciones y errores de lectura sin que haga falta una respuesta de referencia. |
| **Bucle de autocorrección** | El modelo recibe errores concretos (qué, dónde, cómo corregir) y los corrige. La mejora se mide por iteración. |
| **Anclaje por evidencia** | Cada campo y cada regla deben citar un fragmento literal del manual; si no existe, se trata como alucinación. |
| **Evaluación por comportamiento** | Mide si la especificación extraída *se comporta* como la de referencia sobre una batería de archivos con errores inyectados, con independencia de cómo nombre los campos. |
| **LLM + validación determinista en la conciliación** | La IA propone la llave o la configuración a partir de nombres, ejemplos o una descripción en texto; el código comprueba contra los datos que lo propuesto exista y funcione antes de aplicarlo. |

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
  cruce.py          lectura de archivos, detección de formato, cruce de 2 tablas, sugerencia de llave (código y LLM)
  grupos.py         conciliación N-way por grupos de archivos con llaves (flujo 2)
  instrucciones.py  texto -> configuración del cruce, con validación determinista
  almacen.py        cruces guardados, memoria de configuración y formatos (local o Supabase)
  metricas.py       métricas de uso de la memoria de configuración (eventos, confianza, exportación a Excel)
  i18n.py           textos de la interfaz en español y portugués
  llm/              proveedores desacoplados, prompts, extractor con bucle
  evaluacion/       mutaciones, métricas, banco del compilador (ejecutar.py), banco de conciliación (conciliacion.py), informe.py
manuales/           5 manuales ficticios de dificultad creciente
gold/               especificaciones de referencia escritas a mano (corpus de evaluación)
biblioteca/         formatos reales cargados a mano (sin datos): se reconocen solos al subir un archivo
ejemplos/           CSV para generar un informe; par sistema.csv / presentado.txt; ejemplos/grupos/ para la conciliación por grupos
resultados/         salidas de las evaluaciones (detalle.csv y resumen.md por corrida)
docs/               notas y migraciones SQL
tests/              73 tests
app.py              app en Streamlit
```

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest                                      # 73 tests
```

La clave del proveedor va en un archivo `.env` en la raíz, que no se versiona. En Streamlit Cloud va en *Secrets* (ver más abajo).

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

# 4. App
streamlit run app.py
```

**Modelos.** Por defecto, Groq `openai/gpt-oss-120b`; como alternativa, `openai/gpt-oss-20b` (`--modelo`), que tiene más cupo. El plan gratuito de Groq tiene unos 8.000 tokens por minuto y un cupo diario por modelo que se libera de a poco en 24 horas, de unas 6 extracciones por día y por modelo. Cuando se agota, el sistema lo avisa. Los modelos Qwen no son utilizables en el plan gratuito.

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

## Publicar la app (Streamlit Cloud, gratis)

Publicada en https://conciliacion-tfm.streamlit.app/.

1. Entrá a share.streamlit.io con tu cuenta de GitHub y elegí **Create app → Deploy a public app from GitHub**: repositorio `gmsalines/master-ai-ue`, rama `siguiente-paso`, archivo `app.py`.
2. En **Advanced settings → Secrets**, pegá `GROQ_API_KEY`, `SUPABASE_URL` y `SUPABASE_ANON_KEY` (ver `.streamlit/secrets.toml.ejemplo`).
3. **Deploy.** Los usuarios entran con su mail y contraseña; la clave de IA la toma de los *Secrets*.

Límite a tener en cuenta: el plan gratuito de Streamlit Cloud tiene poca memoria; archivos de referencia de cientos de MB
(por ejemplo, un padrón de millones de filas) conviene recortarlos a las columnas necesarias antes de subirlos.

## Corpus de evaluación del compilador

| Manual | Formato | Qué lo hace difícil |
|---|---|---|
| `m1_retenciones` | TXT 80 pos., CRLF | Tablas limpias con Desde/Hasta/Long. Decimales implícitos, códigos en una tabla aparte, 4 reglas de control. |
| `m2_cuentas` | TXT 120 pos., LF | Sin columna de longitud: hay que deducirla de la notación `X(n)`, `9(n)V99`. Dominios y obligatoriedad en notas al pie, 12 reglas en prosa (sumas por cuenta, integridad referencial, condicionales, comparación con la cabecera). |
| `m3_operaciones_xml` | XML | Todo en prosa, sin tablas. Patrones (tres letras mayúsculas), campos opcionales, 6 reglas. |
| `m4_seguros_pdf` | TXT 100 pos., CRLF | Texto plano como el que sale de un PDF: tablas alineadas con espacios, sin separadores, encabezados y pies de página repetidos, historial de versiones y un **campo obsoleto** que no debe informarse (trampa de alucinación). 10 reglas en prosa. |
| `m5_beneficiarios_inconsistente` | TXT 60 pos., LF | El manual tiene una **errata**: un campo va de la posición 13 a la 42 (30 posiciones) y la columna de longitud dice 28. Una especificación que copia el manual al pie de la letra no es coherente, y el verificador debe detectarlo. |

Los cinco manuales son **ficticios**: no reproducen ningún organismo real.

## Protocolo de evaluación

```bash
# Compilador (E1)
python -m regspec evaluar --solo-sin-ia                              # referencia + línea base
python -m regspec evaluar --proveedor groq --modelo openai/gpt-oss-20b --condiciones llm_1_intento,llm_bucle --repeticiones 1
python -m regspec evaluar --proveedor groq --manuales m1_retenciones,m2_cuentas --condiciones llm_1_intento,llm_bucle

# Conciliación (E2–E5, E3b con --llm, E6 con --solo-texto)
python -m regspec.evaluacion.conciliacion -o resultados/eval_conciliacion
python -m regspec.evaluacion.conciliacion -o resultados/eval_texto_nuevos --solo-texto --semilla-texto 3000 --llm groq --modelo openai/gpt-oss-20b

# Tablas para la memoria
python -m regspec.evaluacion.informe
```

**Costo y límites.** Cada llamada consume unos 5.000 tokens de entrada más la especificación de salida, y una extracción típica usa entre 1 y 4 llamadas (hasta unos 30.000 tokens cuando el bucle agota las iteraciones, como en m4). Con el plan gratuito de Groq conviene repartir el banco en varios días; la caché evita repetir lo ya hecho. Las filas que fallan por cupo se descartan, no cuentan como respuestas del modelo.

**Condiciones del compilador (ablación):**

| Condición | Descripción |
|---|---|
| `referencia` | La especificación gold. Controla el propio banco: debe dar 100 %. |
| `base_sin_ia` | Parser convencional (regex + tablas + notación COBOL). |
| `llm_1_intento` | LLM con una sola llamada, sin verificador. |
| `llm_bucle` | **Sistema propuesto**: LLM + verificador completo + autocorrección. |
| `llm_bucle_sin_evidencia` | Igual al anterior, sin retroalimentación de evidencia. Mide el aporte del anclaje. |
| `llm_bucle_sin_ejecucion` | Igual al anterior, sin la prueba de ida y vuelta. Mide el aporte de la verificación ejecutable. |

**Métricas del compilador:**

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

**Dónde están los resultados** (`resultados/`):

| Exp. | Qué mide | Carpeta |
|---|---|---|
| E1 | Compilador de manuales | `20260930_linea_base_e1`, `20260930_e1_gptoss120b` (m1–m2), `20260930_e1_gptoss120b_m3m5`, `20260930_e1_gptoss20b` (m3–m5) |
| E2 | Motor de conciliación y ablación | `eval_conciliacion` (semilla 1000); línea base v1 en `eval_conciliacion_v1_linea_base` |
| E3 | Sugerencia de llave por código | `eval_conciliacion` |
| E3b | Sugerencia de llave con LLM | `eval_conciliacion_llm` |
| E4 | Memoria de configuración | `eval_conciliacion` |
| E5 | Rendimiento | `eval_conciliacion` |
| E6 | Texto → configuración | `eval_texto` (semilla 2000, diseño) y `eval_texto_nuevos` (semilla 3000) |

`20260927_194002` y `20260930_e1_parcial` son corridas exploratorias o interrumpidas por cupo; no se usan en la memoria.

## Resultados

### E1 · Compilador de manuales (n = 1)

Exactitud balanceada por manual (✓ = especificación válida según el verificador, ✗ = no válida):

| manual | parser sin IA | LLM, 1 intento | LLM + verificador + autocorrección | modelo |
|---|---|---|---|---|
| m1 | 0,92 ✓ | 0,98 ✓ | 0,98 ✓ (1 iter.) | gpt-oss-120b |
| m2 | 0,10 ✓ | 0,98 ✗ | 0,98 ✓ (2 iter.) | gpt-oss-120b |
| m3 | 0,00 ✗ | 0,00 ✗ | 0,93 ✓ (2 iter.) | gpt-oss-20b |
| m4 | 0,00 ✗ | 0,00 ✗ | 0,00 ✗ (3 iter., sin progreso) | gpt-oss-20b |
| m5 | 0,89 ✗ | 1,00 ✗ | 1,00 ✓ (2 iter.) | gpt-oss-20b |
| **especificaciones válidas** | 2 de 5 | 1 de 5 | 4 de 5 | |
| **media** | 0,38 | 0,59 | 0,78 | |

- E1 **mezcla modelos** por el cupo: m1–m2 con `gpt-oss-120b` y m3–m5 con `gpt-oss-20b`. Con `gpt-oss-120b`, el bucle se estancó en m3, m4 y m5 («la corrección no produjo cambios») y ninguna especificación quedó válida.
- En m2 el sistema completo extrajo 10 de 12 reglas.
- m4 (texto de PDF) falló en ambos modelos, pero el sistema **no entregó una especificación incorrecta como válida**: avisó del fallo.
- En m5 el intento único se comporta bien (1,00) pero arrastra la errata del manual; el verificador la bloquea y el bucle la corrige.
- **Hallazgo:** el verificador bloquea siempre las especificaciones defectuosas, pero que la autocorrección funcione depende del modelo.

### E2 · Motor de conciliación

24 casos sintéticos: 4 escenarios × 3 variantes de nombres (es, en, pt) × 2 dificultades.

| condición | casos | exactitud |
|---|---|---|
| completa | 24 | 100,0 % |
| sin transformación de llave | 16 | 5,3 % |
| sin tolerancia | 24 | 90,0 % |
| sin exclusión | 24 | 95,0 % |

### E3 · Sugerencia de llave por código

24 de 24 casos con llave clara y correcta, en las tres variantes de idioma y las dos dificultades. Tiempo medio: unos 54 ms por caso.

### E3b · Sugerencia de llave con LLM

| información enviada | llave correcta | incorrecta aceptada (solo validación estructural) | incorrecta aceptada (+ solidez) | correcta rechazada | columnas inventadas | tokens |
|---|---|---|---|---|---|---|
| solo nombres | 45,8 % | 54,2 % | 0,0 % | 0,0 % | 0 de 170 | 365 |
| nombres + 5 ejemplos | 75,0 % | 20,8 % | 0,0 % | 0,0 % | 0 de 142 | 667 |

El modelo no inventa columnas, pero sí elige columnas equivocadas. La validación por solidez las frena todas sin rechazar ninguna correcta.

### E4 · Memoria de configuración

- **Proceso estable (30 ejecuciones):** la confianza pasa del 95 % en la ejecución 22, a partir de la cual se aplica sola. 23 intervenciones en total, frente a 90 configurando cada vez desde cero.
- **Proceso con cambios (25 ejecuciones:** una exclusión nueva en la 8 y un cambio de columna en la 16): 3 intervenciones en la primera ejecución y 1,17 de media en las siguientes. La corrección baja la confianza y el cambio de estructura reinicia la memoria, así que en este escenario nunca se llega a la aplicación automática.

### E5 · Rendimiento

| registros A | registros B | sugerir llave | cruce | exactitud |
|---|---|---|---|---|
| 900 | 1.000 | 0,06 s | 0,07 s | 100 % |
| 9.000 | 10.000 | 0,17 s | 0,29 s | 100 % |
| 90.000 | 100.000 | 0,75 s | 2,46 s | 100 % |
| 900.000 | 1.000.000 | 7,66 s | 31,12 s | 100 % |

### E6 · Texto → configuración

| conjunto | instrucciones | config. completa sin validar | con validación | exactitud del cruce | tokens |
|---|---|---|---|---|---|
| semilla 2000 (diseño) | 24 | 50,0 % | 91,7 % | 99,8 % | 1.024 |
| semilla 3000 (nuevas) | 23 (1 excluida por cupo) | 65,2 % | 87,0 % | 95,6 % | 990 |

- Las reglas de validación se diseñaron sobre la semilla 2000; la 3000 confirma que generalizan.
- Las instrucciones explícitas quedan al 100 % validadas en ambos conjuntos. Las semánticas, que describen lo que se quiere sin nombrar las columnas, quedan en 83,3 % y 75,0 %.
- Columnas inventadas: 1 y 2 respectivamente; la validación las detecta.

## Decisiones y supuestos del DSL

- Posiciones 1-based e inclusivas. En los decimales, `longitud` incluye los dígitos decimales.
- Numéricos sin signo, alineados a la derecha con ceros. Alfanuméricos alineados a la izquierda con espacios.
- En ancho fijo, el tipo de registro se identifica con un campo `constante` cuyo valor es el código del registro.
- En XML la estructura es plana: raíz → registros → campos simples.
- Formato `delimitado` (p. ej. campos separados por `;`): los campos van en el orden de la lista y los decimales llevan punto explícito. Con `campos_ancho_fijo: true` cada campo ocupa además un ancho exacto (numéricos con ceros a la izquierda; el ancho de un decimal es `longitud + 1`). `codificacion` indica utf-8 o latin-1.
- Si una regla referencia un dato opcional no informado, la regla no aplica a ese registro.

## Conciliación: sugerencia de llaves y archivos grandes

- La llave se sugiere **por código, en conjunto para todos los archivos**: se prueban las columnas identificadoras del primer archivo (descartando importes, fechas y textos) y, para cada una, la columna de cada otro archivo con mayor contención de valores, con la misma normalización para todos (exacta o solo números). Un filtro previo por perfil de dígitos evita recorrer columnas que no pueden coincidir.
- Puntaje de cada par: contención × f(unicidad), con f(u) = min(1, u / 0,5). Una llave es **clara** si su solidez llega a 0,5.
- Si ninguna llave es clara, el botón de llave con IA (`sugerir_llave_ia`) envía al LLM los nombres de columna y 5 ejemplos por archivo. Lo que propone se valida: las columnas tienen que existir y la solidez tiene que llegar a 0,5. El umbral de 0,5 se ajustó a posteriori.
- Excel: se elige la hoja con más datos y se detecta la fila de encabezado (reportes con títulos arriba).
- TXT delimitado de un solo tipo de registro: se lee con pandas; un padrón de ~4,9 millones de líneas (370 MB) se lee en ~8 s y el cruce completo (lookup contra el padrón + 3 grupos) tarda ~5 s. La validación campo a campo se hace en «Validar archivo».
- La app acepta archivos de hasta 1 GB (`.streamlit/config.toml`).

## Describir el cruce con texto

En el expander «Describir el cruce con texto» el usuario escribe qué quiere cruzar en lenguaje natural. El LLM propone la configuración (`regspec/instrucciones.py`) y una validación determinista la corrige antes de aplicarla:

- las columnas, operadores y valores tienen que existir en los archivos;
- los importes tienen que ser columnas numéricas;
- la normalización de la llave se ajusta según la solidez;
- un filtro con doble negación que excluiría la mayoría de los registros se invierte;
- la tolerancia se ancla al número que aparece en el texto.

## Cruces guardados e idiomas

- Después de cruzar, «💾 Guardar este cruce» guarda la **configuración** (grupos, llaves, normalización, filtros, columnas traídas, importes y tolerancia) y el **resultado** (resumen + Excel). En modo local va a `cruces_guardados/` (fuera del repositorio); con usuarios, a Supabase.
- El período siguiente se elige el cruce en «Partir de un cruce guardado» (o «Reutilizar configuración» en la pestaña «Mis cruces»): la configuración se aplica a los archivos nuevos por posición (grupo y orden dentro del grupo) y avisa si falta alguna columna.
- El almacenamiento está detrás de la interfaz `Almacen`, con dos implementaciones: `AlmacenLocal` y `AlmacenSupabase`.
- La interfaz está en **español y portugués** (selector 🌐 en la barra lateral; por defecto, el idioma del navegador). Los textos se escriben en español con `_()` y `regspec/i18n.py` tiene las traducciones, incluidos los mensajes de validación. Un test verifica que todo texto de la app tenga traducción.

## Usuarios y memoria de configuración (Supabase)

Con `SUPABASE_URL` y `SUPABASE_ANON_KEY` en los secrets, la app pide **mail y contraseña** y cada usuario trabaja en su
espacio de trabajo (permisos por fila en la base):

- `conciliacion`: cruces guardados (configuración, resumen, archivos, origen de la sugerencia y si se aceptó); el Excel
  va al bucket `resultados/<usuario>/`.
- `memoria_conciliacion`: **memoria de configuración**. Cada cruce se recuerda asociado a la firma de los archivos (sus
  columnas por grupo, no sus nombres). Cuando el usuario sube archivos con la misma estructura, la app ofrece «Usar la
  configuración aprendida». Se registran `veces_usado` (u) y `veces_corregido` (c), y la confianza se calcula con
  corrección de Laplace: (u − c + 1) / (u + 2). Por debajo del 95 % la configuración se ofrece; por encima se aplica sola.
  Es por usuario. Es un mecanismo **determinista** (conteo de usos y correcciones), no aprendizaje automático.
- `especificacion`: formatos aprendidos por la IA, por espacio de trabajo (en la nube el disco no persiste).
- `perfil.idioma`: la app abre en el idioma del usuario y recuerda el que elija.

Sin esas claves, todo funciona en modo local (sin login) y la memoria se guarda en `cruces_guardados/memoria.json`.

## Pendientes

- E6 semilla 3000: completar la instrucción que falló por cupo (la caché reutiliza las otras 23).
- E1 con `gpt-oss-20b` en m1 y m2, para tener los dos modelos en los cinco manuales; idealmente una segunda repetición con `--sin-cache`.
- Ablaciones del compilador (`llm_bucle_sin_evidencia`, `llm_bucle_sin_ejecucion`) con los cinco manuales.
- Caso real anonimizado para medir el tiempo frente al proceso manual.
- Probar a mano en la app el botón de llave con IA y «Describir el cruce con texto».

## Limitaciones y líneas futuras

- Resultados del compilador con n = 1 y modelos mezclados.
- Cuando el bucle no progresa, reintentar con otra temperatura u otro modelo.
- Textos extraídos de PDF con maquetación compleja (m4).
- Instrucciones en texto que describen el filtro por su significado y no por la columna.
- Estructuras XML anidadas y registros jerárquicos (padre/hijo).
- Campos con signo y formatos numéricos con separador explícito en ancho fijo.
- Transmisión a los organismos (API o web service) y catálogo amplio de jurisdicciones.
- Detección de inconsistencias del propio manual: el verificador ya las señala (m5); falta el caso de estudio.
