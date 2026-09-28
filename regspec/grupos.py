"""Conciliación por grupos de archivos (motor N-way, sin IA).

Modelo:
    Archivo  -> tabla + llave (una o varias columnas) + transformación de la llave + filtros de exclusión.
    Grupo    -> uno o varios archivos combinados con un modo:
                  "concatenar"      une las filas (p. ej. varios meses del mismo reporte);
                  "base_referencia" el primer archivo es la base y los demás se usan como referencia:
                                    se buscan por llave y se traen columnas (lookup);
                  "sumar_por_llave" une las filas y suma los importes por llave.
    Cruce    -> N grupos sobre la llave construida. Cada grupo se reduce a una fila por llave (sumando
                importes); se informa la presencia de cada llave en cada grupo y las diferencias de importe
                con tolerancia.

Tipos de match: exacto; transformación (normaliza la llave: solo números, sin ceros a la izquierda,
mayúsculas sin espacios); tolerancia (en importes); exclusión (filtros previos al cruce).
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import pandas as pd

TRANSFORMACIONES = {
    "exacta": "Exacta (solo recorta espacios)",
    "solo_numeros": "Solo números (quita guiones, puntos, letras)",
    "sin_ceros": "Sin ceros a la izquierda",
    "normalizada": "Mayúsculas, sin espacios ni acentos",
}
MODOS = {
    "concatenar": "Concatenar filas (mismo tipo de archivo)",
    "base_referencia": "Base + referencia (el 1.º es la base; los demás aportan columnas por llave)",
    "sumar_por_llave": "Sumar importes por llave",
}
OPERADORES = ["==", "!=", ">", ">=", "<", "<=", "contiene", "vacío", "no vacío"]


def transformar(valor, tipo: str) -> str:
    if valor is None or (isinstance(valor, float) and valor != valor):
        return ""
    t = str(valor).strip()
    if t.lower() in ("nan", "none"):
        return ""
    if tipo == "solo_numeros":
        t = re.sub(r"\D", "", t).lstrip("0") or ("0" if re.sub(r"\D", "", t) else "")
    elif tipo == "sin_ceros":
        t = t.lstrip("0") or ("0" if t else "")
    elif tipo == "normalizada":
        t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().upper()
        t = re.sub(r"\s+", "", t)
    return t


def numero(v) -> Optional[Decimal]:
    if v is None or (isinstance(v, float) and v != v):
        return None
    t = str(v).strip().replace(" ", "").replace("$", "")
    if not t or t.lower() in ("nan", "none"):
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return Decimal(t)
    except Exception:  # noqa: BLE001
        return None


@dataclass
class Filtro:
    columna: str
    operador: str
    valor: str = ""

    def mascara(self, df: pd.DataFrame) -> pd.Series:
        """True = la fila se EXCLUYE."""
        col = df[self.columna]
        if self.operador == "vacío":
            return col.isna() | (col.astype(str).str.strip() == "")
        if self.operador == "no vacío":
            return ~(col.isna() | (col.astype(str).str.strip() == ""))
        if self.operador == "contiene":
            return col.astype(str).str.contains(self.valor, case=False, na=False, regex=False)
        vn = numero(self.valor)
        nums = col.map(numero)
        if vn is not None and nums.notna().any():
            ops = {"==": nums == vn, "!=": nums != vn, ">": nums > vn, ">=": nums >= vn, "<": nums < vn, "<=": nums <= vn}
            return ops[self.operador].fillna(False).astype(bool)
        s = col.astype(str).str.strip()
        return (s == self.valor) if self.operador == "==" else (s != self.valor)


@dataclass
class ArchivoGrupo:
    nombre: str
    df: pd.DataFrame
    llave: list[str]
    transformacion: str = "exacta"
    filtros: list[Filtro] = field(default_factory=list)
    traer: list[str] = field(default_factory=list)  # solo referencias en modo base_referencia

    def preparado(self) -> tuple[pd.DataFrame, int]:
        df = self.df.copy()
        excluidas = 0
        for f in self.filtros:
            if f.columna in df.columns:
                m = f.mascara(df)
                excluidas += int(m.sum())
                df = df[~m]
        df["_llave"] = df[self.llave].apply(lambda fila: "|".join(transformar(v, self.transformacion) for v in fila), axis=1)
        df["_archivo"] = self.nombre
        return df, excluidas


@dataclass
class Grupo:
    nombre: str
    archivos: list[ArchivoGrupo]
    modo: str = "concatenar"
    importes: list[str] = field(default_factory=list)  # columnas numéricas que se suman por llave

    def construir(self) -> tuple[pd.DataFrame, dict]:
        """Devuelve el detalle del grupo (filas con _llave) y estadísticas."""
        prep = [a.preparado() for a in self.archivos]
        stats = {"archivos": len(self.archivos), "excluidas por filtros": sum(e for _, e in prep)}
        if self.modo == "base_referencia" and len(prep) > 1:
            base = prep[0][0]
            total_base = len(base)
            for (ref, _), arch in zip(prep[1:], self.archivos[1:]):
                cols = [c for c in (arch.traer or []) if c in ref.columns and c not in base.columns]
                ref_u = ref.drop_duplicates("_llave")[["_llave", *cols]]
                base = base.merge(ref_u, on="_llave", how="left", indicator=f"_en_{arch.nombre}")
                encontrados = int((base[f"_en_{arch.nombre}"] == "both").sum())
                stats[f"encontradas en {arch.nombre}"] = f"{encontrados} de {total_base} ({encontrados / max(1, total_base):.1%})"
                base = base.drop(columns=f"_en_{arch.nombre}")
            detalle = base
        else:
            detalle = pd.concat([p for p, _ in prep], ignore_index=True)
        vacias = int((detalle["_llave"].str.replace("|", "", regex=False) == "").sum())
        stats.update({"filas": len(detalle), "llaves únicas": detalle["_llave"].nunique(),
                      "filas con llave repetida": int(detalle["_llave"].duplicated(keep=False).sum()),
                      "llaves vacías (se excluyen del cruce)": vacias})
        detalle = detalle[detalle["_llave"].str.replace("|", "", regex=False) != ""]
        return detalle, stats

    def por_llave(self, detalle: pd.DataFrame) -> pd.DataFrame:
        """Una fila por llave: suma de importes, cantidad de filas y primeros valores del resto."""
        agg = {c: (lambda s: sum((numero(x) or Decimal(0)) for x in s)) for c in self.importes if c in detalle.columns}
        otros = [c for c in detalle.columns if c not in agg and c != "_llave"]
        g = detalle.groupby("_llave", sort=False)
        out = g.agg({**agg, **{c: "first" for c in otros}}) if (agg or otros) else g.size().to_frame("_n")
        out["_filas"] = g.size()
        return out


@dataclass
class ResultadoGrupos:
    matriz: pd.DataFrame          # una fila por llave: presencia en cada grupo + importes
    diferencias: pd.DataFrame
    resumen: dict
    estadisticas: dict
    detalles: dict                # nombre de grupo -> detalle

    def a_excel(self) -> bytes:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            pd.DataFrame([{"indicador": k, "valor": str(v)} for k, v in self.resumen.items()]).to_excel(xw, sheet_name="Resumen", index=False)
            filas = [{"grupo": g, "indicador": k, "valor": str(v)} for g, st in self.estadisticas.items() for k, v in st.items()]
            pd.DataFrame(filas).to_excel(xw, sheet_name="Grupos", index=False)
            _excel_safe(self.matriz).to_excel(xw, sheet_name="Cruce por llave", index=False)
            _excel_safe(self.diferencias).to_excel(xw, sheet_name="Diferencias", index=False)
            for g, d in self.detalles.items():
                _excel_safe(d).to_excel(xw, sheet_name=f"Detalle {g}"[:31], index=False)
        return buf.getvalue()


def _excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    return df.map(lambda v: float(v) if isinstance(v, Decimal) else v)


def cruzar_grupos(grupos: list[Grupo], comparar: list[tuple[str, ...]] = (), tolerancia: Decimal = Decimal("0.01")) -> ResultadoGrupos:
    """Cruza N grupos por la llave construida.

    `comparar`: tuplas con una columna de importe por grupo (en el orden de `grupos`), p. ej.
    [("monto_retenido", "Monto Retenido")]. Se comparan los importes sumados por llave.
    """
    detalles, estad, por = {}, {}, {}
    for g in grupos:
        det, st = g.construir()
        detalles[g.nombre], estad[g.nombre] = det, st
        g_imp = set(g.importes) | {t[i] for t in comparar for i, gg in enumerate(grupos) if gg is g and i < len(t)}
        g.importes = [c for c in g_imp if c in det.columns]
        por[g.nombre] = g.por_llave(det)
    universo = pd.Index(sorted(set().union(*[set(p.index) for p in por.values()])), name="llave")
    m = pd.DataFrame(index=universo)
    for g in grupos:
        m[f"en {g.nombre}"] = universo.isin(por[g.nombre].index)
        m[f"filas {g.nombre}"] = por[g.nombre]["_filas"].reindex(universo).fillna(0).astype(int)
    for t in comparar:
        for i, g in enumerate(grupos):
            if i < len(t) and t[i] in por[g.nombre].columns:
                m[f"{g.nombre}.{t[i]}"] = por[g.nombre][t[i]].reindex(universo)
    presencia = m[[f"en {g.nombre}" for g in grupos]]
    m["en todos"] = presencia.all(axis=1)
    m["estado"] = presencia.apply(lambda f: "en todos" if f.all() else "solo en " + " + ".join(
        c.removeprefix("en ") for c, v in f.items() if v), axis=1)

    difs = []
    for llave, fila in m[m["en todos"]].iterrows():
        textos = []
        for t in comparar:
            vals = [fila.get(f"{g.nombre}.{t[i]}") for i, g in enumerate(grupos) if i < len(t)]
            vals = [v for v in vals if v is not None and not (isinstance(v, float) and v != v)]
            if len(vals) >= 2 and max(vals) - min(vals) > tolerancia:
                textos.append(" vs ".join(f"{g.nombre}.{t[i]}={fila.get(f'{g.nombre}.{t[i]}')}" for i, g in enumerate(grupos) if i < len(t))
                              + f" (dif. {max(vals) - min(vals)})")
        if textos:
            difs.append({"llave": llave, "diferencias": "; ".join(textos)})
    m["con diferencias"] = m.index.isin([d["llave"] for d in difs])
    matriz = m.reset_index()
    resumen = {"llaves en el universo": len(universo), "en todos los grupos": int(m["en todos"].sum()),
               "en todos, con diferencias": len(difs), "en todos, iguales": int(m["en todos"].sum()) - len(difs),
               "tolerancia": str(tolerancia)}
    for g in grupos:
        solo = presencia[f"en {g.nombre}"] & ~presencia.drop(columns=f"en {g.nombre}").any(axis=1)
        resumen[f"solo en {g.nombre}"] = int(solo.sum())
        resumen[f"cobertura de {g.nombre} (llaves presentes en todos los demás)"] = (
            f"{int(m.loc[presencia[f'en {g.nombre}'], 'en todos'].sum())} de {int(presencia[f'en {g.nombre}'].sum())}")
    return ResultadoGrupos(matriz, pd.DataFrame(difs), resumen, estad, detalles)
