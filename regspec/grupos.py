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
# cómo se muestran (sin símbolos, para usuarios que no programan)
OPERADORES_TEXTO = {"==": "es igual a", "!=": "es distinto de", ">": "es mayor que", ">=": "es mayor o igual a",
                    "<": "es menor que", "<=": "es menor o igual a", "contiene": "contiene", "vacío": "está vacío",
                    "no vacío": "no está vacío"}
OPERADORES_MULTIVALOR = ("==", "!=", "contiene")  # admiten varios valores (cumple si coincide con alguno)
ACCIONES = {"excluir": "Excluir filas donde…", "incluir": "Incluir solo filas donde…"}


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


def transformar_serie(s: pd.Series, tipo: str) -> pd.Series:
    """Versión vectorizada de `transformar` (mismo resultado, apta para millones de filas)."""
    t = s.astype("string").fillna("").str.strip()
    t = t.mask(t.str.lower().isin(["nan", "none", "<na>"]), "")
    if tipo == "solo_numeros":
        d = t.str.replace(r"\D", "", regex=True)
        t = d.str.lstrip("0").mask((d != "") & (d.str.lstrip("0") == ""), "0")
    elif tipo == "sin_ceros":
        t = t.str.lstrip("0").mask((t != "") & (t.str.lstrip("0") == ""), "0")
    elif tipo == "normalizada":
        t = t.map(lambda v: transformar(v, "normalizada"))
    return t.astype(object)


@dataclass
class Filtro:
    """Condición sobre una columna. `accion`: "excluir" descarta las filas que la cumplen; "incluir" deja solo esas.

    `valores` admite varios valores para ==, != y contiene (cumple si coincide con alguno; en != si no coincide con
    ninguno). `valor` se mantiene por compatibilidad y para las comparaciones numéricas.
    """
    columna: str
    operador: str
    valor: str = ""
    accion: str = "excluir"
    valores: list[str] = field(default_factory=list)

    def lista(self) -> list[str]:
        vals = [v.strip() for v in self.valores if str(v).strip()] or ([self.valor.strip()] if str(self.valor).strip() else [])
        return vals

    def mascara(self, df: pd.DataFrame) -> pd.Series:
        """True = la fila cumple la condición."""
        col = df[self.columna]
        vacia = col.isna() | (col.astype(str).str.strip() == "")
        if self.operador == "vacío":
            return vacia
        if self.operador == "no vacío":
            return ~vacia
        vals = self.lista()
        s = col.astype(str).str.strip()
        if self.operador == "contiene":
            m = pd.Series(False, index=df.index)
            for v in vals:
                m |= s.str.contains(v, case=False, na=False, regex=False)
            return m
        if self.operador in ("==", "!="):
            nums = [numero(v) for v in vals]
            if vals and all(n is not None for n in nums) and col.map(numero).notna().any():
                en = col.map(numero).isin(nums)  # 100 == 100.00
            else:
                en = s.isin(vals)
            return en if self.operador == "==" else ~en
        vn = numero(vals[0]) if vals else None
        if vn is None:
            return pd.Series(False, index=df.index)
        nums = col.map(numero)
        ops = {">": nums > vn, ">=": nums >= vn, "<": nums < vn, "<=": nums <= vn}
        return ops[self.operador].fillna(False).astype(bool)

    def descartar(self, df: pd.DataFrame) -> pd.Series:
        """True = la fila se descarta según la acción del filtro."""
        m = self.mascara(df)
        return m if self.accion != "incluir" else ~m


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
                m = f.descartar(df)
                excluidas += int(m.sum())
                df = df[~m]
        partes = [transformar_serie(df[c], self.transformacion) for c in self.llave]
        llave = partes[0]
        for p in partes[1:]:
            llave = llave + "|" + p
        df["_llave"] = llave
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


# ------------------------------------------------------------------ sugerencia de llaves (sin IA)

_NO_LLAVE = ("monto", "importe", "total", "fecha", "saldo", "valor", "alicuota", "alícuota", "ventas", "base",
             "diferencia", "email", "mail", "leyenda", "denominacion", "denominación", "nombre", "razon", "social")


def _muestra(serie: pd.Series, n: int = 3000) -> pd.Series:
    s = serie.head(n * 3).dropna().astype(str).str.strip()
    s = s[(s != "") & ~s.str.lower().isin(["nan", "none"])]
    return s.head(n)


def _perfil_digitos(serie: pd.Series) -> float:
    """Mediana de la cantidad de dígitos significativos (sin ceros a la izquierda) de una muestra."""
    s = _muestra(serie)
    if s.empty:
        return 0.0
    return float(s.str.replace(r"\D", "", regex=True).str.lstrip("0").str.len().median())


