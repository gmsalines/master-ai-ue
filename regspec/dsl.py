"""Lenguaje de especificación declarativo (DSL) para archivos regulatorios.

Una `Especificacion` describe, de forma ejecutable, el layout de un archivo
(ancho fijo, delimitado o XML), sus tipos de registro, sus campos y las reglas de negocio
que debe cumplir. Es el "objetivo de compilación" del extractor: el LLM lee
un manual en lenguaje natural y produce una instancia de este modelo.

Convenciones:
- Posiciones 1-based e inclusivas (como en los manuales).
- `longitud` de un campo decimal = total de dígitos, incluidos los decimales
  (en ancho fijo los decimales son implícitos, sin separador).
- Numéricos y decimales: alineados a la derecha, rellenos con ceros.
- Alfanuméricos: alineados a la izquierda, rellenos con espacios.
- Delimitado (p. ej. campos separados por ";"): los campos van en el orden de la lista,
  los decimales llevan punto explícito y `longitud` es el máximo de caracteres/dígitos.
  Con `campos_ancho_fijo` cada campo ocupa exactamente su ancho (numéricos con ceros a la
  izquierda; el ancho de un decimal es longitud + 1 por el punto).
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

TipoCampo = Literal["alfanumerico", "numerico", "decimal", "fecha", "constante"]
Formato = Literal["ancho_fijo", "delimitado", "xml"]
Posicion = Literal["primero", "ultimo", "cualquiera"]
Ambito = Literal["registro", "archivo"]


class Campo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(description="Identificador snake_case único dentro del registro. Se usa en las reglas.")
    descripcion: str = Field(default="", description="Nombre o descripción del campo tal como figura en el manual.")
    inicio: Optional[int] = Field(default=None, description="Posición inicial (1-based). Obligatoria en ancho fijo; null en XML.")
    longitud: Optional[int] = Field(default=None, description="Cantidad de caracteres (ancho fijo, obligatoria) o longitud/dígitos máximos (XML, opcional).")
    fin: Optional[int] = Field(default=None, description="Posición final tal como figura en el manual, si figura. Se usa para control cruzado.")
    tipo: TipoCampo
    decimales: int = Field(default=0, description="Cantidad de decimales (solo tipo decimal).")
    formato_fecha: Optional[str] = Field(default=None, description="Máscara de fecha con AAAA, MM, DD y separadores, p. ej. AAAAMMDD o AAAA-MM-DD.")
    obligatorio: bool = True
    valores_permitidos: Optional[list[str]] = Field(default=None, description="Lista cerrada de códigos admitidos, si el manual la define.")
    valor_constante: Optional[str] = Field(default=None, description="Valor fijo (solo tipo constante).")
    patron: Optional[str] = Field(default=None, description="Expresión regular que debe cumplir el valor, si el manual la define.")
    etiqueta_xml: Optional[str] = Field(default=None, description="Nombre del elemento XML (solo formato XML).")
    evidencia: str = Field(default="", description="Fragmento LITERAL del manual que respalda este campo.")

    @property
    def fin_calculado(self) -> Optional[int]:
        if self.inicio is None or self.longitud is None:
            return None
        return self.inicio + self.longitud - 1


class TipoRegistro(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str = Field(description="Código que identifica el tipo de registro (ancho fijo) o nombre del elemento XML.")
    nombre: str
    descripcion: str = ""
    min_ocurrencias: int = 1
    max_ocurrencias: Optional[int] = Field(default=1, description="null = sin límite.")
    posicion: Posicion = Field(default="cualquiera", description="Si el registro debe ser el primero o el último del archivo.")
    etiqueta_xml: Optional[str] = None
    campos: list[Campo]

    def campo(self, nombre: str) -> Optional[Campo]:
        for c in self.campos:
            if c.nombre == nombre:
                return c
        return None


class Regla(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    descripcion: str
    ambito: Ambito
    tipo_registro: Optional[str] = Field(default=None, description="Código del registro al que aplica (obligatorio si ambito = registro).")
    expresion: str = Field(description="Expresión booleana en el lenguaje de reglas.")
    evidencia: str = ""


class Especificacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str
    version: str = "1.0"
    formato: Formato
    longitud_registro: Optional[int] = Field(default=None, description="Longitud fija de cada línea (solo ancho fijo).")
    separador_lineas: Literal["LF", "CRLF"] = "LF"
    separador_campos: Optional[str] = Field(default=None, description="Separador de campos (solo delimitado), p. ej. ';'.")
    campos_ancho_fijo: bool = Field(default=False, description="Delimitado: cada campo ocupa exactamente su ancho (relleno con ceros o espacios).")
    codificacion: str = Field(default="utf-8", description="Codificación del archivo (utf-8, latin-1).")
    etiqueta_raiz_xml: Optional[str] = None
    tipos_registro: list[TipoRegistro]
    reglas: list[Regla] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _completar_xml(cls, d):
        """En XML la longitud y el separador de líneas no siempre están definidos: se completan valores neutros.

        En ancho fijo no se completa nada salvo la longitud cuando el manual da inicio y fin.
        """
        if not isinstance(d, dict):
            return d
        d = _sanear(d)
        xml = d.get("formato") == "xml"
        if xml and d.get("separador_lineas") not in ("LF", "CRLF"):
            d = {**d, "separador_lineas": "LF"}
        trs = d.get("tipos_registro")
        if not isinstance(trs, list):
            return d
        nuevos = []
        for tr in trs:
            if not isinstance(tr, dict) or not isinstance(tr.get("campos"), list):
                nuevos.append(tr)
                continue
            if xml and not tr.get("etiqueta_xml"):
                tr = {**tr, "etiqueta_xml": tr.get("codigo")}
            campos = []
            for c in tr["campos"]:
                if isinstance(c, dict) and c.get("longitud") in (None, ""):
                    c = dict(c)
                    if not xml and isinstance(c.get("inicio"), int) and isinstance(c.get("fin"), int):
                        c["longitud"] = c["fin"] - c["inicio"] + 1
                    elif xml:
                        t = c.get("tipo")
                        if t == "fecha" and c.get("formato_fecha"):
                            c["longitud"] = len(c["formato_fecha"])
                        elif t == "constante" and c.get("valor_constante") is not None:
                            c["longitud"] = len(str(c["valor_constante"]))
                        elif t in ("numerico", "decimal"):
                            c["longitud"] = 18
                        else:
                            c["longitud"] = 255
                campos.append(c)
            nuevos.append({**tr, "campos": campos})
        return {**d, "tipos_registro": nuevos}

    def tipo(self, codigo: str) -> Optional[TipoRegistro]:
        for t in self.tipos_registro:
            if t.codigo == codigo:
                return t
        return None

    def campo_identificador(self, tr: TipoRegistro) -> Optional[Campo]:
        """Campo constante cuyo valor coincide con el código del registro (ancho fijo y delimitado)."""
        for c in tr.campos:
            if c.tipo == "constante" and c.valor_constante is not None and c.valor_constante.strip() == tr.codigo.strip():
                return c
        return None

    @property
    def modo_valores(self) -> str:
        """Cómo se representan los valores: 'ancho_fijo', 'delimitado', 'delimitado_fijo' o 'xml'."""
        if self.formato == "delimitado":
            return "delimitado_fijo" if self.campos_ancho_fijo else "delimitado"
        return self.formato

    @property
    def eol(self) -> str:
        return "\r\n" if self.separador_lineas == "CRLF" else "\n"


# ---------------------------------------------------------------- fechas

_TOKENS = [("AAAA", "%Y"), ("YYYY", "%Y"), ("MM", "%m"), ("DD", "%d")]
_SEPARADORES = set("-/.")


def mascara_a_strftime(mascara: str) -> str:
    """Convierte AAAAMMDD / AAAA-MM-DD / DD/MM/AAAA / AAAAMM a formato strftime.

    Lanza ValueError si la máscara contiene tokens no soportados.
    """
    out, i, vistos = [], 0, set()
    m = mascara.strip().upper()
    while i < len(m):
        for tok, py in _TOKENS:
            if m.startswith(tok, i):
                if py in vistos:
                    raise ValueError(f"token repetido en la máscara '{mascara}'")
                vistos.add(py)
                out.append(py)
                i += len(tok)
                break
        else:
            if m[i] in _SEPARADORES:
                out.append(m[i])
                i += 1
            else:
                raise ValueError(f"carácter o token no soportado '{m[i:]}' en la máscara '{mascara}' (use AAAA, MM, DD y separadores - / .)")
    if "%Y" not in vistos or "%m" not in vistos:
        raise ValueError(f"la máscara '{mascara}' debe contener al menos AAAA y MM")
    return "".join(out)


NOMBRE_VALIDO = re.compile(r"^[a-z][a-z0-9_]*$")
NCNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")


def a_snake(texto: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    if not t or not t[0].isalpha():
        t = "c_" + t
    return t


_ENTEROS_CAMPO = ("inicio", "longitud", "fin", "decimales")
_ENTEROS_REG = ("min_ocurrencias", "max_ocurrencias")


def _entero(v):
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _sanear(d: dict) -> dict:
    """Normalización determinística de tipos (sin IA): "12" -> 12, version numérica -> texto,
    reglas sin id o sin descripción. No cambia el contenido, solo la forma."""
    d = dict(d)
    if d.get("version") in (None, ""):
        d["version"] = "1.0"
    elif not isinstance(d["version"], str):
        d["version"] = str(d["version"])
    if isinstance(d.get("longitud_registro"), (str, float)):
        d["longitud_registro"] = _entero(d["longitud_registro"])
    trs = []
    for tr in d.get("tipos_registro") or []:
        if isinstance(tr, dict):
            tr = {**tr, **{k: _entero(tr[k]) for k in _ENTEROS_REG if k in tr}}
            if isinstance(tr.get("codigo"), (int, float)):
                tr["codigo"] = str(tr["codigo"])
            if isinstance(tr.get("campos"), list):
                tr["campos"] = [{**c, **{k: _entero(c[k]) for k in _ENTEROS_CAMPO if k in c}} if isinstance(c, dict) else c
                                for c in tr["campos"]]
        trs.append(tr)
    if "tipos_registro" in d:
        d["tipos_registro"] = trs
    reglas = []
    for i, r in enumerate(d.get("reglas") or [], 1):
        if isinstance(r, dict):
            r = dict(r)
            r.setdefault("id", f"R{i}")
            if not r.get("descripcion"):
                r["descripcion"] = (r.get("evidencia") or r.get("expresion") or "")[:120]
        reglas.append(r)
    if "reglas" in d:
        d["reglas"] = reglas
    return d
