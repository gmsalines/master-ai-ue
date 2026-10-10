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
import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import numpy as np
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
    t = re.sub(r"^([+-]?)[A-Za-z€]{1,3}(?=[\d+-])", r"\1", t)  # prefijo de moneda: R$, US$, U$S, €
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


def serie_numerica(s: pd.Series) -> pd.Series:
    """Versión vectorizada de `numero` (float; NaN si no es numérico). Acepta 1.234,56 · 1,234.56 · 12,5 · $ 100."""
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return s.astype(float)
    t0 = s.astype("string").str.strip()
    simples = t0.str.fullmatch(r"[+-]?\d+(\.\d+)?", na=False)
    vacios = t0.isna() | (t0 == "")
    if bool((simples | vacios).all()):  # vía rápida: números sin separador de miles ni moneda (la mayoría de los CSV)
        return t0.mask(vacios).astype("float64").astype(float)  # astype respeta los ceros a la izquierda
    t = t0.str.replace(r"[\s$]", "", regex=True)
    t = t.str.replace(r"^([+-]?)[A-Za-z€]{1,3}(?=[\d+-])", r"\1", regex=True)  # prefijo de moneda: R$, US$, €
    coma, punto = t.str.contains(",", regex=False), t.str.contains(".", regex=False)
    coma_ultima = t.str.rfind(",") > t.str.rfind(".")
    a = t.mask(coma & punto & coma_ultima, t.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    a = a.mask(coma & punto & ~coma_ultima, t.str.replace(",", "", regex=False))
    a = a.mask(coma & ~punto, t.str.replace(",", ".", regex=False))
    # sin ceros a la izquierda: pandas pierde los decimales en textos como "000000000000000280.38"
    a = a.str.replace(r"^([+-]?)0+(?=\d)", r"\1", regex=True)
    return pd.to_numeric(a, errors="coerce").astype(float)


def formatear_numero(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return (f"{float(v):.6f}").rstrip("0").rstrip(".")


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
            col_num = serie_numerica(col) if vals and all(n is not None for n in nums) else None
            if col_num is not None and col_num.notna().any():
                en = col_num.isin([float(n) for n in nums])  # 100 == 100.00
            else:
                en = s.isin(vals)
            return en if self.operador == "==" else ~en
        vn = numero(vals[0]) if vals else None
        if vn is None:
            return pd.Series(False, index=df.index)
        nums, vn = serie_numerica(col), float(vn)
        ops = {">": nums > vn, ">=": nums >= vn, "<": nums < vn, "<=": nums <= vn}
        return ops[self.operador].fillna(False).astype(bool)

    def descartar(self, df: pd.DataFrame) -> pd.Series:
        """True = la fila se descarta según la acción del filtro."""
        m = self.mascara(df)
        return m if self.accion != "incluir" else ~m


def describir_filtro_texto(f: Filtro) -> str:
    """Descripción del filtro para la columna «motivo» de las filas excluidas."""
    vals = " | ".join(f.lista())
    return f"{ACCIONES.get(f.accion, f.accion).rstrip('…')} {f.columna} {OPERADORES_TEXTO.get(f.operador, f.operador)} {vals}".strip()


# ------------------------------------------------------------------ agrupar y filtrar por total (GROUP BY … HAVING)

OPERACIONES = {"+": "suma", "-": "resta", "*": "multiplica por", "/": "divide por"}
SIMBOLOS = {"+": "+", "-": "−", "*": "×", "/": "÷"}
AGREGACIONES = {"suma": "Suma", "cantidad": "Cantidad de filas", "maximo": "Máximo", "minimo": "Mínimo",
                "promedio": "Promedio"}
COMPARADORES = [">", ">=", "<", "<="]
COMPARADORES_TEXTO = {">": "mayor que", ">=": "mayor o igual a", "<": "menor que", "<=": "menor o igual a"}
CONDICIONES = {"ninguna": "Sin condición (solo agrupar)", "unica": "Un umbral para todas las filas",
               "categoria": "Umbral según el valor de una columna"}
SALIDAS = {"filas": "Mantener las filas (se excluyen las de los grupos que no cumplen)",
           "resumir": "Resumir: una fila por grupo"}
ACOMPANAN = {"primero": "primer valor", "ultimo": "último valor", "distintos": "cantidad de valores distintos"}
COL_VALOR, COL_TOTAL, COL_UMBRAL, COL_CATEGORIA, COL_CUMPLE = (
    "valor calculado", "total del grupo", "umbral aplicado", "categoría", "cumple")
COLUMNAS_AUXILIARES = [COL_VALOR, COL_TOTAL, COL_UMBRAL, COL_CATEGORIA]
MOTIVO_UMBRAL, MOTIVO_SIN_UMBRAL, MOTIVO_DIV0 = (
    "el total del grupo no cumple el umbral", "categoría sin umbral definido", "división por cero")


@dataclass
class Termino:
    """Una columna de la fórmula. `operacion` la combina con lo anterior (se ignora en el primer término)."""
    columna: str
    operacion: str = "+"
    absoluto: bool = False


@dataclass
class Umbral:
    """Condición sobre el total del grupo. `valores`: categorías a las que se aplica (vacío = todas)."""
    comparador: str = ">"
    umbral: float = 0.0
    valores: list[str] = field(default_factory=list)

    def texto(self) -> str:
        return f"{self.comparador} {formatear_numero(self.umbral)}"


@dataclass
class Agregacion:
    """Columna calculada por fila, agregación por grupo y condición sobre el total (con umbral por categoría).

    1. valor = t1 op t2 op t3 … (de izquierda a derecha; cada término puede ir en valor absoluto; los vacíos valen 0);
    2. total = función(valor) por cada combinación de `agrupar_por`;
    3. condición: total <comparador> umbral, donde el umbral depende de la categoría de la fila (`categoria`).
       Las filas de los grupos que no cumplen se excluyen y quedan registradas con su motivo.
    4. salida: «filas» conserva las filas que cumplen; «resumir» deja una fila por grupo.
    """
    terminos: list[Termino] = field(default_factory=list)
    agrupar_por: list[str] = field(default_factory=list)
    funcion: str = "suma"
    condicion: str = "ninguna"           # ninguna | unica | categoria
    categoria: Optional[str] = None
    umbrales: list[Umbral] = field(default_factory=list)
    otros: str = "excluir"                # categorías sin umbral: excluir | sin_umbral
    salida: str = "filas"                 # filas | resumir
    acompanan: str = "primero"            # en «resumir», cómo se llevan las columnas que no son del grupo
    mostrar: list[str] = field(default_factory=lambda: list(COLUMNAS_AUXILIARES))

    def columnas(self) -> list[str]:
        """Columnas del archivo que usa la agregación (para avisar si faltan)."""
        cols = [t.columna for t in self.terminos] + list(self.agrupar_por)
        if self.condicion == "categoria" and self.categoria:
            cols.append(self.categoria)
        return list(dict.fromkeys(cols))

    def formula(self) -> str:
        if not self.terminos:
            return AGREGACIONES.get(self.funcion, self.funcion).lower()
        partes = []
        for i, t in enumerate(self.terminos):
            c = f"|{t.columna}|" if t.absoluto else t.columna
            partes.append(c if i == 0 else f"{SIMBOLOS.get(t.operacion, t.operacion)} {c}")
        cuerpo = " ".join(partes)
        return f"{AGREGACIONES.get(self.funcion, self.funcion).lower()}({cuerpo})" + \
            (f" por {', '.join(self.agrupar_por)}" if self.agrupar_por else "")

    def valor(self, df: pd.DataFrame) -> pd.Series:
        """Columna calculada, fila por fila. NaN solo donde hay una división por cero."""
        if not self.terminos:
            return pd.Series(1.0, index=df.index)
        v = None
        for t in self.terminos:
            x = serie_numerica(df[t.columna]).fillna(0.0) if t.columna in df.columns else pd.Series(0.0, index=df.index)
            if t.absoluto:
                x = x.abs()
            if v is None:
                v = x
            elif t.operacion == "-":
                v = v - x
            elif t.operacion == "*":
                v = v * x
            elif t.operacion == "/":
                v = v / x.where(x != 0)  # x/0 -> NaN (se excluye la fila con ese motivo)
            else:
                v = v + x
        return v.round(6)

    def _umbral_por_fila(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        """Comparador, umbral, texto del umbral y si la fila tiene umbral, según su categoría."""
        comp = pd.Series(None, index=df.index, dtype=object)
        umb = pd.Series(np.nan, index=df.index)
        txt = pd.Series("", index=df.index, dtype=object)
        if self.condicion == "unica" and self.umbrales:
            u = self.umbrales[0]
            comp[:], umb[:], txt[:] = u.comparador, float(u.umbral), u.texto()
        elif self.condicion == "categoria" and self.categoria in df.columns:
            # la categoría tiene pocos valores distintos: la coincidencia se evalúa sobre ellos y no fila por fila
            cat = df[self.categoria].astype("string").fillna("").str.strip()
            uniq = pd.DataFrame({self.categoria: pd.unique(cat)})
            libre_u = pd.Series(True, index=uniq.index)
            asignado = pd.Series(-1, index=uniq.index)
            for i, u in enumerate(self.umbrales):
                m = (Filtro(self.categoria, "==", valores=list(u.valores)).mascara(uniq) if u.valores
                     else pd.Series(True, index=uniq.index))  # sin valores: todas las categorías que no tengan otro umbral
                m = m & libre_u
                asignado[m] = i
                libre_u &= ~m
            idx = cat.map(dict(zip(uniq[self.categoria], asignado))).fillna(-1).astype(int).to_numpy()
            for i, u in enumerate(self.umbrales):
                m = idx == i
                if m.any():
                    comp[m], umb[m], txt[m] = u.comparador, float(u.umbral), u.texto()
        tiene = comp.notna()
        return comp, umb, txt, tiene

    def aplicar(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Devuelve (filas o grupos que cumplen, filas excluidas con su motivo, totales por grupo)."""
        grupo = [c for c in self.agrupar_por if c in df.columns]
        valor = self.valor(df)
        excl = []
        div0 = valor.isna()
        if div0.any():
            e = df[div0].copy()
            e["motivo"] = MOTIVO_DIV0
            excl.append(e)
            df, valor = df[~div0], valor[~div0]
        claves = [df[c].astype("string").fillna("").str.strip() for c in grupo]
        if claves:
            g = valor.groupby(claves, sort=False, dropna=False)
            fn = {"suma": "sum", "cantidad": "count", "maximo": "max", "minimo": "min", "promedio": "mean"}.get(self.funcion, "sum")
            total = g.transform(fn)
        else:  # sin columnas de grupo: todo el archivo es un grupo
            total = pd.Series(getattr(valor, {"cantidad": "count", "maximo": "max", "minimo": "min",
                                               "promedio": "mean"}.get(self.funcion, "sum"))(), index=df.index)
        total = total.astype(float).round(6)
        comp, umb, umbral_txt, tiene = self._umbral_por_fila(df)
        if self.condicion == "ninguna":
            cumple = pd.Series(True, index=df.index)
        else:
            t, u = total.to_numpy(), umb.to_numpy()
            c = comp.to_numpy()
            with np.errstate(invalid="ignore"):
                ok = np.select([c == ">", c == ">=", c == "<", c == "<="], [t > u, t >= u, t < u, t <= u], default=False)
            cumple = pd.Series(ok, index=df.index)
            if self.otros == "sin_umbral":
                cumple |= ~tiene
        cat = (df[self.categoria].astype("string").fillna("").str.strip()
               if self.condicion == "categoria" and self.categoria in df.columns else pd.Series("", index=df.index))

        # totales por grupo (siempre: es el control de lo que se aplicó)
        tot = pd.DataFrame({c: k.to_numpy() for c, k in zip(grupo, claves)})
        tot[COL_CATEGORIA], tot[COL_TOTAL], tot[COL_UMBRAL] = cat.to_numpy(), total.to_numpy(), umbral_txt.to_numpy()
        tot[COL_CUMPLE] = np.where(cumple.to_numpy(), "sí", "no")
        tot["filas"] = 1
        if self.condicion != "categoria":
            tot = tot.drop(columns=COL_CATEGORIA)
        llave_t = [c for c in tot.columns if c not in ("filas",)]
        totales = tot.groupby(llave_t, sort=False, dropna=False)["filas"].sum().reset_index()

        # filas excluidas por la condición
        no = ~cumple
        if no.any():
            e = df[no].copy()
            e["motivo"] = np.where((~tiene)[no].to_numpy(), MOTIVO_SIN_UMBRAL, MOTIVO_UMBRAL)
            e[COL_TOTAL], e[COL_UMBRAL] = total[no].to_numpy(), umbral_txt[no].to_numpy()
            excl.append(e)
        excluidas = pd.concat(excl, ignore_index=True) if excl else pd.DataFrame(columns=["motivo"])

        ok_df = df[cumple].copy(deep=False)
        aux = {COL_VALOR: valor[cumple], COL_TOTAL: total[cumple], COL_UMBRAL: umbral_txt[cumple], COL_CATEGORIA: cat[cumple]}
        if self.condicion != "categoria":
            aux.pop(COL_CATEGORIA)
        if self.condicion == "ninguna":
            aux.pop(COL_UMBRAL)
        if self.salida == "resumir" and grupo:
            ok_df = self._resumir(ok_df, grupo, aux)
        else:
            for k, v in aux.items():
                if k in self.mostrar:
                    ok_df[_libre(ok_df, k)] = v.to_numpy()
        return ok_df, excluidas, totales

    def _resumir(self, df: pd.DataFrame, grupo: list[str], aux: dict) -> pd.DataFrame:
        claves = [df[c].astype("string").fillna("").str.strip().rename(c) for c in grupo]
        resto = [c for c in df.columns if c not in grupo and not str(c).startswith("_")]
        g = df[resto].groupby(claves, sort=False, dropna=False) if resto else None
        if g is None:
            out = pd.DataFrame(index=pd.MultiIndex.from_arrays(claves).unique())
        elif self.acompanan == "ultimo":
            out = g.last()
        elif self.acompanan == "distintos":
            out = g.nunique()
        else:
            out = g.first()
        primeros = pd.DataFrame({k: v.to_numpy() for k, v in aux.items() if k != COL_VALOR},
                                index=df.index).groupby(claves, sort=False, dropna=False).first()
        for k in primeros.columns:
            if k in self.mostrar or k == COL_TOTAL:  # en un resumen el total siempre va: es el dato del grupo
                out[_libre(out, k)] = primeros[k]
        out["filas del grupo"] = pd.Series(1, index=df.index).groupby(claves, sort=False, dropna=False).sum()
        out = out.reset_index()
        return out


def _libre(df: pd.DataFrame, nombre: str) -> str:
    """Nombre de columna que no pisa una existente."""
    n, i = nombre, 2
    while n in df.columns:
        n, i = f"{nombre} ({i})", i + 1
    return n


@dataclass
class ArchivoGrupo:
    nombre: str
    df: pd.DataFrame
    llave: list[str]
    transformacion: str = "exacta"
    filtros: list[Filtro] = field(default_factory=list)
    traer: list[str] = field(default_factory=list)  # solo referencias en modo base_referencia
    agregacion: Optional[Agregacion] = None
    # solo referencias: columnas de la base (o traídas por una referencia anterior) con las que se busca esta
    # referencia; vacío = la llave de la base. Permite encadenar: padrón por identificador → tabla por letra.
    buscar_por: list[str] = field(default_factory=list)

    def preparado(self, excluidas_out: Optional[list] = None, totales_out: Optional[list] = None) -> tuple[pd.DataFrame, int]:
        """Filtros, agrupación con condición sobre el total y llave construida. Si se pasan las listas
        `excluidas_out` / `totales_out`, se agregan ahí las filas excluidas (con su motivo) y los totales por grupo."""
        df = self.df.copy(deep=False)  # sin duplicar los datos: solo se agregan columnas o se filtran filas
        excluidas = 0
        for f in self.filtros:
            if f.columna in df.columns:
                m = f.descartar(df)
                n = int(m.sum())
                excluidas += n
                if n and excluidas_out is not None:
                    e = df[m].copy()
                    e["motivo"] = describir_filtro_texto(f)
                    excluidas_out.append(_con_archivo(e, self.nombre))
                df = df[~m]
        if self.agregacion is not None:
            df, e, tot = self.agregacion.aplicar(df)
            excluidas += len(e)
            if excluidas_out is not None and len(e):
                excluidas_out.append(_con_archivo(e, self.nombre))
            if totales_out is not None:
                totales_out.append(_con_archivo(tot, self.nombre))
        partes = [transformar_serie(df[c], self.transformacion) if c in df.columns else pd.Series("", index=df.index, dtype=object)
                  for c in self.llave]
        llave = partes[0]
        for p in partes[1:]:
            llave = llave + "|" + p
        df["_llave"] = llave
        df["_archivo"] = self.nombre
        return df, excluidas

    def columnas_resultado(self) -> list[str]:
        """Columnas que tendrá el archivo después de la agrupación (para elegir importes a comparar)."""
        cols = [c for c in self.df.columns if not str(c).startswith("_")]
        a = self.agregacion
        if a is None:
            return cols
        aux = [c for c in COLUMNAS_AUXILIARES if c in a.mostrar or (a.salida == "resumir" and c == COL_TOTAL)]
        if a.condicion != "categoria":
            aux = [c for c in aux if c != COL_CATEGORIA]
        if a.condicion == "ninguna":
            aux = [c for c in aux if c != COL_UMBRAL]
        if a.salida == "resumir":
            aux = [c for c in aux if c != COL_VALOR] + ["filas del grupo"]
        return cols + [c for c in aux if c not in cols]


def _con_archivo(df: pd.DataFrame, nombre: str) -> pd.DataFrame:
    df = df.copy(deep=False)
    df.insert(0, "archivo", nombre) if "archivo" not in df.columns else None
    return df


def _llave_de(df: pd.DataFrame, cols: list[str], tipo: str) -> pd.Series:
    partes = [transformar_serie(df[c], tipo) for c in cols]
    k = partes[0]
    for x in partes[1:]:
        k = k + "|" + x
    return k


CONTROL_OK, CONTROL_DISTINTA, CONTROL_SIN_DATO = "coincide", "distinta", "sin dato"


@dataclass
class Control:
    """Compara dos columnas de cada fila del grupo (p. ej. la alícuota informada contra la que corresponde según una
    tabla de referencia). Números con tolerancia (01.50 = 1,5); si no son números, texto sin mayúsculas ni espacios.
    Estado por fila: «coincide», «distinta» o «sin dato» (falta alguno de los dos valores)."""
    columna_a: str
    columna_b: str
    tolerancia: float = 0.0
    nombre: str = ""

    def titulo(self) -> str:
        return f"control {self.nombre or f'{self.columna_a} = {self.columna_b}'}"

    def evaluar(self, df: pd.DataFrame) -> pd.Series:
        def limpio(s):
            t = s.astype("string").fillna("").str.strip()
            return t.mask(t.str.lower().isin(["nan", "none", "<na>"]), "")
        a, b = limpio(df[self.columna_a]), limpio(df[self.columna_b])
        na, nb = serie_numerica(a.mask(a == "")), serie_numerica(b.mask(b == ""))
        falta = (a == "") | (b == "")
        numerico = na.notna() & nb.notna()
        with np.errstate(invalid="ignore"):
            igual_num = ((na - nb).abs() <= float(self.tolerancia) + 1e-9).fillna(False)
        igual_txt = a.str.upper().str.replace(r"\s+", "", regex=True) == b.str.upper().str.replace(r"\s+", "", regex=True)
        igual = igual_num.where(numerico, igual_txt)
        estado = np.where(falta, CONTROL_SIN_DATO, np.where(igual.to_numpy(dtype=bool), CONTROL_OK, CONTROL_DISTINTA))
        return pd.Series(estado, index=df.index, dtype=object)


@dataclass
class Grupo:
    nombre: str
    archivos: list[ArchivoGrupo]
    modo: str = "concatenar"
    importes: list[str] = field(default_factory=list)  # columnas numéricas que se suman por llave
    controles: list["Control"] = field(default_factory=list)  # comparaciones entre dos columnas del grupo

    def construir(self, excluidas_out: Optional[list] = None, totales_out: Optional[list] = None) -> tuple[pd.DataFrame, dict]:
        """Devuelve el detalle del grupo (filas con _llave) y estadísticas. Las filas excluidas (con su motivo) y los
        totales por grupo de las agregaciones se agregan a `excluidas_out` y `totales_out`, si se pasan."""
        prep = [a.preparado(excluidas_out, totales_out) for a in self.archivos]
        stats = {"archivos": len(self.archivos), "excluidas por filtros": sum(e for _, e in prep)}
        for a in self.archivos:
            if a.agregacion is not None:
                stats[f"agregación en {a.nombre}"] = a.agregacion.formula()
        if self.modo == "base_referencia" and len(prep) > 1:
            base = prep[0][0]
            total_base = len(base)
            for (ref, _), arch in zip(prep[1:], self.archivos[1:]):
                cols = [c for c in (arch.traer or []) if c in ref.columns and c not in base.columns]
                ref_u = ref.drop_duplicates("_llave")[["_llave", *cols]]
                ind = f"_en_{arch.nombre}"
                por = [c for c in arch.buscar_por if c in base.columns]
                if por:  # búsqueda encadenada: la llave de la referencia contra otras columnas de la base
                    k = _llave_de(base, por, arch.transformacion)
                    base = base.assign(_buscar=k.to_numpy()).merge(ref_u.rename(columns={"_llave": "_buscar"}),
                                                                   on="_buscar", how="left", indicator=ind)
                    base = base.drop(columns="_buscar")
                    stats[f"{arch.nombre} buscada por"] = ", ".join(por)
                else:
                    base = base.merge(ref_u, on="_llave", how="left", indicator=ind)
                encontrados = int((base[ind] == "both").sum())
                stats[f"encontradas en {arch.nombre}"] = f"{encontrados} de {total_base} ({encontrados / max(1, total_base):.1%})"
                base = base.drop(columns=ind)
            detalle = base
        elif len(prep) == 1:  # un solo archivo: sin concatenar (con un millón de filas, una copia menos en memoria)
            detalle = prep[0][0]
            detalle.index = pd.RangeIndex(len(detalle))  # como pd.concat(..., ignore_index=True)
        else:
            detalle = pd.concat([p for p, _ in prep], ignore_index=True)
        es_vacia = detalle["_llave"].str.replace("|", "", regex=False) == ""
        vacias = int(es_vacia.sum())
        stats.update({"filas": len(detalle), "llaves únicas": detalle["_llave"].nunique(),
                      "filas con llave repetida": int(detalle["_llave"].duplicated(keep=False).sum()),
                      "llaves vacías (se excluyen del cruce)": vacias})
        if vacias:
            detalle = detalle[~es_vacia.to_numpy()]
        for c in self.controles:
            if c.columna_a in detalle.columns and c.columna_b in detalle.columns:
                estado = c.evaluar(detalle)
                detalle = detalle.assign(**{c.titulo(): estado.to_numpy()})
                cuenta = estado.value_counts()
                stats[f"{c.titulo()}: coincide / distinta / sin dato"] = " / ".join(
                    str(int(cuenta.get(e, 0))) for e in (CONTROL_OK, CONTROL_DISTINTA, CONTROL_SIN_DATO))
            else:
                faltan = [x for x in (c.columna_a, c.columna_b) if x not in detalle.columns]
                stats[f"{c.titulo()}: no se pudo evaluar"] = "faltan columnas: " + ", ".join(faltan)
        return detalle, stats

    def por_llave(self, detalle: pd.DataFrame) -> pd.DataFrame:
        """Una fila por llave: suma de importes, cantidad de filas y primeros valores del resto."""
        importes = [c for c in self.importes if c in detalle.columns]
        otros = [c for c in detalle.columns if c not in importes and c != "_llave"]
        g = detalle.groupby("_llave", sort=False)
        out = g[otros].first() if otros else pd.DataFrame(index=g.size().index)
        for c in importes:  # vectorizado: millones de filas en segundos
            out[c] = serie_numerica(detalle[c]).fillna(0.0).groupby(detalle["_llave"], sort=False).sum().round(6)
        out["_filas"] = g.size()
        return out


@dataclass
class ResultadoGrupos:
    matriz: pd.DataFrame          # una fila por llave: presencia en cada grupo + importes
    diferencias: pd.DataFrame
    resumen: dict
    estadisticas: dict
    detalles: dict                # nombre de grupo -> detalle
    excluidas: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["grupo", "archivo", "motivo"]))
    totales: pd.DataFrame = field(default_factory=pd.DataFrame)  # totales por grupo de las agregaciones
    controles: pd.DataFrame = field(default_factory=pd.DataFrame)  # filas que no pasan un control (distinta o sin dato)

    def a_excel(self, max_filas: int = 200_000, hojas_sel: Optional[list[str]] = None) -> bytes:
        """Excel del resultado. Cada hoja se limita a `max_filas` (Excel admite ~1 millón y escribir más es muy lento);
        si se recorta, se indica en la hoja Resumen.

        Se escribe fila a fila con un libro de solo escritura de openpyxl: con resultados de un millón de llaves,
        armar el libro entero en memoria (como hace pandas.to_excel) se quedaba sin memoria."""
        from openpyxl import Workbook

        sel = set(hojas_sel) if hojas_sel else set(HOJAS_EXCEL)
        hojas = ([("Cruce por llave", self.matriz)] if "cruce" in sel else []) + \
                ([("Diferencias", self.diferencias)] if "diferencias" in sel else []) + \
                ([(f"Detalle {g}", d) for g, d in self.detalles.items()] if "detalle" in sel else []) + \
                ([("Excluidos", self.excluidas)] if "excluidos" in sel else []) + \
                ([("Totales por grupo", self.totales)] if "totales" in sel and len(self.totales) else []) + \
                ([("Controles", self.controles)] if "controles" in sel and len(self.controles.columns) else [])
        recortes = [{"indicador": f"hoja «{n}» recortada", "valor": f"primeras {max_filas} de {len(df)} filas"}
                    for n, df in hojas if len(df) > max_filas]
        resumen = pd.DataFrame([{"indicador": k, "valor": str(v)} for k, v in self.resumen.items()] + recortes)
        grupos = pd.DataFrame([{"grupo": g, "indicador": k, "valor": str(v)}
                               for g, st in self.estadisticas.items() for k, v in st.items()])

        wb = Workbook(write_only=True)

        def hoja(nombre: str, df: pd.DataFrame, tope: Optional[int] = max_filas) -> None:
            ws = wb.create_sheet(nombre[:31])
            ws.append([str(c) for c in df.columns])
            for fila in (df if tope is None else df.head(tope)).itertuples(index=False, name=None):
                ws.append([_valor_excel(v) for v in fila])

        if "resumen" in sel or not (sel - {"resumen"}):
            hoja("Resumen", resumen, None)
        if "resumen" in sel:
            hoja("Grupos", grupos, None)
        for n, df in hojas:
            hoja(n, df)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()


HOJAS_EXCEL = {"resumen": "Resumen y estadísticas por grupo", "cruce": "Cruce por llave", "diferencias": "Diferencias",
               "detalle": "Detalle de cada grupo", "excluidos": "Excluidos (con el motivo)",
               "totales": "Totales por grupo (agregaciones)", "controles": "Controles (filas que no coinciden)"}


def _valor_excel(v):
    """Valor de una celda: Decimal y tipos de numpy a tipos de Python; vacíos (NaN, None, NA) a celda vacía."""
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (float, np.floating)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def cruzar_grupos(grupos: list[Grupo], comparar: list[tuple[str, ...]] = (), tolerancia: Decimal = Decimal("0.01")) -> ResultadoGrupos:
    """Cruza N grupos por la llave construida.

    `comparar`: tuplas con una columna de importe por grupo (en el orden de `grupos`), p. ej.
    [("monto_retenido", "Monto Retenido")]. Se comparan los importes sumados por llave.
    """
    detalles, estad, por = {}, {}, {}
    excl_todas, tot_todos = [], []
    for g in grupos:
        excl_g, tot_g = [], []
        det, st = g.construir(excl_g, tot_g)
        excl_todas += [e.assign(grupo=g.nombre) for e in excl_g]
        tot_todos += [t.assign(grupo=g.nombre) for t in tot_g]
        detalles[g.nombre], estad[g.nombre] = det, st
        g_imp = set(g.importes) | {t[i] for t in comparar for i, gg in enumerate(grupos) if gg is g and i < len(t)}
        g.importes = [c for c in g_imp if c in det.columns]
        por[g.nombre] = g.por_llave(det)
    universo = pd.Index(pd.unique(np.concatenate([p.index.to_numpy(dtype=object) for p in por.values()]))).sort_values()
    universo.name = "llave"
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
    # estado vectorizado: cada combinación de presencia es un código binario
    pres = presencia.to_numpy(dtype=bool)
    codigos = pres.astype(np.int64) @ (1 << np.arange(len(grupos), dtype=np.int64))
    etiquetas = {}
    for c in np.unique(codigos):
        nombres_c = [g.nombre for i, g in enumerate(grupos) if (int(c) >> i) & 1]
        etiquetas[int(c)] = "en todos" if len(nombres_c) == len(grupos) else "solo en " + " + ".join(nombres_c)
    m["estado"] = pd.Series(codigos, index=m.index).map(etiquetas)

    tol = float(tolerancia)
    textos: dict = {}
    en_todos = m["en todos"].to_numpy()
    for t in comparar:
        cols = [(g.nombre, t[i], f"{g.nombre}.{t[i]}") for i, g in enumerate(grupos) if i < len(t) and f"{g.nombre}.{t[i]}" in m.columns]
        if len(cols) < 2:
            continue
        arr = m[[c for _, _, c in cols]].to_numpy(dtype=float)
        validos = (~np.isnan(arr)).sum(axis=1) >= 2
        with np.errstate(all="ignore"):
            dif = np.round(np.nanmax(arr, axis=1) - np.nanmin(arr, axis=1), 6)
        malas = np.flatnonzero(en_todos & validos & (dif > tol + 1e-9))
        for k in malas:
            texto = " vs ".join(f"{g}.{c}={formatear_numero(arr[k, j])}" for j, (g, c, _) in enumerate(cols)) + \
                f" (dif. {formatear_numero(dif[k])})"
            textos.setdefault(m.index[k], []).append(texto)
    difs = [{"llave": k, "diferencias": "; ".join(v)} for k, v in sorted(textos.items())]
    m["con diferencias"] = m.index.isin(list(textos))
    matriz = m.reset_index()
    resumen = {"llaves en el universo": len(universo), "en todos los grupos": int(m["en todos"].sum()),
               "en todos, con diferencias": len(difs), "en todos, iguales": int(m["en todos"].sum()) - len(difs),
               "tolerancia": str(tolerancia)}
    for g in grupos:
        solo = presencia[f"en {g.nombre}"] & ~presencia.drop(columns=f"en {g.nombre}").any(axis=1)
        resumen[f"solo en {g.nombre}"] = int(solo.sum())
        resumen[f"cobertura de {g.nombre} (llaves presentes en todos los demás)"] = (
            f"{int(m.loc[presencia[f'en {g.nombre}'], 'en todos'].sum())} de {int(presencia[f'en {g.nombre}'].sum())}")
    excluidas = _primero(pd.concat(excl_todas, ignore_index=True) if excl_todas else
                         pd.DataFrame(columns=["grupo", "archivo", "motivo"]), ["grupo", "archivo", "motivo"])
    totales = _primero(pd.concat(tot_todos, ignore_index=True), ["grupo", "archivo"]) if tot_todos else pd.DataFrame()
    ctl = []
    for g in grupos:
        det = detalles[g.nombre]
        for c in g.controles:
            if c.titulo() in det.columns:
                malas = det[det[c.titulo()] != CONTROL_OK]
                cols = [x for x in malas.columns if not str(x).startswith("_") and x != c.titulo()]
                ctl.append(malas[cols].assign(grupo=g.nombre, control=c.titulo(), estado=malas[c.titulo()].to_numpy(),
                                              llave=malas["_llave"].to_numpy()))
    controles = (_primero(pd.concat(ctl, ignore_index=True), ["grupo", "control", "estado", "llave"]) if ctl else
                 pd.DataFrame(columns=["grupo", "control", "estado", "llave"]) if any(g.controles for g in grupos) else pd.DataFrame())
    resumen["filas excluidas (filtros y agregaciones)"] = len(excluidas)
    if any(g.controles for g in grupos):
        resumen["filas que no pasan los controles"] = len(controles)
    return ResultadoGrupos(matriz, pd.DataFrame(difs, columns=["llave", "diferencias"]), resumen, estad, detalles,
                           excluidas, totales, controles)


def _primero(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Reordena para que `cols` vayan primero (las que existan)."""
    adelante = [c for c in cols if c in df.columns]
    return df[adelante + [c for c in df.columns if c not in adelante]]


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
    # importes: 12,5 · 1234.56 · 1.234,56 · 1,234.56 · R$ 1.234,56
    decimales = s.str.match(r"^-?\d+[.,]\d{1,3}$|^[-+]?[^\d\s]{0,3}\s?\d{1,3}([.,]\d{3})+[.,]\d{2}$").mean()
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


SOLIDEZ_MINIMA = 0.5  # por debajo, la llave no se considera clara (código) ni plausible (LLM)


def _unicidad(serie: pd.Series) -> float:
    """Proporción de valores distintos en una muestra: una llave identifica registros (≈1); un atributo compartido
    por muchos registros (cliente, sucursal, estado) tiene unicidad baja aunque sus valores coincidan entre archivos."""
    s = _muestra(serie)
    return s.nunique() / max(1, len(s))


def _factor_unicidad(u: float) -> float:
    """Penaliza columnas poco únicas: 1 si al menos la mitad de los valores son distintos; proporcional por debajo."""
    return min(1.0, u / 0.5)


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
    perfil de dígitos, para que un padrón de millones de filas no sea costoso). La contención se pondera
    por la unicidad de las columnas: un atributo compartido (cliente, sucursal) puede coincidir al 100 %
    sin identificar registros. Gana la combinación que mejor vincula a todas las tablas; la misma
    transformación se aplica a todas para que las llaves sean comparables. El tercer valor devuelto es la
    solidez de la llave (contención × factor de unicidad): por debajo de SOLIDEZ_MINIMA la llave no es clara y la app
    ofrece consultar al LLM. Si ninguna columna vincula, se usa el nombre de la columna y la unicidad.
    """
    if not tablas:
        return []
    cols = [[c for c in df.columns if not str(c).startswith("_") and _parece_llave(str(c), df[c])] for df in tablas]
    perfiles = [{c: _perfil_digitos(df[c]) for c in cs} for df, cs in zip(tablas, cols)]
    ancla = tablas[0]
    memo: dict = {}
    series = [{c: df[c] for c in cs} for df, cs in zip(tablas, cols)]
    unic = [{c: _factor_unicidad(_unicidad(df[c])) for c in cs} for df, cs in zip(tablas, cols)]
    mejor = None  # (puntaje, col_ancla, tr, [(col, cob) por tabla])
    for ca in sorted(cols[0], key=lambda c: -puntaje_nombre(str(c)))[:10]:
        for tr in ("exacta", "solo_numeros"):
            a_vals = set(pd.unique(transformar_serie(ancla[ca].dropna(), tr))) - {""}
            if len(a_vals) < 2:
                continue
            fa = unic[0][ca]
            elegidas, total = [(ca, fa)], 0.0
            for sr, cs, pf, un in zip(series[1:], cols[1:], perfiles[1:], unic[1:]):
                cand = [c for c in cs if abs(pf[c] - perfiles[0][ca]) <= 1.5]
                cobs = [(c, _cobertura(a_vals, sr[c], tr, memo) * min(fa, un[c])) for c in cand]
                c_best = max(cobs, key=lambda x: (x[1], puntaje_nombre(str(x[0])))) if cobs else (None, 0.0)
                elegidas.append(c_best)
                total += c_best[1]
            puntaje = total + 0.01 * puntaje_nombre(str(ca)) - (0.001 if tr != "exacta" else 0)
            if mejor is None or puntaje > mejor[0] + 1e-9:
                mejor = (puntaje, ca, tr, elegidas)
    out: list[Optional[tuple[str, str, float]]] = []
    for i, df in enumerate(tablas):
        if mejor is not None and mejor[0] >= SOLIDEZ_MINIMA and mejor[3][i][0] is not None and mejor[3][i][1] >= SOLIDEZ_MINIMA:
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


# ------------------------------------------------------------------ llave con IA: respaldo validado del código (varias tablas)


def llaves_claras(sugeridas: list, umbral: float = SOLIDEZ_MINIMA) -> bool:
    """El código encontró una llave clara si hay sugerencia para todas las tablas y la solidez de cada una
    (salvo el ancla) alcanza `umbral`. Si no, la app ofrece el respaldo con IA."""
    if len(sugeridas) < 2 or any(s is None for s in sugeridas):
        return False
    return all(s[2] >= umbral for s in sugeridas[1:])


def _llave_compuesta(df: pd.DataFrame, cols: list[str], tr: str) -> pd.Series:
    partes = [transformar_serie(df[c], tr) for c in cols]
    k = partes[0]
    for p in partes[1:]:
        k = k + "|" + p
    return k[k.str.strip("|") != ""]


def solidez_llave(ancla: pd.DataFrame, cols_ancla: list[str], df: pd.DataFrame, cols: list[str], tr: str) -> float:
    """Mismo criterio que la sugerencia por código: contención de valores entre las llaves × factor de unicidad."""
    a, b = _llave_compuesta(ancla, cols_ancla, tr), _llave_compuesta(df, cols, tr)
    ka, kb = set(pd.unique(a)), set(pd.unique(b))
    if not ka or not kb:
        return 0.0
    contencion = len(ka & kb) / min(len(ka), len(kb))
    return contencion * min(_factor_unicidad(_unicidad(a)), _factor_unicidad(_unicidad(b)))


def sugerir_llaves_ia(tablas: list[pd.DataFrame], proveedor, nombres: Optional[list[str]] = None, muestras: int = 5,
                      umbral: float = SOLIDEZ_MINIMA) -> dict:
    """Pide al LLM la llave de cada tabla cuando el código no encontró una clara. Respaldo **validado**:

    1. al LLM solo se le envían nombres de columna y `muestras` valores de ejemplo por columna;
    2. se descartan columnas inventadas y normalizaciones desconocidas;
    3. la llave propuesta se comprueba con el mismo criterio que el código: una tabla queda con la llave de la IA
       solo si su solidez frente al ancla (contención × unicidad) alcanza `umbral`.

    Devuelve {"llaves": [(columnas, transformacion, solidez) | None por tabla], "validada": bool,
    "motivo": str, "tokens": int}.
    """
    nombres = nombres or [f"tabla_{i + 1}" for i in range(len(tablas))]

    def perfil(df):
        return {str(c): [str(v)[:40] for v in _muestra(df[c], muestras).head(muestras)]
                for c in df.columns if not str(c).startswith("_")}

    tablas_txt = "\n".join(f"{i}. {n}: {json.dumps(perfil(df), ensure_ascii=False)}"
                           for i, (n, df) in enumerate(zip(nombres, tablas)))
    pedido = (
        "Quiero conciliar estas tablas: hay que encontrar en cada una la(s) columna(s) que identifican el mismo "
        "registro (la llave), para poder cruzarlas. Columnas con ejemplos de valores:\n"
        f"{tablas_txt}\n"
        "Todas las llaves deben tener la misma cantidad de columnas y el mismo significado, en el mismo orden. "
        f"Normalizaciones posibles: {', '.join(TRANSFORMACIONES)} (una sola, común a todas). "
        'Respondé SOLO JSON: {"llaves": [["col", ...] por tabla, en orden], "normalizacion": "…", "motivo": "breve"}. '
        "Usá exactamente los nombres de columna dados."
    )
    r = proveedor.completar([{"role": "user", "content": pedido}], modo_json=True, temperatura=0.0, max_tokens=800)
    t = r.texto or ""
    try:
        d = json.loads(t[t.find("{"): t.rfind("}") + 1])
    except ValueError:
        d = {}
    d = d if isinstance(d, dict) else {}
    tokens = int(getattr(r, "tokens_entrada", 0) or 0) + int(getattr(r, "tokens_salida", 0) or 0)
    tr = d.get("normalizacion") if d.get("normalizacion") in TRANSFORMACIONES else "exacta"
    propuestas = list(d.get("llaves") or [])
    propuestas += [[]] * (len(tablas) - len(propuestas))
    cols = []
    for df, p in zip(tablas, propuestas):
        p = [p] if isinstance(p, str) else list(p or [])
        cols.append([c for c in p if c in df.columns])
    n = len(cols[0])
    out: list = [None] * len(tablas)
    if n and all(len(c) == n for c in cols):
        def evaluar(tr_):
            sols = [solidez_llave(tablas[0], cols[0], df, c, tr_) for df, c in zip(tablas[1:], cols[1:])]
            return [(cols[0], tr_, 1.0)] + [(c, tr_, s) if s >= umbral else None for c, s in zip(cols[1:], sols)]
        out = evaluar(tr)
        if tr == "exacta" and any(x is None for x in out):  # segunda oportunidad: la misma llave, solo números
            alt = evaluar("solo_numeros")
            if sum(x is not None for x in alt) > sum(x is not None for x in out):
                out = alt
    validada = all(x is not None for x in out)
    return {"llaves": out, "validada": validada, "motivo": str(d.get("motivo", ""))[:300], "tokens": tokens}

