"""Texto de un manual en PDF, estable entre versiones de pypdf.

Desde pypdf 6 la extracción por defecto de algunos PDF devuelve casi una palabra por línea («Tipo / de / Archivo:»),
y el código que busca encabezados por línea (archivos que declara el manual, tablas) deja de encontrarlos. Si el texto
sale así de fragmentado, se vuelve a extraer en modo «layout», que conserva las líneas como se ven en la página.
"""
from __future__ import annotations

import io
import re


def _fragmentado(texto: str) -> bool:
    lineas = [l for l in texto.splitlines() if l.strip()]
    if len(lineas) < 20:
        return False
    sueltas = sum(1 for l in lineas if len(l.split()) <= 1)
    return sueltas / len(lineas) > 0.4


def texto_de_pdf(datos: bytes) -> str:
    from pypdf import PdfReader

    lector = PdfReader(io.BytesIO(datos))
    texto = "\n".join(p.extract_text() or "" for p in lector.pages)
    if not _fragmentado(texto):
        return texto
    try:
        layout = "\n".join(p.extract_text(extraction_mode="layout") or "" for p in lector.pages)
    except (TypeError, ValueError):  # versión de pypdf sin modo layout
        return texto
    layout = "\n".join(l.rstrip() for l in layout.splitlines())
    return re.sub(r"\n{3,}", "\n\n", layout).strip() + "\n"
