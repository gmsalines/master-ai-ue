"""Cruce (conciliación) de dos archivos, sin IA.

Lee cada fuente como tabla:
  - CSV / Excel directamente;
  - TXT posicional o XML regulatorio a través de una especificación (el
    resultado del flujo "del manual a la especificación").
Luego cruza por una llave (una o varias columnas) y clasifica cada registro:
  solo en A, solo en B, en ambos con diferencias, en ambos iguales.
Los importes se comparan con tolerancia. Las llaves duplicadas se informan.

La llave se sugiere con una heurística determinista (similitud de nombres,
unicidad y solapamiento de valores): no consume tokens.
"""
from __future__ import annotations

import difflib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import pandas as pd

from .archivos import leer
from .dsl import Especificacion


# ------------------------------------------------------------------ lectura


def _decodificar(contenido: bytes, codificacion: str = "utf-8") -> str:
    """Decodifica probando la codificación indicada y, si falla, latin-1 (nunca falla)."""
    for cod in dict.fromkeys([codificacion, "utf-8-sig", "latin-1"]):
        try:
            return contenido.decode(cod)
        except (UnicodeDecodeError, LookupError):
            continue
    return contenido.decode("latin-1")


def _fila_encabezado(crudo: pd.DataFrame, max_filas: int = 15) -> int:
    """Primera fila que parece encabezado: la que tiene más celdas de texto no vacías y distintas."""
    mejor, puntaje = 0, -1.0
    ancho = crudo.shape[1] or 1
    for i in range(min(max_filas, len(crudo))):
        fila = [str(v).strip() for v in crudo.iloc[i].tolist() if v is not None and str(v).strip() not in ("", "nan", "None")]
        textos = [v for v in fila if not re.fullmatch(r"-?[\d.,/: -]+", v)]
        p = len(set(textos)) / ancho
        if p > puntaje + 0.2:  # la primera fila claramente mejor gana (evita elegir una fila de datos)
            mejor, puntaje = i, p
    return mejor


def _celdas(df: pd.DataFrame) -> int:
    return df.dropna(how="all").shape[0] * max(1, df.dropna(how="all", axis=1).shape[1])


def hoja_por_defecto(contenido: bytes) -> str:
    """La hoja con más datos (filas × columnas no vacías)."""
    libro = pd.read_excel(io.BytesIO(contenido), sheet_name=None, header=None, dtype=str)
    return max(libro, key=lambda h: _celdas(libro[h]))


def leer_excel(contenido: bytes, hoja: Optional[str] = None) -> pd.DataFrame:
    """Lee un Excel eligiendo la hoja con más datos y detectando la fila de encabezado."""
    libro = pd.read_excel(io.BytesIO(contenido), sheet_name=None if hoja is None else [hoja], header=None, dtype=str)
    if hoja is None:
        hoja = max(libro, key=lambda h: _celdas(libro[h]))
    crudo = libro[hoja]
    h = _fila_encabezado(crudo)
    nombres, vistos = [], {}
    for i, v in enumerate(crudo.iloc[h].tolist()):
        n = re.sub(r"\s+", " ", str(v)).strip() if v is not None and str(v) not in ("nan", "None") else f"columna_{i + 1}"
        vistos[n] = vistos.get(n, 0) + 1
        nombres.append(n if vistos[n] == 1 else f"{n} ({vistos[n]})")
    df = crudo.iloc[h + 1:].copy()
    df.columns = nombres
    df = df.dropna(how="all").dropna(how="all", axis=1).reset_index(drop=True)
    return df


def hojas_excel(contenido: bytes) -> list[str]:
    return pd.ExcelFile(io.BytesIO(contenido)).sheet_names


