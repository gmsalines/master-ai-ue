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


def tabla_desde_archivo(contenido: bytes, nombre: str, spec: Optional[Especificacion] = None,
                        tipo_registro: Optional[str] = None) -> pd.DataFrame:
    """Convierte un archivo en tabla. TXT/XML requieren la especificación (y el tipo de registro a cruzar)."""
    ext = nombre.lower().rsplit(".", 1)[-1]
    if ext in ("xlsx", "xls"):
        return pd.read_excel(io.BytesIO(contenido), dtype=str)
    if ext == "csv":
        texto = contenido.decode("utf-8-sig", errors="replace")
        return pd.read_csv(io.StringIO(texto), dtype=str, sep=None, engine="python")
    if spec is None:
        raise ValueError(f"para leer '{nombre}' hace falta la especificación del formato")
    texto = contenido.decode("utf-8", errors="replace")
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
