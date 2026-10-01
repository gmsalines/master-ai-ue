"""Sugerencia de llave con un modelo aprendido y ajuste por uso.

La heurística de `grupos.sugerir_llaves` puntúa cada par de columnas con una fórmula fija (contención × unicidad).
Aquí la misma decisión la toma un modelo de regresión logística entrenado con ejemplos:

* cada **candidato** es un par (columna del archivo ancla, columna de otro archivo, normalización);
* sus **características** describen el par sin depender del dominio: contención de valores en ambos sentidos,
  unicidad, perfil de dígitos, si los valores parecen una numeración de filas, parecido de los nombres y
  las sílabas iniciales de cada palabra del nombre (hash en pocas dimensiones);
* la **etiqueta** es 1 si el par es la llave correcta.

**Aprendizaje por uso.** El modelo base se entrena con casos sintéticos. Cada vez que el usuario confirma un cruce,
las llaves que eligió (aceptando la sugerencia o corrigiéndola) se convierten en ejemplos etiquetados: el par elegido
es positivo y el resto de candidatos, negativos. El modelo se reentrena con los ejemplos base más los del usuario,
con más peso. A diferencia de la memoria de configuración, que solo reconoce archivos con la misma estructura
(firma de columnas), lo aprendido aquí se aplica a archivos con otra estructura que compartan el patrón.

La elección sigue pasando por la validación de solidez de `grupos` (contención × factor de unicidad ≥ 0,5):
el modelo decide qué par proponer, pero una llave que no vincula los archivos no se presenta como clara.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .grupos import (_cobertura, _factor_unicidad, _muestra, _parece_llave, _perfil_digitos, _unicidad,
                     puntaje_nombre, transformar_serie)

TRANSFORMACIONES_ML = ("exacta", "solo_numeros")
N_HASH = 16
NUMERICAS = ["cob_max", "cob_ancla", "cob_otra", "unic_ancla", "unic_otra", "solidez", "parece_ancla", "parece_otra",
             "id_ancla", "id_otra", "sim_nombre", "digitos", "dif_digitos", "secuencial_ancla", "secuencial_otra",
             "solo_numeros"]
NOMBRES_CARACTERISTICAS = NUMERICAS + [f"silaba_{i}" for i in range(N_HASH)]
RUTA_BASE = Path(__file__).parent / "modelos" / "llave_base.json"
RUTA_DATOS = Path(__file__).parent / "modelos" / "llave_base_datos.npz"  # ejemplos con que se entrenó (para reentrenar)
MAX_CRUCES_USUARIO = 200  # se conservan los últimos cruces confirmados de cada usuario
PESO_USUARIO = 5.0  # cada ejemplo del usuario pesa como 5 ejemplos base
MAX_COLUMNAS = 15
MAX_FILAS_MODELO = 300_000  # con tablas más grandes (p. ej. un padrón) se usa la heurística, que es más liviana


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _silabas(nombre: str) -> list[str]:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(nombre)).encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.split(r"[^a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", s)) if len(t) >= 2]
    return [t[:3] for t in toks]


def _hash(t: str) -> int:
    return int(hashlib.md5(t.encode()).hexdigest(), 16) % N_HASH


def _secuencial(serie: pd.Series) -> float:
    """1 si los valores parecen una numeración de filas: enteros casi consecutivos sin huecos."""
    s = _muestra(serie, 5000)
    if s.empty or not s.str.fullmatch(r"\d{1,7}").all():
        return 0.0
    v = pd.to_numeric(s).drop_duplicates()
    if len(v) < 10:
        return 0.0
    rango = v.max() - v.min() + 1
    return 1.0 if rango / len(v) <= 1.1 else 0.0


@dataclass
class Candidato:
    tabla: int  # índice de la tabla (1..N-1) que se vincula con el ancla
    col_ancla: str
    col: str
    transformacion: str
    x: np.ndarray
    solidez: float


class _Perfil:
    """Datos por columna que se reutilizan entre candidatos."""

    def __init__(self, df: pd.DataFrame):
        cols = [c for c in df.columns if not str(c).startswith("_")]
        cand = [c for c in cols if _parece_llave(str(c), df[c])]
        self.cols = (cand or cols)[:MAX_COLUMNAS]
        self.df = df
        self.digitos = {c: _perfil_digitos(df[c]) for c in self.cols}
        self.unic = {c: _unicidad(df[c]) for c in self.cols}
        self.parece = {c: float(c in cand) for c in self.cols}
        self.ident = {c: puntaje_nombre(str(c)) for c in self.cols}
        self.sec = {c: _secuencial(df[c]) for c in self.cols}
        self.silabas = {c: _silabas(c) for c in self.cols}


def candidatos(tablas: list[pd.DataFrame], perfiles: Optional[list[_Perfil]] = None) -> list[Candidato]:
    """Todos los pares (columna del ancla, columna de cada otra tabla, normalización) con su vector de características."""
    if len(tablas) < 2:
        return []
    perfiles = perfiles or [_Perfil(df) for df in tablas]
    pa = perfiles[0]
    memo: dict = {}
    out = []
    for ca in pa.cols:
        for tr in TRANSFORMACIONES_ML:
            a_vals = set(pd.unique(transformar_serie(pa.df[ca].dropna(), tr))) - {""}
            if len(a_vals) < 2:
                continue
            for ti, pb in enumerate(perfiles[1:], start=1):
                for cb in pb.cols:
                    if abs(pb.digitos[cb] - pa.digitos[ca]) > 3:
                        continue  # perfiles de dígitos incompatibles: no pueden coincidir
                    serie = pb.df[cb]
                    t = memo.get((ti, cb, tr))
                    if t is None:
                        t = transformar_serie(serie.dropna(), tr)
                        t = t[t != ""]
                        memo[(ti, cb, tr)] = t
                    if not len(t):
                        continue
                    ub = pd.unique(t)
                    k = int(pd.Series(ub).isin(a_vals).sum())
                    if k == 0:
                        continue
                    cob_ancla, cob_otra = k / len(a_vals), k / max(1, len(ub))
                    cob = max(cob_ancla, cob_otra)
                    solidez = cob * min(_factor_unicidad(pa.unic[ca]), _factor_unicidad(pb.unic[cb]))
                    num = [cob, cob_ancla, cob_otra, pa.unic[ca], pb.unic[cb], solidez, pa.parece[ca], pb.parece[cb],
                           pa.ident[ca], pb.ident[cb],
                           difflib.SequenceMatcher(None, _norm(ca), _norm(cb)).ratio(),
                           min(pa.digitos[ca], 20) / 20, min(abs(pa.digitos[ca] - pb.digitos[cb]), 10) / 10,
                           pa.sec[ca], pb.sec[cb], float(tr == "solo_numeros")]
                    h = np.zeros(N_HASH)
                    for s in pa.silabas[ca] + pb.silabas[cb]:
                        h[_hash(s)] = 1.0
                    out.append(Candidato(ti, ca, cb, tr, np.concatenate([num, h]), solidez))
    return out


# ------------------------------------------------------------------ modelo


@dataclass
class ModeloLlave:
    coef: np.ndarray
    intercepto: float
    n_base: int = 0
    n_usuario: int = 0

    def probas(self, X: np.ndarray) -> np.ndarray:
        return 1 / (1 + np.exp(-(X @ self.coef + self.intercepto)))

    def a_dict(self) -> dict:
        return {"caracteristicas": NOMBRES_CARACTERISTICAS, "coef": [round(float(c), 6) for c in self.coef],
                "intercepto": round(float(self.intercepto), 6), "n_base": self.n_base, "n_usuario": self.n_usuario}

    @classmethod
    def de_dict(cls, d: dict) -> "ModeloLlave":
        if d.get("caracteristicas") != NOMBRES_CARACTERISTICAS:
            raise ValueError("el modelo guardado no corresponde a estas características")
        return cls(np.array(d["coef"], dtype=float), float(d["intercepto"]), d.get("n_base", 0), d.get("n_usuario", 0))

    def guardar(self, ruta: Path | str = RUTA_BASE) -> None:
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        Path(ruta).write_text(json.dumps(self.a_dict(), indent=1), encoding="utf-8")


def entrenar(X: np.ndarray, y: np.ndarray, peso: Optional[np.ndarray] = None, C: float = 1.0) -> ModeloLlave:
    from sklearn.linear_model import LogisticRegression
    m = LogisticRegression(C=C, class_weight="balanced", max_iter=5000)
    m.fit(X, y, sample_weight=peso)
    return ModeloLlave(m.coef_[0].copy(), float(m.intercept_[0]), n_base=int(len(y)))


def modelo_base() -> Optional[ModeloLlave]:
    try:
        return ModeloLlave.de_dict(json.loads(RUTA_BASE.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


def datos_base() -> tuple[np.ndarray, np.ndarray]:
    d = np.load(RUTA_DATOS)
    return d["X"], d["y"]


def modelo_usuario(uso: list[dict]) -> Optional[ModeloLlave]:
    """Modelo base reentrenado con los cruces confirmados del usuario; el base si todavía no hay uso."""
    uso = [e for e in (uso or []) if e.get("X")][-MAX_CRUCES_USUARIO:]
    if not uso:
        return modelo_base()
    try:
        return ajustar(*datos_base(), uso)
    except Exception:  # noqa: BLE001 (sin scikit-learn o sin datos base: se usa el modelo base)
        return modelo_base()


# ------------------------------------------------------------------ ejemplos etiquetados


def ejemplos(cands: list[Candidato], correctas: dict[int, tuple[str, str]],
             tr_validas: Optional[dict[int, set]] = None) -> tuple[np.ndarray, np.ndarray]:
    """Etiqueta los candidatos: 1 si (col_ancla, col) es la llave elegida para esa tabla (y la normalización vale)."""
    X, y = [], []
    for c in cands:
        ok = correctas.get(c.tabla) == (c.col_ancla, c.col)
        if ok and tr_validas is not None:
            ok = c.transformacion in tr_validas.get(c.tabla, {c.transformacion})
        X.append(c.x)
        y.append(int(ok))
    return (np.array(X), np.array(y)) if X else (np.zeros((0, len(NOMBRES_CARACTERISTICAS))), np.zeros(0, dtype=int))


def ejemplos_de_cruce(tablas: list[pd.DataFrame], llaves: list[Optional[str]], transformacion: str) -> dict:
    """Ejemplos de uso a partir de un cruce confirmado: la llave elegida en cada tabla (una columna) es el positivo.

    Devuelve {"X": [...], "y": [...]} listo para guardar en JSON, o vacío si las llaves no son de una columna."""
    if (len(tablas) < 2 or not llaves or any(ll is None for ll in llaves)
            or max(len(t) for t in tablas) > MAX_FILAS_MODELO):
        return {"X": [], "y": []}
    cands = candidatos(tablas)
    correctas = {i: (llaves[0], llaves[i]) for i in range(1, len(tablas))}
    X, y = ejemplos(cands, correctas, {i: {transformacion} for i in range(1, len(tablas))})
    if not y.sum():
        return {"X": [], "y": []}  # la llave elegida no es candidata (p. ej. columnas sin valores en común)
    return {"X": np.round(X, 4).tolist(), "y": y.tolist()}


def ajustar(base_X: np.ndarray, base_y: np.ndarray, usuario: list[dict], peso_usuario: float = PESO_USUARIO) -> ModeloLlave:
    """Reentrena con los ejemplos base más los de uso del usuario (con más peso)."""
    Xu = [np.array(e["X"], dtype=float) for e in usuario if e.get("X")]
    yu = [np.array(e["y"], dtype=int) for e in usuario if e.get("y")]
    if not Xu:
        return entrenar(base_X, base_y)
    X = np.vstack([base_X, *Xu])
    y = np.concatenate([base_y, *yu])
    w = np.concatenate([np.ones(len(base_y)), np.full(sum(len(v) for v in yu), peso_usuario)])
    m = entrenar(X, y, w)
    m.n_base, m.n_usuario = int(len(base_y)), int(sum(len(v) for v in yu))
    return m


# ------------------------------------------------------------------ sugerencia


def sugerir_llaves_modelo(tablas: list[pd.DataFrame], modelo: ModeloLlave) -> list[Optional[tuple[str, str, float]]]:
    """Misma salida que `grupos.sugerir_llaves`: (columna, normalización, solidez) por tabla.

    Elige la columna del ancla y la normalización que maximizan la probabilidad sumada de la mejor columna de cada
    otra tabla; la solidez es la de la heurística (contención × factor de unicidad), así que la validación y el
    respaldo con IA funcionan igual."""
    from .grupos import sugerir_llaves
    if len(tablas) < 2 or max(len(t) for t in tablas) > MAX_FILAS_MODELO:
        return sugerir_llaves(tablas)
    cands = candidatos(tablas)
    if not cands:
        return sugerir_llaves(tablas)
    p = modelo.probas(np.array([c.x for c in cands]))
    mejor = {}  # (col_ancla, tr) -> {tabla: (proba, cand)}
    for c, pc in zip(cands, p):
        d = mejor.setdefault((c.col_ancla, c.transformacion), {})
        if c.tabla not in d or pc > d[c.tabla][0]:
            d[c.tabla] = (pc, c)
    n = len(tablas) - 1
    total = {k: sum(v[0] for v in d.values()) for k, d in mejor.items() if len(d) == n}
    if not total:
        return sugerir_llaves(tablas)
    (ca, tr), _ = max(total.items(), key=lambda kv: kv[1])
    elegidos = mejor[(ca, tr)]
    perfil_ancla = _factor_unicidad(_unicidad(tablas[0][ca]))
    out: list[Optional[tuple[str, str, float]]] = [(ca, tr, perfil_ancla)]
    for i in range(1, len(tablas)):
        c = elegidos[i][1]
        out.append((c.col, tr, round(c.solidez, 3)))
    return out
