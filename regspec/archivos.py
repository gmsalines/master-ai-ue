"""Escritura y lectura de archivos (TXT posicional, TXT delimitado y XML) a partir de una especificación."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from lxml import etree

from .dsl import Especificacion, TipoRegistro
from .valores import ErrorValor, a_texto, desde_texto

Registro = tuple[str, dict[str, Any]]  # (codigo, {campo: valor})


@dataclass
class RegistroLeido:
    linea: int
    codigo: Optional[str]
    valores: dict[str, Any] = field(default_factory=dict)
    errores: list[tuple[Optional[str], str]] = field(default_factory=list)  # (campo, mensaje)


# ------------------------------------------------------------------ TXT posicional


def escribir_txt(spec: Especificacion, registros: list[Registro]) -> str:
    lineas = []
    for i, (codigo, valores) in enumerate(registros, 1):
        tr = spec.tipo(codigo)
        if tr is None:
            raise ErrorValor(f"registro {i}: tipo '{codigo}' no definido en la especificación")
        partes = []
        for c in sorted(tr.campos, key=lambda c: c.inicio or 0):
            try:
                partes.append(a_texto(c, valores.get(c.nombre), "ancho_fijo"))
            except ErrorValor as e:
                raise ErrorValor(f"registro {i} ({codigo}): {e}")
        linea = "".join(partes)
        if spec.longitud_registro and len(linea) != spec.longitud_registro:
            raise ErrorValor(f"registro {i} ({codigo}) mide {len(linea)} y debería medir {spec.longitud_registro}")
        lineas.append(linea)
    return spec.eol.join(lineas) + spec.eol


def _identificar(spec: Especificacion, linea: str) -> Optional[TipoRegistro]:
    for tr in spec.tipos_registro:
        c = spec.campo_identificador(tr)
        if c is None or c.inicio is None:
            continue
        if linea[c.inicio - 1 : c.inicio - 1 + c.longitud].strip() == (c.valor_constante or "").strip():
            return tr
    return None


def leer_txt(spec: Especificacion, contenido: str) -> tuple[list[RegistroLeido], list[tuple[int, str]]]:
    """Devuelve (registros, errores_de_estructura[(linea, mensaje)])."""
    registros, estructura = [], []
    lineas = contenido.replace("\r\n", "\n").split("\n")
    if lineas and lineas[-1] == "":
        lineas.pop()
    for n, linea in enumerate(lineas, 1):
        if spec.longitud_registro and len(linea) != spec.longitud_registro:
            estructura.append((n, f"la línea mide {len(linea)} caracteres y debería medir {spec.longitud_registro}"))
            continue
        tr = _identificar(spec, linea)
        if tr is None:
            estructura.append((n, "no se reconoce el tipo de registro"))
            continue
        r = RegistroLeido(linea=n, codigo=tr.codigo)
        for c in tr.campos:
            crudo = linea[c.inicio - 1 : c.inicio - 1 + c.longitud]
            try:
                r.valores[c.nombre] = desde_texto(c, crudo, "ancho_fijo")
            except ErrorValor as e:
                r.valores[c.nombre] = None
                r.errores.append((c.nombre, str(e)))
        registros.append(r)
    return registros, estructura


# ------------------------------------------------------------------ TXT delimitado


def escribir_delimitado(spec: Especificacion, registros: list[Registro]) -> str:
    sep, modo, lineas = spec.separador_campos or ";", spec.modo_valores, []
    for i, (codigo, valores) in enumerate(registros, 1):
        tr = spec.tipo(codigo)
        if tr is None:
            raise ErrorValor(f"registro {i}: tipo '{codigo}' no definido en la especificación")
        partes = []
        for c in tr.campos:
            try:
                partes.append(a_texto(c, valores.get(c.nombre), modo) or "")
            except ErrorValor as e:
                raise ErrorValor(f"registro {i} ({codigo}): {e}")
            if sep in partes[-1]:
                raise ErrorValor(f"registro {i} ({codigo}): el valor de '{c.nombre}' contiene el separador '{sep}'")
        lineas.append(sep.join(partes))
    return spec.eol.join(lineas) + spec.eol


def _identificar_delimitado(spec: Especificacion, partes: list[str]) -> Optional[TipoRegistro]:
    if len(spec.tipos_registro) == 1:
        return spec.tipos_registro[0]
    for tr in spec.tipos_registro:
        c = spec.campo_identificador(tr)
        if c is None:
            continue
        k = tr.campos.index(c)
        if k < len(partes) and partes[k].strip() == (c.valor_constante or "").strip():
            return tr
    return None


def leer_delimitado(spec: Especificacion, contenido: str) -> tuple[list[RegistroLeido], list[tuple[int, str]]]:
    sep, modo = spec.separador_campos or ";", spec.modo_valores
    registros, estructura = [], []
    lineas = contenido.replace("\r\n", "\n").split("\n")
    if lineas and lineas[-1] == "":
        lineas.pop()
    for n, linea in enumerate(lineas, 1):
        partes = linea.split(sep)
        tr = _identificar_delimitado(spec, partes)
        if tr is None:
            estructura.append((n, "no se reconoce el tipo de registro"))
            continue
        if len(partes) != len(tr.campos):
            estructura.append((n, f"la línea tiene {len(partes)} campos y el registro {tr.codigo} define {len(tr.campos)}"))
            continue
        r = RegistroLeido(linea=n, codigo=tr.codigo)
        for c, crudo in zip(tr.campos, partes):
            try:
                r.valores[c.nombre] = desde_texto(c, crudo, modo)
            except ErrorValor as e:
                r.valores[c.nombre] = None
                r.errores.append((c.nombre, str(e)))
        registros.append(r)
    return registros, estructura


# ------------------------------------------------------------------ XML


def _etq(obj) -> str:
    return obj.etiqueta_xml or getattr(obj, "codigo", None) or obj.nombre


def generar_xsd(spec: Especificacion) -> str:
    XS = "http://www.w3.org/2001/XMLSchema"
    q = lambda t: f"{{{XS}}}{t}"  # noqa: E731
    schema = etree.Element(q("schema"), nsmap={"xs": XS}, elementFormDefault="qualified")
    raiz = etree.SubElement(schema, q("element"), name=spec.etiqueta_raiz_xml or "Archivo")
    seq = etree.SubElement(etree.SubElement(raiz, q("complexType")), q("sequence"))
    for tr in spec.tipos_registro:
        maxo = "unbounded" if tr.max_ocurrencias is None else str(tr.max_ocurrencias)
        el = etree.SubElement(seq, q("element"), name=_etq(tr), minOccurs=str(tr.min_ocurrencias), maxOccurs=maxo)
        s2 = etree.SubElement(etree.SubElement(el, q("complexType")), q("sequence"))
        for c in tr.campos:
            ce = etree.SubElement(s2, q("element"), name=_etq(c), minOccurs="1" if c.obligatorio else "0")
            st = etree.SubElement(ce, q("simpleType"))
            if c.tipo == "fecha" and (c.formato_fecha or "").upper() in ("AAAA-MM-DD", "YYYY-MM-DD"):
                etree.SubElement(st, q("restriction"), base="xs:date")
                continue
            if c.tipo == "fecha" and (c.formato_fecha or "").upper() in ("AAAA-MM", "YYYY-MM"):
                etree.SubElement(st, q("restriction"), base="xs:gYearMonth")
                continue
            if c.tipo in ("numerico", "decimal"):
                r = etree.SubElement(st, q("restriction"), base="xs:decimal" if c.tipo == "decimal" else "xs:integer")
                etree.SubElement(r, q("totalDigits"), value=str(c.longitud))
                if c.tipo == "decimal":
                    etree.SubElement(r, q("fractionDigits"), value=str(c.decimales))
            else:
                r = etree.SubElement(st, q("restriction"), base="xs:string")
                if c.tipo == "constante":
                    etree.SubElement(r, q("enumeration"), value=c.valor_constante or "")
                    continue
                etree.SubElement(r, q("maxLength"), value=str(c.longitud))
                if c.tipo == "fecha":
                    continue
            for v in c.valores_permitidos or []:
                etree.SubElement(r, q("enumeration"), value=v)
            if c.patron:
                etree.SubElement(r, q("pattern"), value=c.patron)
    return etree.tostring(schema, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode()


def escribir_xml(spec: Especificacion, registros: list[Registro]) -> str:
    raiz = etree.Element(spec.etiqueta_raiz_xml or "Archivo")
    for i, (codigo, valores) in enumerate(registros, 1):
        tr = spec.tipo(codigo)
        if tr is None:
            raise ErrorValor(f"registro {i}: tipo '{codigo}' no definido en la especificación")
        el = etree.SubElement(raiz, _etq(tr))
        for c in tr.campos:
            try:
                txt = a_texto(c, valores.get(c.nombre), "xml")
            except ErrorValor as e:
                raise ErrorValor(f"registro {i} ({codigo}): {e}")
            if txt is not None:
                etree.SubElement(el, _etq(c)).text = txt
    return etree.tostring(raiz, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode()


def validar_xsd(xsd: str, contenido: str) -> list[tuple[int, str]]:
    esquema = etree.XMLSchema(etree.fromstring(xsd.encode()))
    try:
        doc = etree.fromstring(contenido.encode())
    except etree.XMLSyntaxError as e:
        return [(e.lineno or 0, f"XML mal formado: {e.msg}")]
    if esquema.validate(doc):
        return []
    return [(err.line, err.message) for err in esquema.error_log]


def leer_xml(spec: Especificacion, contenido: str) -> tuple[list[RegistroLeido], list[tuple[int, str]]]:
    estructura: list[tuple[int, str]] = []
    try:
        doc = etree.fromstring(contenido.encode())
    except etree.XMLSyntaxError as e:
        return [], [(e.lineno or 0, f"XML mal formado: {e.msg}")]
    raiz_esperada = spec.etiqueta_raiz_xml or "Archivo"
    if doc.tag != raiz_esperada:
        estructura.append((doc.sourceline or 0, f"el elemento raíz es '{doc.tag}' y debería ser '{raiz_esperada}'"))
    por_etq = {_etq(tr): tr for tr in spec.tipos_registro}
    registros = []
    for el in doc:
        if not isinstance(el.tag, str):
            continue
        tr = por_etq.get(el.tag)
        if tr is None:
            estructura.append((el.sourceline or 0, f"elemento '{el.tag}' no reconocido"))
            continue
        r = RegistroLeido(linea=el.sourceline or 0, codigo=tr.codigo)
        hijos = {h.tag: h for h in el if isinstance(h.tag, str)}
        for c in tr.campos:
            h = hijos.get(_etq(c))
            try:
                r.valores[c.nombre] = desde_texto(c, h.text if h is not None else None, "xml")
            except ErrorValor as e:
                r.valores[c.nombre] = None
                r.errores.append((c.nombre, str(e)))
        conocidos = {_etq(c) for c in tr.campos}
        for etq in hijos:
            if etq not in conocidos:
                r.errores.append((None, f"elemento '{etq}' no definido en '{tr.codigo}'"))
        registros.append(r)
    return registros, estructura


def escribir(spec: Especificacion, registros: list[Registro]) -> str:
    if spec.formato == "delimitado":
        return escribir_delimitado(spec, registros)
    return escribir_txt(spec, registros) if spec.formato == "ancho_fijo" else escribir_xml(spec, registros)


def leer(spec: Especificacion, contenido: str):
    if spec.formato == "delimitado":
        return leer_delimitado(spec, contenido)
    return leer_txt(spec, contenido) if spec.formato == "ancho_fijo" else leer_xml(spec, contenido)
