"""Idiomas de la interfaz: español (base) y portugués.

Los textos se escriben en español en el código y se envuelven con `_()`; en portugués se busca la traducción
exacta en `PT`. Los mensajes variables del núcleo (validación, resúmenes del cruce) se traducen con patrones
en `PATRONES_PT`. Lo que no tiene traducción se muestra en español (nunca se rompe la interfaz).
"""
from __future__ import annotations

import re

IDIOMAS = {"es": "Español", "pt": "Português"}

PT: dict[str, str] = {
    # encabezado y barra lateral
    "Conciliación de archivos regulatorios": "Conciliação de arquivos regulatórios",
    "Subí tus archivos (CSV, Excel, TXT posicional o XML) y el sistema los cruza. Si un formato es nuevo, lo aprende de su manual técnico: un LLM lo convierte en una especificación formal y un verificador la comprueba.":
        "Envie seus arquivos (CSV, Excel, TXT posicional ou XML) e o sistema os cruza. Se um formato é novo, ele o aprende a partir do manual técnico: um LLM o converte em uma especificação formal e um verificador a confere.",
    "Idioma": "Idioma",
    "Modelo": "Modelo",
    "Proveedor": "Provedor",
    "sin IA (línea base)": "sem IA (linha de base)",
    "(otro)": "(outro)",
    "Nombre del modelo": "Nome do modelo",
    "API key": "Chave de API",
    "Cada modelo tiene su propio cupo diario en el plan gratuito.": "Cada modelo tem sua própria cota diária no plano gratuito.",
    "No se guarda; solo se usa en esta sesión.": "Não é salva; só é usada nesta sessão.",
    "Máximo de iteraciones de autocorrección": "Máximo de iterações de autocorreção",
    "Reutilizar respuestas guardadas (no gasta tokens al repetir)": "Reutilizar respostas salvas (não gasta tokens ao repetir)",
    "Modo de extracción": "Modo de extração",
    "secciones": "seções",
    "completo": "completo",
    "Secciones: una llamada por parte (entra en los planes gratuitos). Completo: una sola respuesta.":
        "Seções: uma chamada por parte (cabe nos planos gratuitos). Completo: uma única resposta.",
    "Cargar una especificación existente": "Carregar uma especificação existente",
    "spec.json": "spec.json",
    "Usar esta especificación": "Usar esta especificação",
    # pestañas
    "🔀 Cruzar archivos": "🔀 Cruzar arquivos",
    "🗂 Mis cruces": "🗂 Meus cruzamentos",
    "📘 Aprender un formato (manual)": "📘 Aprender um formato (manual)",
    "📋 Especificación": "📋 Especificação",
    "✅ Validar archivo": "✅ Validar arquivo",
    "🧾 Generar archivo": "🧾 Gerar arquivo",
    # aprender un formato
    "Manual de ejemplo": "Manual de exemplo",
    "(subir uno propio)": "(enviar um próprio)",
    "…o subí un manual (md, txt, pdf)": "…ou envie um manual (md, txt, pdf)",
    "Ver manual": "Ver manual",
    "Compilar especificación": "Compilar especificação",
    "El modelo está leyendo el manual…": "O modelo está lendo o manual…",
    "Línea base sin IA: {0}": "Linha de base sem IA: {0}",
    "válida": "válida",
    "{0} errores": "{0} erros",
    "Iteración {0}: especificación verificada ✔ ({1} tokens, {2:.1f} s)": "Iteração {0}: especificação verificada ✔ ({1} tokens, {2:.1f} s)",
    "Iteración {0}: {1} errores → se devuelven al modelo · {2}": "Iteração {0}: {1} erros → devolvidos ao modelo · {2}",
    "Problemas detectados en la iteración {0}": "Problemas detectados na iteração {0}",
    "{0} Paso **{1}** ({2}, {3:.0f} s)": "{0} Etapa **{1}** ({2}, {3:.0f} s)",
    "sin IA": "sem IA",
    "desde caché, 0 tokens": "do cache, 0 tokens",
    "{0} tokens": "{0} tokens",
    "Guardada en la biblioteca como '{0}': desde ahora el sistema reconoce este formato al cruzar archivos.":
        "Salva na biblioteca como '{0}': a partir de agora o sistema reconhece este formato ao cruzar arquivos.",
    "Resultado": "Resultado",
    "Válida": "Válida",
    "Con errores": "Com erros",
    "{0} iteraciones · {1} tokens": "{0} iterações · {1} tokens",
    # especificación
    "Primero compilá un manual o cargá una especificación.": "Primeiro compile um manual ou carregue uma especificação.",
    "Formato": "Formato",
    "Tipos de registro": "Tipos de registro",
    "Campos": "Campos",
    "Reglas": "Regras",
    "Verificador: sin errores": "Verificador: sem erros",
    "Verificador: {0} errores": "Verificador: {0} erros",
    "ocurrencias {0}..{1} · posición {2}": "ocorrências {0}..{1} · posição {2}",
    "Descargar especificación (JSON)": "Baixar especificação (JSON)",
    "Archivo de ejemplo": "Arquivo de exemplo",
    "Esquema XSD": "Esquema XSD",
    # validar
    "Archivo a validar": "Arquivo a validar",
    "Formato reconocido: {0} · {1}": "Formato reconhecido: {0} · {1}",
    "Formato reconocido: **{0}** · {1}": "Formato reconhecido: **{0}** · {1}",
    "Especificación": "Especificação",
    "Validando campo a campo y reglas…": "Validando campo a campo e regras…",
    "(compilada en esta sesión)": "(compilada nesta sessão)",
    # generar
    "Cargá los datos de detalle (por ejemplo, el resultado de una conciliación). Los registros únicos (cabecera) se completan abajo; los totales de control se calculan solos a partir de las reglas.":
        "Carregue os dados de detalhe (por exemplo, o resultado de uma conciliação). Os registros únicos (cabeçalho) são preenchidos abaixo; os totais de controle são calculados automaticamente a partir das regras.",
    "Tipo de registro de detalle": "Tipo de registro de detalhe",
    "CSV con los datos": "CSV com os dados",
    "(ignorar)": "(ignorar)",
    "Registro {0} · {1}": "Registro {0} · {1}",
    "Dejar vacío si se calcula a partir de las reglas": "Deixar vazio se for calculado a partir das regras",
    "Generar archivo": "Gerar arquivo",
    "Calculado automáticamente: {0}": "Calculado automaticamente: {0}",
    "Control previo: {0}": "Controle prévio: {0}",
    "Descargar archivo": "Baixar arquivo",
    # cruzar
    "Partir de un cruce guardado": "Partir de um cruzamento salvo",
    "(configuración nueva)": "(configuração nova)",
    "Aplica los mismos grupos, llaves, filtros, importes y tolerancia a los archivos nuevos.":
        "Aplica os mesmos grupos, chaves, filtros, valores e tolerância aos arquivos novos.",
    "Aplicar": "Aplicar",
    "Usando la configuración **{0}**. Subí los archivos del período en el mismo orden (grupo y posición dentro del grupo) y revisá las llaves antes de cruzar.":
        "Usando a configuração **{0}**. Envie os arquivos do período na mesma ordem (grupo e posição dentro do grupo) e revise as chaves antes de cruzar.",
    "1 · Armá los grupos de archivos": "1 · Monte os grupos de arquivos",
    "Cada **grupo** reúne uno o varios archivos. En cada archivo definís su **llave** (y cómo normalizarla); el cruce se hace sobre esa llave entre todos los grupos.":
        "Cada **grupo** reúne um ou vários arquivos. Em cada arquivo você define sua **chave** (e como normalizá-la); o cruzamento é feito sobre essa chave entre todos os grupos.",
    "Nombre del grupo": "Nome do grupo",
    "Grupo {0}": "Grupo {0}",
    "Archivos de «{0}» (uno o varios)": "Arquivos de «{0}» (um ou vários)",
    "Cómo combinar los archivos del grupo": "Como combinar os arquivos do grupo",
    "Concatenar filas (mismo tipo de archivo)": "Concatenar linhas (mesmo tipo de arquivo)",
    "Base + referencia (el 1.º es la base; los demás aportan columnas por llave)": "Base + referência (o 1.º é a base; os demais trazem colunas pela chave)",
    "Sumar importes por llave": "Somar valores por chave",
    "Hoja": "Planilha",
    "Se elige sola la hoja con más datos; la fila de encabezado también se detecta.":
        "A planilha com mais dados é escolhida automaticamente; a linha de cabeçalho também é detectada.",
    "Tabla (Excel)": "Tabela (Excel)",
    "Tabla (CSV)": "Tabela (CSV)",
    "Cambiar formato o tipo de registro": "Alterar formato ou tipo de registro",
    "Tipo de registro a cruzar": "Tipo de registro a cruzar",
    "No reconozco el formato de este archivo. Subí su **manual técnico** y lo aprendo (usa IA una sola vez; después queda guardado).":
        "Não reconheço o formato deste arquivo. Envie o **manual técnico** e eu o aprendo (usa IA uma única vez; depois fica salvo).",
    "Manual técnico (PDF, MD o TXT)": "Manual técnico (PDF, MD ou TXT)",
    "Aprender el formato": "Aprender o formato",
    "Leyendo el manual…": "Lendo o manual…",
    "verificación: OK": "verificação: OK",
    "verificación: {0} errores → corrigiendo": "verificação: {0} erros → corrigindo",
    "Formato aprendido y guardado como '{0}'": "Formato aprendido e salvo como '{0}'",
    "No se obtuvo una especificación válida": "Não foi obtida uma especificação válida",
    "Podés revisarla y corregirla en la pestaña «Aprender un formato».": "Você pode revisá-la e corrigi-la na aba «Aprender um formato».",
    "No se pudo leer {0}: {1}": "Não foi possível ler {0}: {1}",
    "{0} registros": "{0} registros",
    "Columnas de la configuración que no están en este archivo: {0}": "Colunas da configuração que não estão neste arquivo: {0}",
    "Llave (una o varias columnas)": "Chave (uma ou várias colunas)",
    "Sugerida por código: la columna cuyos valores coinciden con los de los otros archivos.":
        "Sugerida por código: a coluna cujos valores coincidem com os dos outros arquivos.",
    "Normalizar la llave": "Normalizar a chave",
    "Exacta (solo recorta espacios)": "Exata (só remove espaços)",
    "Solo números (quita guiones, puntos, letras)": "Só números (remove hífens, pontos, letras)",
    "Sin ceros a la izquierda": "Sem zeros à esquerda",
    "Mayúsculas, sin espacios ni acentos": "Maiúsculas, sem espaços nem acentos",
    "Coincidencia de valores con la llave del primer archivo: {0:.0%}": "Coincidência de valores com a chave do primeiro arquivo: {0:.0%}",
    "Ejemplos de llave: {0}": "Exemplos de chave: {0}",
    "Excluir filas donde…": "Excluir linhas onde…",
    "Incluir solo filas donde…": "Incluir só linhas onde…",
    "Excluir filas donde": "Excluir linhas onde",
    "Incluir solo filas donde": "Incluir só linhas onde",
    "Filtro": "Filtro",
    "Otra condición": "Outra condição",
    "➕ Otra condición": "➕ Outra condição",
    "Columna": "Coluna",
    "Valores": "Valores",
    "Elegí uno o varios valores": "Escolha um ou vários valores",
    "Se toman las filas que coinciden con cualquiera de los valores elegidos.": "São consideradas as linhas que coincidem com qualquer um dos valores escolhidos.",
    "Valores (uno por línea)": "Valores (um por linha)",
    "Escribí un valor por línea; se toman las filas que coinciden con cualquiera.": "Escreva um valor por linha; são consideradas as linhas que coincidem com qualquer um.",
    "es igual a": "é igual a",
    "es distinto de": "é diferente de",
    "es mayor que": "é maior que",
    "es mayor o igual a": "é maior ou igual a",
    "es menor que": "é menor que",
    "es menor o igual a": "é menor ou igual a",
    "está vacío": "está vazio",
    "no está vacío": "não está vazio",
    "(sin filtro)": "(sem filtro)",
    "condición": "condição",
    "valor": "valor",
    "contiene": "contém",
    "Columnas a traer a la base": "Colunas a trazer para a base",
    "Elegí una o varias columnas": "Escolha uma ou várias colunas",
    "➕ Agregar grupo": "➕ Adicionar grupo",
    "➖ Quitar el último": "➖ Remover o último",
    "2 · ¿Qué comparo en cada llave?": "2 · O que comparo em cada chave?",
    "Elegí la columna de importe de cada grupo; se suman por llave y se comparan con la tolerancia.":
        "Escolha a coluna de valor de cada grupo; os valores são somados por chave e comparados com a tolerância.",
    "Cantidad de importes a comparar": "Quantidade de valores a comparar",
    "Tolerancia en importes": "Tolerância nos valores",
    "3 · Resultado": "3 · Resultado",
    "Cruzar": "Cruzar",
    "Cruzando…": "Cruzando…",
    "En todos los grupos": "Em todos os grupos",
    "…con diferencias": "…com diferenças",
    "Solo en {0}": "Só em {0}",
    "Estadísticas por grupo (filas, llaves, duplicados, exclusiones, lookups)": "Estatísticas por grupo (linhas, chaves, duplicados, exclusões, lookups)",
    "**Diferencias de importe**": "**Diferenças de valor**",
    "Ver llaves": "Ver chaves",
    "(todos)": "(todos)",
    "Descargar resultado (Excel)": "Baixar resultado (Excel)",
    "Se muestran las primeras {0} de {1} llaves; el Excel tiene el detalle.": "São mostradas as primeiras {0} de {1} chaves; o Excel tem o detalhe.",
    "El resultado es grande: el Excel se arma a pedido (puede tardar un minuto) y cada hoja se limita a 200.000 filas.":
        "O resultado é grande: o Excel é montado sob demanda (pode levar um minuto) e cada planilha é limitada a 200.000 linhas.",
    "Preparar Excel": "Preparar Excel",
    "Armando el Excel…": "Montando o Excel…",
    "Los archivos de «{0}» tienen columnas distintas y se van a concatenar (sumar sus filas). Si uno es un padrón o tabla de referencia, elegí «Base + referencia» en «Cómo combinar los archivos del grupo».":
        "Os arquivos de «{0}» têm colunas diferentes e serão concatenados (somando suas linhas). Se um deles é um cadastro ou tabela de referência, escolha «Base + referência» em «Como combinar os arquivos do grupo».",
    "💾 Guardar este cruce": "💾 Salvar este cruzamento",
    "Se guardan la configuración (para repetirla con los archivos del próximo período) y el resultado.":
        "São salvos a configuração (para repeti-la com os arquivos do próximo período) e o resultado.",
    "Nombre del cruce": "Nome do cruzamento",
    "Notas (opcional)": "Notas (opcional)",
    "Guardar": "Salvar",
    "Guardado «{0}». Lo encontrás en la pestaña «Mis cruces» y arriba, en «Partir de un cruce guardado».":
        "Salvo «{0}». Você o encontra na aba «Meus cruzamentos» e acima, em «Partir de um cruzamento salvo».",
    "Para probar: Grupo 1 = ejemplos/grupos/g1_retenciones_sistema.csv + g1_padron_referencia.csv (modo base + referencia) · Grupo 2 = ejemplos/grupos/g2_reporte_agente.csv":
        "Para testar: Grupo 1 = ejemplos/grupos/g1_retenciones_sistema.csv + g1_padron_referencia.csv (modo base + referência) · Grupo 2 = ejemplos/grupos/g2_reporte_agente.csv",
    # mis cruces
    "Todavía no hay cruces guardados. Después de cruzar, usá «💾 Guardar este cruce».":
        "Ainda não há cruzamentos salvos. Depois de cruzar, use «💾 Salvar este cruzamento».",
    "Configuración «{0}» lista: andá a «🔀 Cruzar archivos» y subí los archivos del nuevo período.":
        "Configuração «{0}» pronta: vá para «🔀 Cruzar arquivos» e envie os arquivos do novo período.",
    "Llaves en el universo": "Chaves no universo",
    "Archivos: {0}": "Arquivos: {0}",
    "Importes comparados: {0} · tolerancia {1}": "Valores comparados: {0} · tolerância {1}",
    "Resumen completo": "Resumo completo",
    "Descargar Excel": "Baixar Excel",
    "Reutilizar configuración": "Reutilizar configuração",
    "Borrar": "Excluir",
    "Confirmar borrado": "Confirmar exclusão",
    # columnas de tablas
    "grupo": "grupo", "modo": "modo", "archivo": "arquivo", "llave": "chave", "normalización": "normalização",
    "filtros": "filtros", "trae": "traz", "indicador": "indicador", "diferencias": "diferenças", "estado": "estado",
    "en todos": "em todos", "con diferencias": "com diferenças",
    "severidad": "severidade", "origen": "origem", "mensaje": "mensagem", "linea": "linha", "tipo_registro": "tipo_registro",
    "campo": "campo", "regla": "regra", "error": "erro", "advertencia": "aviso", "estructura": "estrutura",
    "ocurrencias": "ocorrências", "archivos": "arquivos", "excluidas por filtros": "excluídas por filtros", "filas": "linhas",
    "llaves únicas": "chaves únicas", "filas con llave repetida": "linhas com chave repetida",
    "llaves vacías (se excluyen del cruce)": "chaves vazias (excluídas do cruzamento)",
    "llaves en el universo": "chaves no universo", "en todos los grupos": "em todos os grupos",
    "en todos, con diferencias": "em todos, com diferenças", "en todos, iguales": "em todos, iguais", "tolerancia": "tolerância",
    "concatenar": "concatenar", "base_referencia": "base + referência", "sumar_por_llave": "somar por chave",
}

