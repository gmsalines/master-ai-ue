"""Recuperación de fragmentos del manual (sin IA, sin tokens).

Cada paso de la extracción recibe solo las partes del manual que necesita, en
lugar del manual completo. Esto reduce los tokens de entrada y permite
procesar manuales largos (PDF de decenas de páginas).

Método: el manual se divide en fragmentos (por títulos en Markdown o por
párrafos/páginas en texto plano) y se puntúan con coincidencia léxica
ponderada contra la consulta de cada paso. Es deliberadamente simple
(BM25 simplificado, sin embeddings) para no depender de modelos ni de red.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass


@dataclass
class Fragmento:
    indice: int
    titulo: str
    texto: str

    @property
    def completo(self) -> str:
        return (self.titulo + "\n" + self.texto).strip()


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return t


def _tokens(t: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm(t))


def dividir(manual: str, max_chars: int = 1800) -> list[Fragmento]:
    """Divide por títulos Markdown; si no hay, por párrafos. Fragmentos largos se parten."""
    bloques: list[tuple[str, str]] = []
    if re.search(r"^#{1,6}\s", manual, re.M):
        titulo, buf = "", []
        for linea in manual.splitlines():
            if re.match(r"^#{1,6}\s", linea):
                if buf or titulo:
                    bloques.append((titulo, "\n".join(buf)))
                titulo, buf = linea.lstrip("#").strip(), []
            else:
                buf.append(linea)
        bloques.append((titulo, "\n".join(buf)))
    else:  # texto plano (p. ej. extraído de PDF): párrafos separados por líneas en blanco o saltos de página
        for par in re.split(r"\n\s*\n|\f", manual):
            par = par.strip()
            if par:
                primera = par.splitlines()[0][:80]
                bloques.append((primera, par))
    out: list[Fragmento] = []
    for titulo, texto in bloques:
        texto = texto.strip()
        if not texto and not titulo:
            continue
        while len(texto) > max_chars:  # partir respetando líneas
            corte = texto.rfind("\n", 0, max_chars)
            corte = corte if corte > max_chars // 2 else max_chars
            out.append(Fragmento(len(out), titulo, texto[:corte]))
            texto = texto[corte:].strip()
            titulo = titulo + " (cont.)"
        out.append(Fragmento(len(out), titulo, texto))
    return out


def seleccionar(frags: list[Fragmento], terminos: list[str], presupuesto: int = 6000,
                obligatorios: tuple[int, ...] = (), patrones: tuple[str, ...] = (),
                peso_titulo: float = 3.0, max_fragmentos: int = 99) -> str:
    idx = seleccionar_indices(frags, terminos, presupuesto, obligatorios, patrones, peso_titulo, max_fragmentos)
    return unir(frags, idx)


def unir(frags: list[Fragmento], indices) -> str:
    return "\n\n".join(frags[i].completo for i in sorted(set(indices)))


def indice_de_titulos(frags: list[Fragmento]) -> str:
    """Tabla de contenidos barata: título y primera línea de cada fragmento."""
    lineas = []
    for f in frags:
        primera = next((l.strip() for l in f.texto.splitlines() if l.strip() and not l.strip().startswith("|")), "")
        lineas.append(f"- {f.titulo}" + (f": {primera[:120]}" if primera else ""))
    return "\n".join(lineas)


def seleccionar_indices(frags: list[Fragmento], terminos: list[str], presupuesto: int = 6000,
                        obligatorios: tuple[int, ...] = (), patrones: tuple[str, ...] = (),
                        peso_titulo: float = 3.0, max_fragmentos: int = 99) -> list[int]:
    """Devuelve los fragmentos más relevantes (en su orden original) hasta `presupuesto` caracteres.

    `terminos`: palabras de la consulta. `patrones`: regex que, si aparecen, suman mucho (p. ej. el código
    del registro). `obligatorios`: índices que siempre se incluyen.
    """
    docs = [_tokens(f.completo) for f in frags]
    n = len(docs) or 1
    df: dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    consulta = [t for t in _tokens(" ".join(terminos))]

    def puntaje(i: int) -> float:
        f, d = frags[i], docs[i]
        if not d:
            return 0.0
        tit = set(_tokens(f.titulo))
        s = 0.0
        for t in consulta:
            tf = d.count(t)
            if tf:
                idf = math.log(1 + n / (1 + df.get(t, 0)))
                s += idf * (tf / (tf + 1.5)) * (peso_titulo if t in tit else 1.0)
        for p in patrones:
            if re.search(p, f.titulo, re.I):
                s += 12
            elif re.search(p, f.texto, re.I):
                s += 4
        return s

    orden = sorted(range(len(frags)), key=puntaje, reverse=True)
    elegidos, usados = set(obligatorios), sum(len(frags[i].completo) for i in obligatorios)
    for i in orden:
        if i in elegidos or puntaje(i) <= 0:
            continue
        largo = len(frags[i].completo)
        if usados + largo > presupuesto and elegidos:
            continue
        elegidos.add(i)
        usados += largo
        if usados >= presupuesto or len(elegidos) >= max_fragmentos:
            break
    # los fragmentos que continúan a uno elegido ("(cont.)") se incluyen también
    for i in list(elegidos):
        j = i + 1
        while j < len(frags) and frags[j].titulo.startswith(frags[i].titulo.split(" (cont.)")[0]) and "(cont.)" in frags[j].titulo:
            elegidos.add(j)
            j += 1
    return sorted(elegidos)


# consultas típicas de cada paso
TERMINOS_ESQUELETO = ["registro", "registros", "longitud", "caracteres", "posiciones", "archivo", "estructura",
                      "orden", "primero", "ultimo", "unico", "xml", "raiz", "elemento", "separador", "linea",
                      "cabecera", "encabezado", "cierre", "totales", "detalle", "formato", "general"]
TERMINOS_REGLAS = ["debe", "deberan", "debera", "validacion", "validaciones", "control", "controles", "igual",
                   "coincidir", "coincida", "suma", "mayor", "menor", "obligatorio", "obligatoria", "no", "puede",
                   "rechazara", "rechazo", "consistencia", "verificara", "blanco", "posterior", "anterior"]
TERMINOS_NOTAS = ["nota", "notas", "valores", "codigos", "tabla", "admitidos", "permitidos", "corresponde"]


def patron_registro(codigo: str, nombre: str = "") -> tuple[str, ...]:
    c = re.escape(str(codigo))
    pats = [rf"registro\s+(tipo\s+)?{c}\b", rf"\({c}\)", rf"[\"“]{c}[\"”]", rf"\btipo\s+{c}\b", rf"<{c}\b"]
    if nombre and len(nombre) > 3:
        pats.append(re.escape(nombre))
    return tuple(pats)
