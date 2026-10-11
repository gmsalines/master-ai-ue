"""Idiomas de la interfaz: español (base) y portugués.

Los textos se escriben en español en el código y se envuelven con `_()`; en portugués se busca la traducción
exacta en `PT`. Los mensajes variables del núcleo (validación, resúmenes del cruce) se traducen con patrones
en `PATRONES_PT`. Lo que no tiene traducción se muestra en español (nunca se rompe la interfaz).
"""
from __future__ import annotations

import re

IDIOMAS = {"es": "Español", "pt": "Português"}

PT: dict[str, str] = {
    "Sugerida por el modelo de llave, que aprende de los cruces que confirmás.": "Sugerida pelo modelo de chave, que aprende com os cruzamentos que você confirma.",
    "No se pudo actualizar el modelo de llave": "Não foi possível atualizar o modelo de chave",
    # encabezado y barra lateral
    "Conciliación de archivos regulatorios": "Conciliação de arquivos regulatórios",
    "Conciliación de archivos heterogéneos": "Conciliação de arquivos heterogêneos",
    "Entrá con tu mail para ver tus cruces y configuraciones.": "Entre com seu e-mail para ver seus cruzamentos e configurações.",
    "Subí los archivos que tenés que cruzar, revisá cómo se comparan y descargá el resultado. Si un formato es nuevo, el sistema lo aprende de su manual técnico.":
        "Envie os arquivos que você precisa cruzar, revise como são comparados e baixe o resultado. Se um formato é novo, o sistema o aprende a partir do manual técnico.",
    "Prototipo del Trabajo Fin de Máster. El cruce siempre lo hace el código; la IA solo propone y todo se valida.":
        "Protótipo do Trabalho de Conclusão de Mestrado. O cruzamento é sempre feito pelo código; a IA apenas propõe e tudo é validado.",
    "Configuración avanzada (IA)": "Configuração avançada (IA)",
    "Prototipo · Trabajo Fin de Máster": "Protótipo · Trabalho de Conclusão de Mestrado",
    "Asistido por IA": "Assistido por IA",
    "Determinístico": "Determinístico",
    "Un modelo de lenguaje propone la especificación a partir del manual.": "Um modelo de linguagem propõe a especificação a partir do manual.",
    "Un verificador con código la revisa y le devuelve los errores para que los corrija.": "Um verificador em código a revisa e devolve os erros para que sejam corrigidos.",
    "La IA propone, el verificador decide.": "A IA propõe, o verificador decide.",
    "El código comprueba estructura, posiciones, reglas y evidencia en el manual, y prueba la especificación generando y releyendo un archivo.": "O código verifica estrutura, posições, regras e evidência no manual, e testa a especificação gerando e relendo um arquivo.",
    "Registros, campos y reglas de la especificación activa, con el resultado de su verificación.": "Registros, campos e regras da especificação ativa, com o resultado da sua verificação.",
    "El archivo se valida con código contra la especificación; no se usa IA.": "O arquivo é validado em código contra a especificação; não se usa IA.",
    "El archivo se arma con código a partir de la especificación y se controla antes de descargarlo.": "O arquivo é montado em código a partir da especificação e é conferido antes do download.",
    "El cruce, los importes y las diferencias los calcula el código.": "O cruzamento, os valores e as diferenças são calculados pelo código.",
    "Solo si hace falta: propone la llave o la configuración y se valida antes de usarla.": "Só quando necessário: propõe a chave ou a configuração, que é validada antes de ser usada.",
    "Coinciden en todos los grupos": "Coincidem em todos os grupos",
    "En todos, con diferencias de importe": "Em todos, com diferenças de valor",
    "{0} llaves en total": "{0} chaves no total",
    "Archivos": "Arquivos",
    "Comparación": "Comparação",
    "Subí tus archivos (CSV, Excel, TXT posicional o XML) y el sistema los cruza. Si un formato es nuevo, lo aprende de su manual técnico: un LLM lo convierte en una especificación formal y un verificador la comprueba.":
        "Envie seus arquivos (CSV, Excel, TXT posicional ou XML) e o sistema os cruza. Se um formato é novo, ele o aprende a partir do manual técnico: um LLM o converte em uma especificação formal e um verificador a confere.",
    "Idioma": "Idioma",
    "Modelo": "Modelo",
    "Proveedor": "Provedor",
    "sin IA (línea base)": "sem IA (linha de base)",
    "(otro)": "(outro)",
    "Nombre del modelo": "Nome do modelo",
    "API key": "Chave de API",
    "(usa la clave configurada)": "(usa a chave configurada)",
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
    "No se guardó en la biblioteca: {0}. El verificador encontró errores; revisalos en «Especificación».": "Não foi salvo na biblioteca: {0}. O verificador encontrou erros; revise-os em «Especificação».",
    "Guardar igual en la biblioteca": "Salvar mesmo assim na biblioteca",
    "Guardada en la biblioteca como '{0}'.": "Salva na biblioteca como '{0}'.",
    "Tiene {0} errores del verificador: revisá que lea bien el archivo antes de usarla.": "Tem {0} erros do verificador: confira se lê bem o arquivo antes de usá-la.",
    "{0} formatos ({1} válidos)": "{0} formatos ({1} válidos)",
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
    # acceso y memoria
    "Entrar": "Entrar",
    "Mail": "E-mail",
    "Contraseña": "Senha",
    "Salir": "Sair",
    "Mail o contraseña incorrectos.": "E-mail ou senha incorretos.",
    "No se pudo entrar": "Não foi possível entrar",
    "Tu usuario no tiene un espacio de trabajo asignado. Pedile acceso a la administradora.":
        "Seu usuário não tem um espaço de trabalho atribuído. Peça acesso à administradora.",
    "No se pudo guardar el formato en la cuenta": "Não foi possível salvar o formato na conta",
    "🧠 **Ya cruzaste archivos con esta misma estructura** ({0} veces, confianza {1}%).":
        "🧠 **Você já cruzou arquivos com esta mesma estrutura** ({0} vezes, confiança {1}%).",
    "Usar la configuración aprendida": "Usar a configuração aprendida",
    "Configurar a mano": "Configurar manualmente",
    "configuración aprendida": "configuração aprendida",
    "No se pudo actualizar la memoria de configuración": "Não foi possível atualizar a memória de configuração",
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
    # memoria con umbral, IA para la llave y métricas
    "**Evolución de la confianza de la memoria**": "**Evolução da confiança da memória**",
    "**Por origen de la configuración**": "**Por origem da configuração**",
    "Aceptación de lo aprendido": "Aceitação do aprendido",
    "Actualizar": "Atualizar",
    "Ahorro de preparación": "Economia de preparação",
    "Aplicarla automáticamente de ahora en más (la confianza supera el {0}%)": "Aplicá-la automaticamente daqui em diante (a confiança supera {0}%)",
    "Con configuración aprendida": "Com configuração aprendida",
    "Con más usos sin cambios, cuando la confianza supere el {0}%, vas a poder aceptar que se aplique sola.":
        "Com mais usos sem mudanças, quando a confiança superar {0}%, você poderá aceitar que ela seja aplicada sozinha.",
    "Consultando a la IA…": "Consultando a IA…",
    "Cruces": "Cruzamentos",
    "Descargar métricas (Excel)": "Baixar métricas (Excel)",
    "La IA no pudo proponer una llave: {0}": "A IA não conseguiu propor uma chave: {0}",
    "No aplicarla más automáticamente": "Não aplicá-la mais automaticamente",
    "No se pudieron leer las métricas": "Não foi possível ler as métricas",
    "No se pudo registrar la métrica": "Não foi possível registrar a métrica",
    "Podés elegirla a mano o pedirle una propuesta a la IA: se le envían solo los nombres de las columnas y 5 valores de ejemplo de cada una, y la propuesta se valida con código (las columnas tienen que existir y sus valores tienen que coincidir entre archivos).":
        "Você pode escolhê-la manualmente ou pedir uma proposta à IA: são enviados apenas os nomes das colunas e 5 valores de exemplo de cada uma, e a proposta é validada por código (as colunas precisam existir e seus valores precisam coincidir entre os arquivos).",
    "Solo para tu usuario. Si la corregís, la confianza baja y vuelve a preguntarte.":
        "Só para o seu usuário. Se você a corrigir, a confiança cai e ela volta a perguntar.",
    "Solo ves los cruces de tu espacio de trabajo.": "Você só vê os cruzamentos do seu espaço de trabalho.",
    "Sugerida por la IA y validada con código.": "Sugerida pela IA e validada por código.",
    "Todavía no hay cruces registrados.": "Ainda não há cruzamentos registrados.",
    "Uso real de la herramienta para el capítulo de resultados: cada «Cruzar» deja un registro con el origen de la configuración, si se aceptó o se corrigió, el tiempo de preparación y el uso de IA.":
        "Uso real da ferramenta para o capítulo de resultados: cada «Cruzar» deixa um registro com a origem da configuração, se foi aceita ou corrigida, o tempo de preparação e o uso de IA.",
    "🔎 **El código no encontró una llave clara** que vincule todos los archivos.":
        "🔎 **O código não encontrou uma chave clara** que vincule todos os arquivos.",
    "🤖 La IA propuso la llave y la validación la confirmó (coincide entre archivos e identifica registros: solidez ≥ 50 %). Revisala abajo. Motivo: {0}":
        "🤖 A IA propôs a chave e a validação a confirmou (coincide entre os arquivos e identifica registros: solidez ≥ 50 %). Revise-a abaixo. Motivo: {0}",
    "🤖 La propuesta de la IA no pasó la validación en todos los archivos; donde no la pasó se mantiene la sugerencia del código. Motivo: {0}":
        "🤖 A proposta da IA não passou na validação em todos os arquivos; onde não passou, mantém-se a sugestão do código. Motivo: {0}",
    "🤖 Pedir la llave a la IA": "🤖 Pedir a chave à IA",
    "🧠 **Se aplicó automáticamente la configuración aprendida** (confianza {0}%, mayor al {1}%, y aceptaste que se aplique sola). Revisala antes de cruzar.":
        "🧠 **A configuração aprendida foi aplicada automaticamente** (confiança {0}%, maior que {1}%, e você aceitou que ela seja aplicada sozinha). Revise-a antes de cruzar.",
    "📊 Métricas": "📊 Métricas", "cruces": "cruzamentos",
    "preparación mediana (s)": "preparação mediana (s)", "corregidas": "corrigidas", "tokens IA": "tokens IA",
    "usuarios": "usuários", "estructuras distintas (firmas)": "estruturas distintas (assinaturas)",
    "cruces con configuración aprendida": "cruzamentos com configuração aprendida",
    "aceptada sin cambios": "aceita sem mudanças", "corregida por el usuario": "corrigida pelo usuário",
    "tasa de aceptación de lo aprendido (%)": "taxa de aceitação do aprendido (%)",
    "aplicadas solas (> 95 % y aceptado)": "aplicadas sozinhas (> 95 % e aceito)",
    "preparación mediana a mano (s)": "preparação mediana manual (s)",
    "preparación mediana reutilizando (s)": "preparação mediana reutilizando (s)",
    "ahorro de tiempo de preparación (%)": "economia de tempo de preparação (%)",
    "cruces que pidieron la llave a la IA": "cruzamentos que pediram a chave à IA",
    "propuesta de la IA validada": "proposta da IA validada", "llamadas a la IA": "chamadas à IA", "tokens de IA": "tokens de IA",
    "cruce mediano (s)": "cruzamento mediano (s)",
    "Armado a mano (sugerencia por código)": "Montado manualmente (sugestão por código)",
    "Llave sugerida por IA (validada)": "Chave sugerida pela IA (validada)",
    "Configuración aprendida (ofrecida)": "Configuração aprendida (oferecida)",
    "Configuración aprendida (aplicada sola, > 95 %)": "Configuração aprendida (aplicada sozinha, > 95 %)",
    "Cruce guardado reutilizado": "Cruzamento salvo reutilizado",
    "🧹 Nueva conciliación": "🧹 Nova conciliação",
    "Quita los archivos, el resultado y la configuración aplicada para empezar otra conciliación.":
        "Remove os arquivos, o resultado e a configuração aplicada para começar outra conciliação.",
    # agrupar y filtrar por total
    "**Agrupar**": "**Agrupar**", "**Columna calculada (por fila)**": "**Coluna calculada (por linha)**",
    "**Condición sobre el total del grupo**": "**Condição sobre o total do grupo**",
    "**Resultado de la agrupación**": "**Resultado do agrupamento**", "Agrupar por": "Agrupar por",
    "Agrupar y filtrar por total": "Agrupar e filtrar por total",
    "Calcula una columna con una fórmula, la agrupa (por ejemplo, por documento y mes) y excluye los grupos cuyo total no cumple un umbral, que puede depender de una columna.":
        "Calcula uma coluna com uma fórmula, agrupa-a (por exemplo, por documento e mês) e exclui os grupos cujo total não cumpre um limite, que pode depender de uma coluna.",
    "Columna que define el umbral (por ejemplo, el tipo de documento)": "Coluna que define o limite (por exemplo, o tipo de documento)",
    "Columnas que se agregan al resultado": "Colunas que são adicionadas ao resultado", "Condición": "Condição",
    "El total tiene que ser": "O total tem que ser", "el total tiene que ser": "o total tem que ser",
    "Elegí al menos un valor y su umbral.": "Escolha pelo menos um valor e seu limite.",
    "Filas con otros valores": "Linhas com outros valores",
    "Filas excluidas: {0} (filtros y agrupaciones, con el motivo)": "Linhas excluídas: {0} (filtros e agrupamentos, com o motivo)",
    "Función": "Função", "Fórmula: {0}": "Fórmula: {0}", "Hojas del Excel": "Planilhas do Excel",
    "La llave del cruce ({0}) no está entre las columnas del grupo: después de resumir, cada grupo lleva un solo valor de esa columna. Agregala a «Agrupar por» o cambiá la llave.":
        "A chave do cruzamento ({0}) não está entre as colunas do grupo: depois de resumir, cada grupo leva um único valor dessa coluna. Adicione-a em «Agrupar por» ou mude a chave.",
    "Las demás columnas se llevan con": "As demais colunas são levadas com", "Operación": "Operação",
    "Primera columna": "Primeira coluna",
    "Se guardan con el cruce. Si no elegís ninguna, el Excel trae solo el resumen.":
        "São salvas com o cruzamento. Se você não escolher nenhuma, o Excel traz só o resumo.",
    "Se ven en el detalle de cada grupo y en el Excel; también se pueden elegir como importe a comparar.":
        "Aparecem no detalhe de cada grupo e no Excel; também podem ser escolhidas como valor a comparar.",
    "Si la columna vale": "Se a coluna vale", "Si la columna vale (separá con «;»)": "Se a coluna vale (separe com «;»)",
    "Totales por grupo: {0} grupos, {1} cumplen la condición": "Totais por grupo: {0} grupos, {1} cumprem a condição",
    "Umbral": "Limite", "en valor absoluto": "em valor absoluto", "pasan sin umbral": "passam sem limite",
    "se excluyen": "são excluídas", "todas": "todas", "➕ Otra columna": "➕ Outra coluna", "➕ Otro umbral": "➕ Outro limite",
    "➖ Quitar la última": "➖ Remover a última",
    "suma": "soma", "resta": "subtrai", "multiplica por": "multiplica por", "divide por": "divide por",
    "Suma": "Soma", "Cantidad de filas": "Quantidade de linhas", "Máximo": "Máximo", "Mínimo": "Mínimo", "Promedio": "Média",
    "mayor que": "maior que", "mayor o igual a": "maior ou igual a", "menor que": "menor que", "menor o igual a": "menor ou igual a",
    "Sin condición (solo agrupar)": "Sem condição (só agrupar)", "Un umbral para todas las filas": "Um limite para todas as linhas",
    "Umbral según el valor de una columna": "Limite conforme o valor de uma coluna",
    "Mantener las filas (se excluyen las de los grupos que no cumplen)": "Manter as linhas (excluem-se as dos grupos que não cumprem)",
    "Resumir: una fila por grupo": "Resumir: uma linha por grupo", "primer valor": "primeiro valor", "último valor": "último valor",
    "cantidad de valores distintos": "quantidade de valores distintos",
    "Resumen y estadísticas por grupo": "Resumo e estatísticas por grupo", "Cruce por llave": "Cruzamento por chave",
    "Diferencias": "Diferenças", "Detalle de cada grupo": "Detalhe de cada grupo", "Excluidos (con el motivo)": "Excluídos (com o motivo)",
    "Totales por grupo (agregaciones)": "Totais por grupo (agregações)",
    "valor calculado": "valor calculado", "total del grupo": "total do grupo", "umbral aplicado": "limite aplicado",
    "categoría": "categoria", "cumple": "cumpre", "motivo": "motivo",
    "filas del grupo": "linhas do grupo",
    "el total del grupo no cumple el umbral": "o total do grupo não cumpre o limite",
    "categoría sin umbral definido": "categoria sem limite definido", "división por cero": "divisão por zero",
    "filas excluidas (filtros y agregaciones)": "linhas excluídas (filtros e agregações)",
    # referencias encadenadas y controles entre columnas
    "Buscar por (columnas de la base)": "Buscar por (colunas da base)", "La llave de la base": "A chave da base",
    "Compara dos columnas de cada fila del grupo, por ejemplo la alícuota informada contra la que corresponde según el padrón. Las filas que no coinciden van a la hoja «Controles» del Excel.":
        "Compara duas colunas de cada linha do grupo, por exemplo a alíquota informada contra a que corresponde segundo o cadastro. As linhas que não coincidem vão para a planilha «Controles» do Excel.",
    "Tolerancia": "Tolerância",
    "Vacío: se busca por la llave de la base. Elegí otra columna para encadenar búsquedas, por ejemplo la letra que trajo el padrón contra una tabla de equivalencias.":
        "Vazio: busca-se pela chave da base. Escolha outra coluna para encadear buscas, por exemplo a letra que veio do cadastro contra uma tabela de equivalências.",
    "tiene que coincidir con": "tem que coincidir com",
    "«Buscar por» tiene que tener tantas columnas como la llave de este archivo ({0}).":
        "«Buscar por» tem que ter tantas colunas quanto a chave deste arquivo ({0}).",
    "✓ Controles entre columnas ({0})": "✓ Controles entre colunas ({0})", "➕ Agregar control": "➕ Adicionar controle",
    "Controles (filas que no coinciden)": "Controles (linhas que não coincidem)", "coincide": "coincide",
    "distinta": "diferente", "sin dato": "sem dado", "filas que no pasan los controles": "linhas que não passam nos controles",
    "Cómo se agrupa por llave": "Como se agrupa por chave", "Sumar por llave (importes)": "Somar por chave (valores)",
    "Valor único por llave (alícuotas, tasas, códigos: no se suman)": "Valor único por chave (alíquotas, taxas, códigos: não se somam)",
    "Los importes se suman por llave. Una alícuota o una tasa no se suma: se compara su valor, y si una llave tiene varios valores distintos se marca como diferencia.":
        "Os valores se somam por chave. Uma alíquota ou uma taxa não se soma: compara-se o seu valor, e se uma chave tem vários valores diferentes, marca-se como diferença.",
    "La referencia «{0}» de «{1}» encontró solo {2}% de las filas. Revisá su llave, la normalización y «Buscar por» (por ejemplo, una tabla de equivalencias se busca por la letra, no por el identificador).":
        "A referência «{0}» de «{1}» encontrou só {2}% das linhas. Revise a sua chave, a normalização e «Buscar por» (por exemplo, uma tabela de equivalências se busca pela letra, não pelo identificador).",
    "Las llaves de los grupos casi no coinciden ({0} en común). Revisá que todos usen la misma columna (por ejemplo, el documento y no la cuenta) y la misma normalización.":
        "As chaves dos grupos quase não coincidem ({0} em comum). Revise se todos usam a mesma coluna (por exemplo, o documento e não a conta) e a mesma normalização.",
    "Esta condición no tiene valores: no se aplica.": "Esta condição não tem valores: não é aplicada.",
    "suma (columnas numéricas) y primer valor (texto)": "soma (colunas numéricas) e primeiro valor (texto)",
    "✍️ Describir el cruce con texto (asistido por IA)": "✍️ Descrever o cruzamento com texto (assistido por IA)",
    "Escribí qué querés cruzar con tus palabras, por ejemplo: «cruzá por CUIT, quedate solo con las leyendas SIRTAC y 680/2020, compará el monto retenido con tolerancia de 1 centavo». La IA propone la configuración, el código la valida contra los datos y se carga abajo para que la revises antes de cruzar. A la IA se le envían los nombres de las columnas y unos pocos valores de ejemplo, no los archivos.":
        "Escreva com as suas palavras o que quer cruzar, por exemplo: «cruze por CUIT, fique só com as legendas SIRTAC e 680/2020, compare o valor retido com tolerância de 1 centavo». A IA propõe a configuração, o código a valida contra os dados e ela é carregada abaixo para você revisar antes de cruzar. À IA são enviados os nomes das colunas e poucos valores de exemplo, não os arquivos.",
    "Instrucción": "Instrução", "¿Qué querés cruzar y cómo?": "O que você quer cruzar e como?",
    "Proponer la configuración": "Propor a configuração",
    "Requiere la clave de la IA (Configuración avanzada).": "Requer a chave da IA (Configuração avançada).",
    "La IA no pudo proponer una configuración: {0}": "A IA não conseguiu propor uma configuração: {0}",
    "configuración desde texto": "configuração a partir de texto",
    "Configuración cargada abajo: revisala antes de cruzar (llaves, filtros, importes y tolerancia).":
        "Configuração carregada abaixo: revise-a antes de cruzar (chaves, filtros, valores e tolerância).",
    "La IA no propuso una llave válida para todos los archivos: no se cargó la configuración.":
        "A IA não propôs uma chave válida para todos os arquivos: a configuração não foi carregada.",
    "Validación: {0} referencias inexistentes descartadas, {1} correcciones aplicadas.":
        "Validação: {0} referências inexistentes descartadas, {1} correções aplicadas.",
    "🤖 Comparar la llave del código con la de la IA": "🤖 Comparar a chave do código com a da IA",
    "El código ya encontró una llave clara. Si querés, podés pedirle igual una propuesta a la IA para compararlas: se valida con el mismo criterio y, si la acepta, reemplaza a la del código.":
        "O código já encontrou uma chave clara. Se quiser, pode pedir mesmo assim uma proposta à IA para compará-las: ela é validada com o mesmo critério e, se for aceita, substitui a do código.",
    "Configuración desde texto (IA, validada)": "Configuração a partir de texto (IA, validada)",
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