# mensajes variables del núcleo (validación y resúmenes del cruce)
PATRONES_PT: list[tuple[str, str]] = [
    (r"^OK: (\d+) registros, sin errores\.$", r"OK: \1 registros, sem erros."),
    (r"^(\d+) errores en (\d+) registros:$", r"\1 erros em \2 registros:"),
    (r"^solo en (.+)$", r"só em \1"),
    (r"^en (.+)$", r"em \1"),
    (r"^filas (.+)$", r"linhas \1"),
    (r"^cobertura de (.+) \(llaves presentes en todos los demás\)$", r"cobertura de \1 (chaves presentes em todos os demais)"),
    (r"^encontradas en (.+)$", r"encontradas em \1"),
    (r"^(\d+) de (\d+) \((.+)\)$", r"\1 de \2 (\3)"),
    (r"^campo obligatorio no informado$", "campo obrigatório não informado"),
    (r"^campo obligatorio '(.+)' sin valor$", r"campo obrigatório '\1' sem valor"),
    (r"^'(.*)' no es una fecha válida con formato (.+)$", r"'\1' não é uma data válida no formato \2"),
    (r"^'(.*)' no respeta exactamente el formato (.+)$", r"'\1' não respeita exatamente o formato \2"),
    (r"^'(.*)' no está entre los valores permitidos (.+)$", r"'\1' não está entre os valores permitidos \2"),
    (r"^'(.*)' no cumple el patrón (.+)$", r"'\1' não cumpre o padrão \2"),
    (r"^'(.*)' mide (\d+) caracteres y el campo tiene ancho (\d+)$", r"'\1' mede \2 caracteres e o campo tem largura \3"),
    (r"^se esperaba el valor fijo '(.*)' y se encontró '(.*)'$", r"esperava-se o valor fixo '\1' e foi encontrado '\2'"),
    (r"^longitud (\d+) mayor que la máxima \((\d+)\)$", r"comprimento \1 maior que o máximo (\2)"),
    (r"^'(.*)' contiene caracteres no numéricos$", r"'\1' contém caracteres não numéricos"),
    (r"^'(.*)' no es un número válido$", r"'\1' não é um número válido"),
    (r"^'(.*)' debe ser entero$", r"'\1' deve ser inteiro"),
    (r"^'(.*)' tiene más de (\d+) decimales$", r"'\1' tem mais de \2 casas decimais"),
    (r"^'(.*)' supera los (\d+) dígitos$", r"'\1' excede \2 dígitos"),
    (r"^la línea mide (\d+) caracteres y debería medir (\d+)$", r"a linha mede \1 caracteres e deveria medir \2"),
    (r"^no se reconoce el tipo de registro$", "tipo de registro não reconhecido"),
    (r"^la línea tiene (\d+) campos y el registro (.+) define (\d+)$", r"a linha tem \1 campos e o registro \2 define \3"),
    (r"^se esperaban al menos (\d+) registros '(.+)' y hay (\d+)$", r"esperavam-se pelo menos \1 registros '\2' e há \3"),
    (r"^se esperaban como máximo (\d+) registros '(.+)' y hay (\d+)$", r"esperavam-se no máximo \1 registros '\2' e há \3"),
    (r"^el registro '(.+)' debe ser el primero del archivo$", r"o registro '\1' deve ser o primeiro do arquivo"),
    (r"^el registro '(.+)' debe ser el último del archivo$", r"o registro '\1' deve ser o último do arquivo"),
    (r"^el elemento raíz es '(.+)' y debería ser '(.+)'$", r"o elemento raiz é '\1' e deveria ser '\2'"),
    (r"^elemento '(.+)' no reconocido$", r"elemento '\1' não reconhecido"),
    (r"^regla no evaluable: (.+)$", r"regra não avaliável: \1"),
    (r"^(\d+) de (\d+) registros leídos sin errores con '(.+)'$", r"\1 de \2 registros lidos sem erros com '\3'"),
    (r"^tabla \(CSV/Excel\): no necesita especificación$", "tabela (CSV/Excel): não precisa de especificação"),
]
_COMPILADOS = [(re.compile(p), r) for p, r in PATRONES_PT]


def traducir(texto, idioma: str):
    """Traduce un texto de la interfaz. Si no hay traducción, lo devuelve igual."""
    if idioma != "pt" or not isinstance(texto, str):
        return texto
    if texto in PT:
        return PT[texto]
    for patron, reemplazo in _COMPILADOS:
        if patron.match(texto):
            return patron.sub(reemplazo, texto)
    return texto


def traducir_df(df, idioma: str, columnas_valor: tuple = ("estado", "severidad", "origen", "mensaje", "indicador", "modo")):
    """Traduce los nombres de columna y los valores de columnas de texto conocidas de una tabla."""
    if idioma != "pt":
        return df
    out = df.copy()
    for c in out.columns:
        if c in columnas_valor:
            out[c] = out[c].map(lambda v: traducir(v, idioma))
    out.columns = [traducir(str(c), idioma) for c in out.columns]
    if out.index.name:
        out.index.name = traducir(out.index.name, idioma)
    out.index = [traducir(str(i), idioma) if isinstance(i, str) else i for i in out.index]
    return out