def tabla_desde_archivo(contenido: bytes, nombre: str, spec: Optional[Especificacion] = None,
                        tipo_registro: Optional[str] = None, hoja: Optional[str] = None) -> pd.DataFrame:
    """Convierte un archivo en tabla. TXT/XML requieren la especificación (y el tipo de registro a cruzar).

    Excel: se elige la hoja con más datos y se detecta la fila de encabezado.
    TXT delimitado con un solo tipo de registro: lectura directa con pandas (rápida para archivos
    de millones de líneas); la validación campo a campo queda para la pestaña «Validar archivo».
    """
    ext = nombre.lower().rsplit(".", 1)[-1]
    if ext in ("xlsx", "xls", "xlsm"):
        return leer_excel(contenido, hoja)
    if ext == "csv":
        texto = _decodificar(contenido)
        return pd.read_csv(io.StringIO(texto), dtype=str, sep=None, engine="python")
    if spec is None:
        raise ValueError(f"para leer '{nombre}' hace falta la especificación del formato")
    if spec.formato == "delimitado" and len(spec.tipos_registro) == 1:
        tr = spec.tipos_registro[0]
        texto = _decodificar(contenido, spec.codificacion)
        df = pd.read_csv(io.StringIO(texto), sep=spec.separador_campos or ";", header=None, dtype=str,
                         names=[c.nombre for c in tr.campos], keep_default_na=False, quoting=3, engine="c")
        return df.map(str.strip) if len(df) < 200_000 else df
    texto = _decodificar(contenido, spec.codificacion)
    registros, _ = leer(spec, texto)
    if tipo_registro is None:  # por defecto, el tipo de registro repetible (el detalle)
        repetibles = [t.codigo for t in spec.tipos_registro if t.max_ocurrencias != 1]
        tipo_registro = repetibles[0] if repetibles else spec.tipos_registro[0].codigo
    filas = []
    for r in registros:
        if r.codigo == tipo_registro:
            fila = {"_linea": r.linea}
            fila.update({k: _a_texto(v) for k, v in r.valores.items()})
            filas.append(fila)
    return pd.DataFrame(filas)


