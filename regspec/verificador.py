"""Verificador formal de especificaciones.

Recibe la especificación producida por el extractor (dict o modelo) y devuelve
una lista de problemas accionables. Cada problema indica dónde está, qué está
mal y cómo corregirlo, para poder devolverlo al LLM en el bucle de
autocorrección.

Etapas:
  0. Esquema       - la estructura respeta el DSL (pydantic).
  1. Estructura    - códigos únicos, ocurrencias, identificador de registro.
  2. Campos        - tipos coherentes, máscaras de fecha, constantes, dominios.
  3. Layout        - inicio/fin/longitud consistentes, sin solapamientos ni huecos,
                     longitud total igual a la del registro.
  4. Reglas        - sintaxis, funciones, referencias y tipos (análisis estático).
  5. Evidencia     - cada campo y regla cita un fragmento literal del manual.
  6. Ejecutable    - se genera un archivo sintético que cumple todo, se escribe,
                     se relee y se valida (ida y vuelta); en XML además se
                     valida contra el XSD generado.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Optional, Union

from pydantic import ValidationError

from .archivos import escribir, generar_xsd, leer, validar_xsd
from .dsl import NCNAME, NOMBRE_VALIDO, Especificacion, a_snake, mascara_a_strftime
from .expr import ErrorExpresion, verificar_regla
from .sintetico import generar_registros
from .validador import validar
from .valores import ErrorValor


@dataclass
class Problema:
    severidad: str  # "error" | "advertencia"
    codigo: str
    ruta: str
    mensaje: str
    sugerencia: str = ""
    etapa: str = ""

    def a_texto(self) -> str:
        s = f"[{self.codigo}] {self.ruta}: {self.mensaje}"
        return s + (f" -> {self.sugerencia}" if self.sugerencia else "")


@dataclass
class ResultadoVerificacion:
    spec: Optional[Especificacion]
    problemas: list[Problema]

    @property
    def errores(self) -> list[Problema]:
        return [p for p in self.problemas if p.severidad == "error"]

    @property
    def valida(self) -> bool:
        return self.spec is not None and not self.errores

    def por_etapa(self) -> dict[str, int]:
        d: dict[str, int] = {}
        for p in self.errores:
            d[p.etapa] = d.get(p.etapa, 0) + 1
        return d

    def a_dicts(self) -> list[dict]:
        return [asdict(p) for p in self.problemas]


# ---------------------------------------------------------------- evidencia


_MASCARA = re.compile(r"(?<![A-Za-z])((?:AAAA|YYYY|DD|MM)(?:[-/.]?(?:AAAA|YYYY|DD|MM)){1,2})(?![A-Za-z])")


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKC", texto).lower()
    t = re.sub(r"[|*_`#>]", " ", t)
    t = t.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", t).strip()


def _celdas_en_una_linea(evidencia: str, lineas_norm: list[str]) -> bool:
    """Fila de tabla citada con alguna celda omitida: todas las celdas citadas están, en orden, en una misma línea."""
    celdas = [normalizar(c) for c in evidencia.split("|") if normalizar(c)]
    if len(celdas) < 2:
        return False
    for linea in lineas_norm:
        pos = 0
        for c in celdas:
            i = linea.find(c, pos)
            if i < 0:
                break
            pos = i + len(c)
        else:
            return True
    return False


def fragmento_mas_parecido(evidencia: str, manual: str) -> str:
    """Frase o fila del manual más parecida a la evidencia citada (para sugerirla en la corrección)."""
    import difflib

    e = normalizar(evidencia)
    if not e:
        return ""
    candidatos = []
    for linea in manual.splitlines():
        for frase in re.split(r"(?<=[.;:])\s+", linea.strip()):
            if len(frase) > 8:
                candidatos.append(frase)
    mejor = max(candidatos, key=lambda f: difflib.SequenceMatcher(None, normalizar(f), e).ratio(), default="")
    return mejor.strip()


def evidencia_en_manual(evidencia: str, manual_norm: str, umbral: float = 0.85, lineas_norm: Optional[list[str]] = None) -> bool:
    e = normalizar(evidencia)
    if len(e) < 3:
        return False
    if e in manual_norm:
        return True
    if lineas_norm and "|" in evidencia and _celdas_en_una_linea(evidencia, lineas_norm):
        return True
    m = SequenceMatcher(None, manual_norm, e, autojunk=False).find_longest_match(0, len(manual_norm), 0, len(e))
    if m.size / len(e) >= umbral:
        return True
    # coincidencia aproximada por ventana alrededor del mejor bloque
    ini = max(0, m.a - m.b - 10)
    ventana = manual_norm[ini : ini + len(e) + 20]
    return SequenceMatcher(None, ventana, e, autojunk=False).ratio() >= umbral


# ---------------------------------------------------------------- verificación


def _sugerencia_esquema(d: dict, loc: tuple) -> str:
    """Sugerencias deterministas para errores de forma frecuentes (sin IA)."""
    base = "Respetá exactamente los nombres y tipos del esquema JSON."
    try:
        if len(loc) == 5 and loc[0] == "tipos_registro" and loc[2] == "campos" and loc[4] in ("inicio", "longitud"):
            campos = d["tipos_registro"][loc[1]]["campos"]
            j = loc[3]
            c = campos[j]
            if loc[4] == "inicio" and j > 0:
                prev = campos[j - 1]
                if isinstance(prev.get("inicio"), int) and isinstance(prev.get("longitud"), int):
                    sig = prev["inicio"] + prev["longitud"]
                    lr = d.get("longitud_registro")
                    if isinstance(lr, int) and sig > lr:
                        return (f"El campo anterior ya termina en la posición {sig - 1} = longitud del registro: este campo "
                                "no existe en el manual. Eliminalo con \"eliminar_campos\": [\"<nombre>\"].")
                    return f"Debe ser un número entero; según el campo anterior, inicio = {sig}."
            if loc[4] == "inicio" and j == 0:
                return "Debe ser un número entero; el primer campo empieza en 1."
            if loc[4] == "longitud" and isinstance(c.get("inicio"), int) and isinstance(c.get("fin"), int):
                return f"Debe ser un número entero; con inicio {c['inicio']} y fin {c['fin']}, longitud = {c['fin'] - c['inicio'] + 1}."
            return "Debe ser un número entero tomado del manual."
        if loc and loc[-1] == "version":
            return 'La versión es un texto; si el manual no la indica, usá "1.0".'
    except (KeyError, IndexError, TypeError):
        pass
    return base


def _ruta_pydantic(loc: tuple) -> str:
    out = ""
    for x in loc:
        out += f"[{x}]" if isinstance(x, int) else (f".{x}" if out else str(x))
    return out


def verificar(
    spec_in: Union[dict, Especificacion],
    manual: Optional[str] = None,
    chequear_evidencia: bool = True,
    chequear_ejecucion: bool = True,
    semillas: tuple[int, ...] = (0, 1, 2),
) -> ResultadoVerificacion:
    P: list[Problema] = []

    def err(codigo, ruta, msg, sug="", etapa="", sev="error"):
        P.append(Problema(sev, codigo, ruta, msg, sug, etapa))

    # 0. esquema
    if isinstance(spec_in, Especificacion):
        spec = spec_in
    else:
        try:
            spec = Especificacion.model_validate(spec_in)
        except ValidationError as e:
            for x in e.errors()[:40]:
                err("ESQUEMA", _ruta_pydantic(x["loc"]), x["msg"], _sugerencia_esquema(spec_in, x["loc"]), "esquema")
            return ResultadoVerificacion(None, P)

    ancho_fijo = spec.formato == "ancho_fijo"

    # 1. estructura
    if not spec.tipos_registro:
        err("SIN_REGISTROS", "tipos_registro", "no se definió ningún tipo de registro", etapa="estructura")
    if ancho_fijo and not spec.longitud_registro:
        err("SIN_LONGITUD", "longitud_registro", "en ancho fijo hay que indicar la longitud de registro",
            "Buscá en el manual la longitud total de cada línea.", "estructura")
    if not ancho_fijo and not spec.etiqueta_raiz_xml:
        err("SIN_RAIZ", "etiqueta_raiz_xml", "en XML hay que indicar el elemento raíz", etapa="estructura")
    vistos: dict[str, int] = {}
    for i, tr in enumerate(spec.tipos_registro):
        r = f"tipos_registro[{i}] ({tr.codigo})"
        if tr.codigo in vistos:
            err("CODIGO_DUPLICADO", r, f"el código '{tr.codigo}' ya se usó en tipos_registro[{vistos[tr.codigo]}]", etapa="estructura")
        vistos[tr.codigo] = i
        if tr.max_ocurrencias is not None and tr.max_ocurrencias < tr.min_ocurrencias:
            err("OCURRENCIAS", r, f"max_ocurrencias ({tr.max_ocurrencias}) < min_ocurrencias ({tr.min_ocurrencias})", etapa="estructura")
        if not ancho_fijo and not NCNAME.match(tr.etiqueta_xml or ""):
            err("ETIQUETA_XML", r, f"etiqueta_xml inválida o ausente: {tr.etiqueta_xml!r}", "Usá el nombre exacto del elemento XML del manual.", "estructura")
    for pos in ("primero", "ultimo"):
        cods = [t.codigo for t in spec.tipos_registro if t.posicion == pos]
        if len(cods) > 1:
            err("POSICION", "tipos_registro", f"más de un registro marcado como '{pos}': {cods}", etapa="estructura")

    if ancho_fijo:
        ids = []
        for i, tr in enumerate(spec.tipos_registro):
            c = spec.campo_identificador(tr)
            if c is None:
                err("SIN_IDENTIFICADOR", f"tipos_registro[{i}] ({tr.codigo})",
                    f"ningún campo constante tiene valor_constante igual al código '{tr.codigo}'",
                    "El campo 'tipo de registro' debe ser tipo constante con valor_constante = codigo del registro.", "estructura")
            else:
                ids.append((tr.codigo, c.inicio, c.longitud))
        if len({(a, b) for _, a, b in ids}) > 1:
            err("IDENTIFICADOR_INCONSISTENTE", "tipos_registro",
                f"el campo identificador no está en la misma posición en todos los registros: {ids}", etapa="estructura")

    # 2. campos y 3. layout
    for i, tr in enumerate(spec.tipos_registro):
        nombres: dict[str, int] = {}
        for j, c in enumerate(tr.campos):
            r = f"tipos_registro[{i}] ({tr.codigo}).campos[{j}] ({c.nombre})"
            if not NOMBRE_VALIDO.match(c.nombre):
                err("NOMBRE_INVALIDO", r, f"'{c.nombre}' no es un identificador snake_case", f"Usá '{a_snake(c.nombre)}'.", "campos")
            if c.nombre in nombres:
                err("NOMBRE_DUPLICADO", r, f"el nombre '{c.nombre}' se repite en el registro", "Agregá un sufijo que lo distinga.", "campos")
            nombres[c.nombre] = j
            if c.longitud is None:
                err("SIN_LONGITUD", r, "falta la longitud del campo", "Indicá la cantidad de posiciones que ocupa el campo según el manual.", "campos")
                continue
            if c.longitud < 1:
                err("LONGITUD", r, "la longitud debe ser >= 1", etapa="campos")
            if c.tipo == "decimal" and not (0 < c.decimales < c.longitud):
                err("DECIMALES", r, f"tipo decimal con decimales={c.decimales} y longitud={c.longitud}",
                    "decimales debe ser > 0 y menor que la longitud total (que incluye los decimales).", "campos")
            if c.tipo != "decimal" and c.decimales:
                err("DECIMALES", r, f"decimales={c.decimales} en un campo {c.tipo}", "Si el campo tiene decimales implícitos su tipo es 'decimal'.", "campos")
            if c.tipo == "fecha":
                try:
                    mascara_a_strftime(c.formato_fecha or "")
                    if ancho_fijo and len(c.formato_fecha) != c.longitud:
                        err("FECHA_LONGITUD", r, f"la máscara '{c.formato_fecha}' tiene {len(c.formato_fecha)} caracteres y el campo {c.longitud}", etapa="campos")
                except ValueError as e:
                    err("FECHA_FORMATO", r, str(e), "Ejemplos válidos: AAAAMMDD, AAAA-MM-DD, DDMMAAAA, AAAAMM.", "campos")
            elif c.formato_fecha:
                err("FECHA_FORMATO", r, f"formato_fecha en un campo {c.tipo}", "Si el campo es una fecha, su tipo debe ser 'fecha'.", "campos", "advertencia")
            if c.tipo == "constante":
                if c.valor_constante is None:
                    err("CONSTANTE", r, "campo constante sin valor_constante", etapa="campos")
                elif len(c.valor_constante) > c.longitud:
                    err("CONSTANTE", r, f"valor_constante '{c.valor_constante}' más largo que la longitud {c.longitud}", etapa="campos")
            elif c.valor_constante is not None:
                err("CONSTANTE", r, f"valor_constante en un campo {c.tipo}", "Si el valor es fijo, el tipo debe ser 'constante'.", "campos")
            for v in c.valores_permitidos or []:
                if len(v) > c.longitud:
                    err("DOMINIO", r, f"el valor permitido '{v}' supera la longitud {c.longitud}", etapa="campos")
                if c.tipo in ("numerico", "decimal") and not v.strip().isdigit():
                    err("DOMINIO", r, f"el valor permitido '{v}' no es numérico y el campo es {c.tipo}", "Si los códigos tienen letras, el campo es alfanumérico.", "campos")
            if c.patron:
                try:
                    re.compile(c.patron)
                except re.error as e:
                    err("PATRON", r, f"regex inválida: {e}", etapa="campos")
            if ancho_fijo:
                if c.inicio is None:
                    err("SIN_INICIO", r, "falta la posición inicial", etapa="layout")
                elif c.inicio < 1:
                    err("INICIO", r, "inicio debe ser >= 1 (posiciones 1-based)", etapa="layout")
                elif c.fin is not None and c.fin != c.fin_calculado:
                    err("INCONSISTENCIA_FIN", r,
                        f"inicio={c.inicio} + longitud={c.longitud} - 1 = {c.fin_calculado}, pero fin={c.fin}",
                        "Releé la fila del manual: alguno de los tres valores está mal transcripto.", "layout")
            else:
                if not NCNAME.match(c.etiqueta_xml or ""):
                    err("ETIQUETA_XML", r, f"etiqueta_xml inválida o ausente: {c.etiqueta_xml!r}", etapa="campos")

        if ancho_fijo and spec.longitud_registro:
            con_pos = sorted([c for c in tr.campos if c.inicio and c.longitud], key=lambda c: c.inicio)
            esperado = 1
            for c in con_pos:
                r = f"tipos_registro[{i}] ({tr.codigo}).campo {c.nombre}"
                if c.inicio > esperado:
                    err("HUECO", r, f"las posiciones {esperado}-{c.inicio - 1} no pertenecen a ningún campo",
                        "Probablemente falta un campo (a menudo un 'relleno'/'filler') o un inicio está mal.", "layout")
                elif c.inicio < esperado:
                    err("SOLAPAMIENTO", r, f"empieza en {c.inicio} pero el campo anterior termina en {esperado - 1}",
                        "Revisá inicio y longitud de este campo y del anterior.", "layout")
                esperado = max(esperado, c.inicio + c.longitud)
            if con_pos and esperado - 1 != spec.longitud_registro:
                err("LONGITUD_TOTAL", f"tipos_registro[{i}] ({tr.codigo})",
                    f"los campos cubren hasta la posición {esperado - 1} y el registro mide {spec.longitud_registro}",
                    "Falta o sobra un campo, o la longitud_registro está mal.", "layout")

    # 4. reglas
    ids_r: set[str] = set()
    for k, rg in enumerate(spec.reglas):
        r = f"reglas[{k}] ({rg.id})"
        if rg.id in ids_r:
            err("REGLA_DUPLICADA", r, f"id de regla repetido '{rg.id}'", etapa="reglas")
        ids_r.add(rg.id)
        if rg.ambito == "registro" and (not rg.tipo_registro or spec.tipo(rg.tipo_registro) is None):
            err("REGLA_AMBITO", r, f"tipo_registro '{rg.tipo_registro}' inexistente para una regla de ámbito registro",
                f"Códigos disponibles: {[t.codigo for t in spec.tipos_registro]}", "reglas")
            continue
        try:
            verificar_regla(spec, rg.expresion, rg.tipo_registro if rg.ambito == "registro" else None)
        except ErrorExpresion as e:
            sug = e.sugerencia
            if "sintaxis" in e.mensaje and ("&&" in rg.expresion or "||" in rg.expresion):
                sug = "Usá and / or en lugar de && / ||. " + sug
            err("REGLA_INVALIDA", r, f"'{rg.expresion}': {e.mensaje}", sug, "reglas")

    # 5. evidencia
    if manual is not None and chequear_evidencia:
        mn = normalizar(manual)
        lineas = [normalizar(l) for l in manual.splitlines() if l.strip()]
        for i, tr in enumerate(spec.tipos_registro):
            for j, c in enumerate(tr.campos):
                if not evidencia_en_manual(c.evidencia, mn, lineas_norm=lineas):
                    err("EVIDENCIA", f"tipos_registro[{i}] ({tr.codigo}).campos[{j}] ({c.nombre})",
                        "la evidencia está vacía o no aparece en el manual" + (f": '{c.evidencia[:80]}'" if c.evidencia else ""),
                        _sug_evidencia(c.evidencia, manual, "el fragmento del manual que define este campo; si no existe en el manual, el campo no debe inventarse."),
                        "evidencia")
                # anclaje de atributos: lo que la evidencia dice explícitamente debe coincidir con la especificación
                ruta_c = f"tipos_registro[{i}] ({tr.codigo}).campos[{j}] ({c.nombre})"
                mascaras = {m.upper().replace("YYYY", "AAAA") for m in _MASCARA.findall(c.evidencia or "")}
                if c.tipo == "fecha" and mascaras and (c.formato_fecha or "").upper().replace("YYYY", "AAAA") not in mascaras:
                    err("ATRIBUTO_NO_RESPALDADO", ruta_c,
                        f"formato_fecha '{c.formato_fecha}' no coincide con la máscara citada en la evidencia ({', '.join(sorted(mascaras))})",
                        "Usá exactamente la máscara que indica el manual.", "evidencia")
                for v in c.valores_permitidos or []:
                    if not re.search(r"(?<![a-z0-9])" + re.escape(normalizar(v)) + r"(?![a-z0-9])", mn):
                        err("ATRIBUTO_NO_RESPALDADO", ruta_c, f"el valor permitido '{v}' no aparece en el manual",
                            "Incluí solo códigos que el manual enumera.", "evidencia")
        # los ejemplos XML del propio manual tienen que ser válidos campo por campo
        if not ancho_fijo:
            for bloque in re.findall(r"```xml\s*(.*?)```", manual, re.S):
                leidos, _ = leer(spec, bloque)
                for rl in leidos:
                    tr = spec.tipo(rl.codigo)
                    i = spec.tipos_registro.index(tr)
                    for campo, msg in rl.errores:
                        if campo is None:
                            continue
                        j = next(k for k, c in enumerate(tr.campos) if c.nombre == campo)
                        err("EJEMPLO_DEL_MANUAL", f"tipos_registro[{i}] ({tr.codigo}).campos[{j}] ({campo})",
                            f"el ejemplo XML del manual no es válido con esta definición: {msg}",
                            "Revisá tipo, formato, longitud u obligatoriedad del campo para que el ejemplo oficial sea válido.", "evidencia")
        for k, rg in enumerate(spec.reglas):
            if not evidencia_en_manual(rg.evidencia, mn):
                err("EVIDENCIA", f"reglas[{k}] ({rg.id})", "la evidencia está vacía o no aparece en el manual",
                    _sug_evidencia(rg.evidencia, manual, "la frase del manual que establece la regla; si no existe, eliminá la regla."), "evidencia")

    # 6. ejecutable (solo si no hay errores estructurales)
    if chequear_ejecucion and not [p for p in P if p.severidad == "error" and p.etapa != "evidencia"]:
        _chequear_ejecucion(spec, semillas, err)

    return ResultadoVerificacion(spec, P)


def _sug_evidencia(evidencia: str, manual: str, que: str) -> str:
    base = "Copiá literalmente " + que
    frag = fragmento_mas_parecido(evidencia, manual) if evidencia else ""
    return base + (f" Fragmento más parecido del manual: \"{frag[:240]}\"" if frag else "")


def _chequear_ejecucion(spec: Especificacion, semillas, err) -> None:
    for s in semillas:
        registros, pendientes = generar_registros(spec, semilla=s)
        try:
            contenido = escribir(spec, registros)
        except ErrorValor as e:
            if pendientes:
                err("REGLAS_INSATISFACIBLES", f"reglas {', '.join(pendientes)}",
                    f"no fue posible generar datos que cumplan estas reglas ({e})",
                    "Revisá si la regla contradice el tipo/longitud/dominio de los campos o si la expresión está invertida.", "ejecucion")
            else:
                m = re.search(r"registro \d+ \(([^)]+)\): .*?'([a-z0-9_]+)'", str(e))
                ruta = f"registro {m.group(1)}.{m.group(2)}" if m else "especificacion"
                sug = ""
                if "obligatorio" in str(e) and m:
                    tr = spec.tipo(m.group(1))
                    campo = m.group(2)
                    exige_vacio = [r.id for r in spec.reglas if r.tipo_registro == m.group(1) and f"vacio({campo})" in r.expresion.replace(" ", "")]
                    if exige_vacio:
                        sug = (f"El campo '{campo}' está marcado como obligatorio pero la(s) regla(s) {', '.join(exige_vacio)} "
                               "exigen que quede vacío en algunos casos. Si el manual permite dejarlo en blanco, poné obligatorio: false.")
                err("NO_EJECUTABLE", ruta, f"no se pudo escribir un archivo de prueba: {e}", sug, etapa="ejecucion")
            return
        leidos, estructura = leer(spec, contenido)
        if estructura:
            err("IDA_Y_VUELTA", "especificacion", f"el archivo generado no se puede releer: {estructura[:3]}", etapa="ejecucion")
            return
        for (cod, vals), rl in zip(registros, leidos):
            tr = spec.tipo(cod)
            for c in tr.campos:
                a, b = vals.get(c.nombre), rl.valores.get(c.nombre)
                if c.tipo == "constante":
                    continue
                if _distinto(a, b):
                    motivo = next((m for campo, m in rl.errores if campo == c.nombre), "")
                    err("IDA_Y_VUELTA", f"registro {cod}.{c.nombre}",
                        f"se escribió {a} y al releerlo falló" + (f": {motivo}" if motivo else f" (se leyó {b!r})"),
                        "Revisá que tipo, patrón, formato y longitud del campo sean coherentes entre sí.", etapa="ejecucion")
                    return
        if spec.formato == "xml":
            errs = validar_xsd(generar_xsd(spec), contenido)
            if errs:
                err("XSD", "especificacion", f"el XML generado no valida contra su XSD: {errs[0][1]}", etapa="ejecucion")
                return
        inf = validar(spec, contenido)
        if not inf.ok:
            reglas = sorted(inf.reglas_violadas() | set(pendientes))
            if reglas:
                ruta = ", ".join(reglas)
                err("REGLAS_INSATISFACIBLES", f"reglas {ruta}",
                    "no fue posible generar un archivo que cumpla estas reglas junto con los campos: " + inf.errores[0].mensaje,
                    "Revisá si la regla contradice el tipo/longitud/dominio de los campos o si la expresión está invertida.", "ejecucion")
            else:
                err("NO_EJECUTABLE", "especificacion", inf.errores[0].mensaje, etapa="ejecucion")
            return


def _distinto(a: Any, b: Any) -> bool:
    if (a is None or a == "") and (b is None or b == ""):
        return False
    if a is None or b is None:
        return True
    from decimal import Decimal

    if isinstance(a, Decimal) or isinstance(b, Decimal):
        try:
            return Decimal(str(a)) != Decimal(str(b))
        except Exception:
            return True
    return str(a).strip() != str(b).strip()