def _parece_llave(nombre: str, serie: pd.Series) -> bool:
    tokens = [t for t in re.split(r"[^a-záéíóúñ0-9]+", nombre.lower()) if t]
    if any(t in _NO_LLAVE for t in tokens):
        return False
    s = _muestra(serie, 500)
    if s.nunique() < 2:
        return False
    fechas = s.str.match(r"^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}").mean()
    decimales = s.str.match(r"^-?\d+[.,]\d{1,3}$").mean()
    return fechas < 0.5 and decimales < 0.5


def _cobertura(anchor: set, serie: pd.Series, tr: str, memo: Optional[dict] = None) -> float:
    """Contención entre los valores de la llave ancla y los de otra columna (la mayor de las dos direcciones)."""
    if not anchor:
        return 0.0
    clave = (id(serie), tr)
    if memo is not None and clave in memo:
        t = memo[clave]
    else:
        t = transformar_serie(serie.dropna(), tr)
        t = t[t != ""]
        if memo is not None:
            memo[clave] = t
    if not len(t):
        return 0.0
    k = pd.unique(t[t.isin(anchor)]).size  # valores distintos de la columna presentes en el ancla
    n = t.size if t.size > 200_000 else pd.unique(t).size  # columnas enormes: se aproxima por la cantidad de filas
    return max(k / len(anchor), k / n)


def puntaje_nombre(nombre: str) -> float:
    tokens = [t for t in re.split(r"[^a-z0-9]+", nombre.lower()) if t]
    ids = ("id", "nro", "numero", "num", "codigo", "cod", "clave", "llave", "doc", "documento", "comprobante",
           "identificador", "identification", "contribuyente", "cuenta", "poliza", "referencia", "ref", "fiscal", "cuit",
           "nif", "rut", "cnpj", "cpf", "rfc")
    return 1.0 if any(t in ids or t.startswith(("ident", "docu", "compro")) for t in tokens) else 0.0


def sugerir_llaves(tablas: list[pd.DataFrame]) -> list[Optional[tuple[str, str, float]]]:
    """Sugiere una llave por tabla: (columna, transformación, cobertura con la llave ancla).

    La primera tabla es el ancla. Para cada columna candidata del ancla y cada transformación se busca,
    en cada una de las demás tablas, la columna con mayor contención de valores (filtrando antes por el
    perfil de dígitos, para que un padrón de millones de filas no sea costoso). Gana la combinación que
    mejor vincula a todas las tablas; la misma transformación se aplica a todas para que las llaves sean
    comparables. Si ninguna columna vincula, se usa el nombre de la columna y la unicidad de sus valores.
    """
    if not tablas:
        return []
    cols = [[c for c in df.columns if not str(c).startswith("_") and _parece_llave(str(c), df[c])] for df in tablas]
    perfiles = [{c: _perfil_digitos(df[c]) for c in cs} for df, cs in zip(tablas, cols)]
    ancla = tablas[0]
    memo: dict = {}
    series = [{c: df[c] for c in cs} for df, cs in zip(tablas, cols)]
    mejor = None  # (puntaje, col_ancla, tr, [(col, cob) por tabla])
    for ca in sorted(cols[0], key=lambda c: -puntaje_nombre(str(c)))[:10]:
        for tr in ("exacta", "solo_numeros"):
            a_vals = set(pd.unique(transformar_serie(ancla[ca].dropna(), tr))) - {""}
            if len(a_vals) < 2:
                continue
            elegidas, total = [(ca, 1.0)], 0.0
            for sr, cs, pf in zip(series[1:], cols[1:], perfiles[1:]):
                cand = [c for c in cs if abs(pf[c] - perfiles[0][ca]) <= 1.5]
                cobs = [(c, _cobertura(a_vals, sr[c], tr, memo)) for c in cand]
                c_best = max(cobs, key=lambda x: (x[1], puntaje_nombre(str(x[0])))) if cobs else (None, 0.0)
                elegidas.append(c_best)
                total += c_best[1]
            puntaje = total + 0.01 * puntaje_nombre(str(ca)) - (0.001 if tr != "exacta" else 0)
            if mejor is None or puntaje > mejor[0] + 1e-9:
                mejor = (puntaje, ca, tr, elegidas)
    out: list[Optional[tuple[str, str, float]]] = []
    for i, df in enumerate(tablas):
        if mejor is not None and mejor[0] >= 0.3 and mejor[3][i][0] is not None and mejor[3][i][1] >= 0.3:
            out.append((mejor[3][i][0], mejor[2], mejor[3][i][1]))
            continue
        cs = cols[i] or [c for c in df.columns if not str(c).startswith("_")]
        if not cs:
            out.append(None)
            continue
        def score(c):
            s = _muestra(df[c])
            return (s.nunique() / max(1, len(s))) + puntaje_nombre(str(c))
        out.append((max(cs, key=score), "exacta", 0.0))
    return out