def _a_texto(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v != v.to_integral() else str(int(v))
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


# ------------------------------------------------------------------ llave sugerida (sin IA)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _valores(col: pd.Series) -> set:
    return {str(v).strip().lstrip("0") or "0" for v in col.dropna() if str(v).strip()}


@dataclass
class ParLlave:
    col_a: str
    col_b: str
    puntaje: float
    motivo: str


def sugerir_llave(a: pd.DataFrame, b: pd.DataFrame, max_pares: int = 3) -> list[ParLlave]:
    """Pares de columnas candidatos a llave: valores casi únicos que coinciden entre ambos archivos."""
    pares = []
    for ca in a.columns:
        if ca.startswith("_"):
            continue
        va = _valores(a[ca])
        if len(va) < 2:
            continue
        unic_a = len(va) / max(1, a[ca].notna().sum())
        for cb in b.columns:
            if cb.startswith("_"):
                continue
            vb = _valores(b[cb])
            if len(vb) < 2:
                continue
            solape = len(va & vb) / max(1, min(len(va), len(vb)))
            if solape < 0.3:
                continue
            unic_b = len(vb) / max(1, b[cb].notna().sum())
            nombre = difflib.SequenceMatcher(None, _norm(ca), _norm(cb)).ratio()
            puntaje = 0.55 * solape + 0.3 * min(unic_a, unic_b) + 0.15 * nombre
            pares.append(ParLlave(ca, cb, round(puntaje, 3),
                                  f"coinciden {solape:.0%} de los valores; unicidad {min(unic_a, unic_b):.0%}"))
    pares.sort(key=lambda p: p.puntaje, reverse=True)
    elegidos, usadas_a, usadas_b = [], set(), set()
    for p in pares:
        if p.col_a not in usadas_a and p.col_b not in usadas_b:
            elegidos.append(p)
            usadas_a.add(p.col_a)
            usadas_b.add(p.col_b)
        if len(elegidos) >= max_pares:
            break
    return elegidos


def sugerir_comparaciones(a: pd.DataFrame, b: pd.DataFrame, llave_a: list[str], llave_b: list[str]) -> list[tuple[str, str]]:
    """Columnas a comparar: pares no-llave con nombres parecidos (p. ej. importe <-> monto_retenido)."""
    out = []
    libres_b = [c for c in b.columns if c not in llave_b and not c.startswith("_")]
    for ca in a.columns:
        if ca in llave_a or ca.startswith("_"):
            continue
        cand = difflib.get_close_matches(_norm(ca), [_norm(c) for c in libres_b], n=1, cutoff=0.6)
        if cand:
            cb = libres_b[[_norm(c) for c in libres_b].index(cand[0])]
            out.append((ca, cb))
            libres_b.remove(cb)
    return out


# ------------------------------------------------------------------ cruce


def _num(v) -> Optional[Decimal]:
    if v is None or (isinstance(v, float) and v != v):
        return None
    t = str(v).strip().replace(" ", "")
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return Decimal(t)
    except Exception:
        return None


def _clave(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    return df[cols].astype(str).apply(lambda f: "|".join((x.strip().lstrip("0") or "0") if x not in ("None", "nan") else "" for x in f), axis=1)


@dataclass
class ResultadoCruce:
    solo_a: pd.DataFrame
    solo_b: pd.DataFrame
    diferencias: pd.DataFrame
    coincidencias: pd.DataFrame
    duplicados_a: pd.DataFrame
    duplicados_b: pd.DataFrame
    resumen: dict = field(default_factory=dict)

    def a_excel(self) -> bytes:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            pd.DataFrame([self.resumen]).T.rename(columns={0: "valor"}).to_excel(xw, sheet_name="Resumen")
            for nombre, df in [("Solo en A", self.solo_a), ("Solo en B", self.solo_b), ("Diferencias", self.diferencias),
                               ("Coincidencias", self.coincidencias), ("Duplicados A", self.duplicados_a),
                               ("Duplicados B", self.duplicados_b)]:
                df.to_excel(xw, sheet_name=nombre, index=False)
        return buf.getvalue()


def cruzar(a: pd.DataFrame, b: pd.DataFrame, llave_a: list[str], llave_b: list[str],
           comparar: list[tuple[str, str]] = (), tolerancia: Decimal = Decimal("0.01")) -> ResultadoCruce:
    if len(llave_a) != len(llave_b) or not llave_a:
        raise ValueError("la llave debe tener la misma cantidad de columnas en A y en B")
    a, b = a.copy(), b.copy()
    a["_llave"], b["_llave"] = _clave(a, llave_a), _clave(b, llave_b)
    dup_a = a[a["_llave"].duplicated(keep=False)]
    dup_b = b[b["_llave"].duplicated(keep=False)]
    ua, ub = a.drop_duplicates("_llave"), b.drop_duplicates("_llave")
    m = ua.merge(ub, on="_llave", how="outer", suffixes=("_A", "_B"), indicator=True)
    solo_a = a[a["_llave"].isin(m.loc[m["_merge"] == "left_only", "_llave"])]
    solo_b = b[b["_llave"].isin(m.loc[m["_merge"] == "right_only", "_llave"])]
    ambos = m[m["_merge"] == "both"].copy()

    def col(df_cols, nombre, lado):
        return nombre + lado if nombre + lado in df_cols else nombre

    filas_dif, filas_ok = [], []
    for _, fila in ambos.iterrows():
        difs = []
        for ca, cb in comparar:
            va, vb = fila.get(col(ambos.columns, ca, "_A")), fila.get(col(ambos.columns, cb, "_B"))
            na, nb = _num(va), _num(vb)
            if na is not None and nb is not None:
                if abs(na - nb) > tolerancia:
                    difs.append(f"{ca}: {na} vs {cb}: {nb} (dif. {na - nb})")
            elif str(va).strip() != str(vb).strip():
                difs.append(f"{ca}: '{va}' vs {cb}: '{vb}'")
        d = {"llave": fila["_llave"], **{f"A.{c}": fila.get(col(ambos.columns, c, "_A")) for c, _ in comparar},
             **{f"B.{c}": fila.get(col(ambos.columns, c, "_B")) for _, c in comparar}}
        if difs:
            filas_dif.append({**d, "diferencias": "; ".join(difs)})
        else:
            filas_ok.append(d)
    res = ResultadoCruce(
        solo_a=solo_a.drop(columns="_llave"), solo_b=solo_b.drop(columns="_llave"),
        diferencias=pd.DataFrame(filas_dif), coincidencias=pd.DataFrame(filas_ok),
        duplicados_a=dup_a.drop(columns="_llave"), duplicados_b=dup_b.drop(columns="_llave"),
    )
    res.resumen = {
        "registros A": len(a), "registros B": len(b), "solo en A": len(res.solo_a), "solo en B": len(res.solo_b),
        "en ambos con diferencias": len(res.diferencias), "en ambos iguales": len(res.coincidencias),
        "llaves duplicadas en A": dup_a["_llave"].nunique(), "llaves duplicadas en B": dup_b["_llave"].nunique(),
        "llave": f"{'+'.join(llave_a)} ↔ {'+'.join(llave_b)}", "tolerancia": str(tolerancia),
    }
    return res


# ------------------------------------------------------------------ detección del formato (sin IA)


@dataclass
class Deteccion:
    nombre_spec: Optional[str]
    spec: Optional[Especificacion]
    tipo_registro: Optional[str]
    confianza: float
    detalle: str


def detectar_especificacion(contenido: bytes, nombre: str, specs: dict) -> Deteccion:
    """Prueba cada especificación de la biblioteca y elige la que lee el archivo con menos errores.

    Confianza = proporción de registros leídos sin errores de estructura ni de campo.
    """
    ext = nombre.lower().rsplit(".", 1)[-1]
    if ext in ("csv", "xlsx", "xls"):
        return Deteccion(None, None, None, 1.0, "tabla (CSV/Excel): no necesita especificación")
    mejor = Deteccion(None, None, None, 0.0, "ninguna especificación de la biblioteca lee este archivo")
    for n, spec in specs.items():
        muestra = len(contenido) > 2_000_000 and spec.formato != "xml"  # archivos grandes: se prueban las primeras 500 líneas
        texto = _decodificar(contenido[:200_000] if muestra else contenido, spec.codificacion)
        es_xml = texto.lstrip().startswith("<")
        if (spec.formato == "xml") != es_xml:
            continue
        if muestra:
            texto = "\n".join(texto.replace("\r\n", "\n").split("\n")[:500]) + "\n"
        try:
            regs, estructura = leer(spec, texto)
        except Exception:  # noqa: BLE001
            continue
        total = len(regs) + len(estructura)
        if not total:
            continue
        buenos = sum(1 for r in regs if not r.errores)
        conf = buenos / total
        if conf > mejor.confianza:
            repet = [t.codigo for t in spec.tipos_registro if t.max_ocurrencias != 1]
            mejor = Deteccion(n, spec, repet[0] if repet else spec.tipos_registro[0].codigo, conf,
                              f"{buenos} de {total} registros leídos sin errores con '{n}'")
    return mejor


def biblioteca_de_especificaciones(*carpetas) -> dict:
    """Especificaciones guardadas en disco (p. ej. gold/ y especificaciones/), por nombre de archivo."""
    import json as _json
    from pathlib import Path as _Path

    out = {}
    for c in carpetas:
        for p in sorted(_Path(c).glob("*.json")) if _Path(c).exists() else []:
            try:
                d = _json.loads(p.read_text(encoding="utf-8"))
                out[p.stem] = Especificacion.model_validate(d.get("spec", d))
            except Exception:  # noqa: BLE001
                continue
    return out


# ------------------------------------------------------------------ llave con IA (solo si el código no alcanza)


def llave_clara(sugeridas: list[ParLlave], umbral: float = 0.6) -> bool:
    return bool(sugeridas) and sugeridas[0].puntaje >= umbral


def sugerir_llave_ia(a: pd.DataFrame, b: pd.DataFrame, proveedor, muestras: int = 5) -> dict:
    """Pregunta al LLM por la llave y las columnas a comparar, enviando solo nombres de columna y unas pocas muestras.

    La respuesta se valida contra las columnas reales (no se aceptan columnas inventadas) y la llave se
    comprueba midiendo el solapamiento de valores. Devuelve {"llave_a", "llave_b", "comparar", "motivo", "solape"}.
    """
    import json as _json

    def perfil(df):
        return {c: [str(v) for v in df[c].dropna().astype(str).head(muestras)] for c in df.columns if not c.startswith("_")}

    pedido = (
        "Tengo dos tablas que quiero conciliar. Columnas con ejemplos de valores:\n"
        f"A: {_json.dumps(perfil(a), ensure_ascii=False)}\nB: {_json.dumps(perfil(b), ensure_ascii=False)}\n"
        "Indicá qué columna(s) identifican el mismo registro en ambas (llave) y qué pares de columnas conviene comparar "
        '(importes, fechas). Respondé SOLO JSON: {"llave_a": ["col"], "llave_b": ["col"], '
        '"comparar": [["colA", "colB"]], "motivo": "breve"}. Usá exactamente los nombres de columna dados.'
    )
    r = proveedor.completar([{"role": "user", "content": pedido}], modo_json=True, temperatura=0.0, max_tokens=800)
    t = r.texto
    d = _json.loads(t[t.find("{") : t.rfind("}") + 1])
    la = [c for c in d.get("llave_a") or [] if c in a.columns]
    lb = [c for c in d.get("llave_b") or [] if c in b.columns]
    if not la or len(la) != len(lb):
        raise ValueError("la IA no propuso una llave válida con las columnas existentes")
    comparar = [(x, y) for x, y in (d.get("comparar") or []) if x in a.columns and y in b.columns]
    ka, kb = set(_clave(a, la)), set(_clave(b, lb))
    solape = len(ka & kb) / max(1, min(len(ka), len(kb)))
    return {"llave_a": la, "llave_b": lb, "comparar": comparar, "motivo": d.get("motivo", ""), "solape": solape,
            "tokens": r.tokens_entrada + r.tokens_salida}
