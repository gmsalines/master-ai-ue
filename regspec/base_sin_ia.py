"""Línea base SIN IA: extracción por reglas (regex + lectura de tablas).

Representa lo que haría un desarrollador escribiendo un parser convencional:
reconoce tablas con columnas de posición/longitud/tipo, notación tipo COBOL
(X(n), 9(n), 9(n)V99), valores fijos y máscaras de fecha. No interpreta prosa,
notas al pie ni reglas de negocio.
"""
from __future__ import annotations

import re
from typing import Optional

from .dsl import a_snake

_PIC = re.compile(r"^\s*(X|9)\((\d+)\)(?:V(9+|9\((\d+)\)))?\s*$", re.I)
_FIJO = re.compile(r'(?:valor fijo|constante|valor)\s*[:=]?\s*["“]([^"”]+)["”]', re.I)
_FECHA = re.compile(r"\b((?:AAAA|YYYY|DD|MM)(?:[-/.]?(?:AAAA|YYYY|DD|MM)){1,2})\b")
_DEC = re.compile(r"(\d+)\s+decimales", re.I)

COLUMNAS = {
    "inicio": ("desde", "inicio", "inicial", "pos"),
    "fin": ("hasta", "fin"),
    "longitud": ("long", "tam", "largo"),
    "tipo": ("tipo", "formato"),
    "oblig": ("oblig",),
    "obs": ("observ", "nota"),
    "descripcion": ("descrip",),
    "nombre": ("campo", "nombre"),
}


def _mapear_columnas(cabecera: list[str]) -> dict[str, int]:
    m: dict[str, int] = {}
    for i, h in enumerate(cabecera):
        h = h.lower().strip()
        for clave, pistas in COLUMNAS.items():
            if clave not in m and any(h.startswith(p) or p in h for p in pistas):
                if clave == "tipo" and "registro" in h:
                    continue
                m[clave] = i
                break
    return m


def _celdas(linea: str) -> list[str]:
    return [c.strip() for c in linea.strip().strip("|").split("|")]


def _entero(s: str) -> Optional[int]:
    s = (s or "").strip()
    return int(s) if s.isdigit() else None


def _codigo_seccion(titulo: str) -> Optional[str]:
    for rx in (r"registro\s+tipo\s+([A-Z0-9]{1,4})\b", r"\(([A-Z0-9]{1,4})\)", r"registro\s+([A-Z0-9]{1,4})\b"):
        m = re.search(rx, titulo, re.I)
        if m and (m.group(1).isdigit() or m.group(1).isupper()):
            return m.group(1)
    return None


def extraer_base(manual: str) -> dict:
    texto = manual
    m = re.search(r"(?:longitud fija de|registros de)\s+(\d+)\s+(?:caracteres|posiciones)", texto, re.I)
    longitud = int(m.group(1)) if m else None
    sep = "CRLF" if re.search(r"CR\s*\+?\s*LF|retorno de carro", texto, re.I) else "LF"

    secciones: list[tuple[str, list[str]]] = []
    actual: Optional[tuple[str, list[str]]] = None
    for linea in texto.splitlines():
        if linea.startswith("#"):
            actual = (linea.lstrip("#").strip(), [])
            secciones.append(actual)
        elif actual:
            actual[1].append(linea)

    tipos = []
    for titulo, lineas in secciones:
        codigo = _codigo_seccion(titulo)
        tabla = [l for l in lineas if l.strip().startswith("|")]
        if not codigo or len(tabla) < 3:
            continue
        cab = _mapear_columnas(_celdas(tabla[0]))
        if "inicio" not in cab or "nombre" not in cab:
            continue
        cuerpo_texto = " ".join(lineas).lower()
        campos = []
        for fila in tabla[2:]:
            c = _celdas(fila)
            get = lambda k: c[cab[k]] if k in cab and cab[k] < len(c) else ""  # noqa: E731
            nombre_txt = get("nombre")
            inicio = _entero(get("inicio"))
            if not nombre_txt or inicio is None:
                continue
            fin = _entero(get("fin"))
            lon = _entero(get("longitud"))
            tipo_txt, obs = get("tipo"), " ".join([get("obs"), get("descripcion")])
            campo = {"nombre": a_snake(nombre_txt), "descripcion": nombre_txt, "inicio": inicio, "fin": fin,
                     "tipo": "alfanumerico", "decimales": 0, "obligatorio": True, "evidencia": fila.strip()}
            pic = _PIC.match(tipo_txt)
            if pic:
                n = int(pic.group(2))
                if pic.group(1).upper() == "X":
                    campo.update(tipo="alfanumerico", longitud=n)
                elif pic.group(3):
                    d = int(pic.group(4)) if pic.group(4) else len(pic.group(3))
                    campo.update(tipo="decimal", longitud=n + d, decimales=d)
                else:
                    campo.update(tipo="numerico", longitud=n)
            else:
                t = tipo_txt.strip().lower()
                if t in ("n", "num", "numérico", "numerico", "9"):
                    campo["tipo"] = "numerico"
                elif t.startswith("fecha"):
                    campo["tipo"] = "fecha"
            if lon is None and "longitud" not in campo:
                lon = (fin - inicio + 1) if fin else None
            if "longitud" not in campo:
                campo["longitud"] = lon or 1
            dec = _DEC.search(obs)
            if dec and campo["tipo"] in ("numerico", "alfanumerico"):
                campo.update(tipo="decimal", decimales=int(dec.group(1)))
            fijo = _FIJO.search(obs)
            if fijo:
                campo.update(tipo="constante", valor_constante=fijo.group(1), decimales=0)
            fch = _FECHA.search(obs)
            if fch:
                campo.update(tipo="fecha", formato_fecha=fch.group(1).upper(), decimales=0)
            ob = get("oblig").strip().upper()
            if ob in ("N", "NO") or re.search(r"relleno|filler", nombre_txt, re.I) or "opcional" in obs.lower():
                campo["obligatorio"] = False
            campos.append(campo)
        if not campos:
            continue
        unico = bool(re.search(r"\búnico\b|una sola vez", cuerpo_texto))
        tipos.append({"codigo": codigo, "nombre": titulo, "min_ocurrencias": 1,
                      "max_ocurrencias": 1 if unico else None, "posicion": "cualquiera", "campos": campos})
    if tipos:
        tipos[0]["posicion"] = "primero"
        tipos[0]["max_ocurrencias"] = 1
        if len(tipos) > 1:
            tipos[-1]["posicion"] = "ultimo"
            tipos[-1]["max_ocurrencias"] = 1

    titulo = next((l.lstrip("#").strip() for l in texto.splitlines() if l.startswith("##")), "Especificación")
    formato = "ancho_fijo" if tipos or not re.search(r"\bXML\b", texto) else "xml"
    return {"nombre": titulo, "formato": formato, "longitud_registro": longitud, "separador_lineas": sep,
            "etiqueta_raiz_xml": None, "tipos_registro": tipos, "reglas": []}
