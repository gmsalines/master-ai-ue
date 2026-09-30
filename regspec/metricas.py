"""Métricas de uso para el capítulo de resultados.

Se calculan a partir de los eventos que deja cada «Cruzar» (tabla `evento_cruce` o `eventos.jsonl` en local) y de la
memoria de configuración. Responden a las preguntas de la evaluación:

* ¿Cuántas veces se reutilizó lo aprendido y con qué resultado? (aceptada sin cambios / corregida)
* ¿Cuánto tiempo de preparación se ahorra al partir de una configuración aprendida o guardada frente a armarla a mano?
* ¿Cuándo hizo falta la IA para la llave, cuántas veces su propuesta pasó la validación y cuántos tokens costó?
"""
from __future__ import annotations

import io
from typing import Optional

import pandas as pd

ORIGENES = {
    "heuristica": "Armado a mano (sugerencia por código)",
    "ia": "Llave sugerida por IA (validada)",
    "memoria": "Configuración aprendida (ofrecida)",
    "memoria_auto": "Configuración aprendida (aplicada sola, > 95 %)",
    "guardado": "Cruce guardado reutilizado",
}


def tabla_eventos(eventos: list[dict]) -> pd.DataFrame:
    cols = ["created_at", "usuario_id", "usuario", "workspace_id", "firma_archivos", "origen", "memoria_confianza", "aceptada",
            "corregida", "segundos_preparacion", "segundos_cruce", "n_grupos", "n_archivos", "filas", "llaves",
            "con_diferencias", "llamadas_ia", "tokens_ia", "ia_llave_validada", "idioma"]
    df = pd.DataFrame(eventos)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols + [c for c in df.columns if c not in cols]]
    for c in ("segundos_preparacion", "segundos_cruce", "memoria_confianza", "filas", "llaves", "llamadas_ia", "tokens_ia"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _pct(a: float, b: float) -> Optional[float]:
    return round(100 * a / b, 1) if b else None


def resumen(eventos: list[dict]) -> dict:
    """Indicadores principales (un diccionario plano, apto para mostrar o exportar)."""
    df = tabla_eventos(eventos)
    n = len(df)
    mem = df[df["origen"].isin(["memoria", "memoria_auto"])]
    ia = df[df["llamadas_ia"].fillna(0) > 0]
    prep = df.groupby("origen")["segundos_preparacion"].median()
    base = prep.get("heuristica")
    reuso = pd.concat([df[df["origen"] == o]["segundos_preparacion"] for o in ("memoria", "memoria_auto", "guardado")]).median() if n else None
    return {
        "cruces": n,
        "usuarios": int(df["usuario_id"].fillna(df["usuario"]).nunique()) if n else 0,
        "estructuras distintas (firmas)": int(df["firma_archivos"].nunique()) if n else 0,
        "cruces con configuración aprendida": len(mem),
        "  aceptada sin cambios": int((mem["corregida"] == False).sum()),  # noqa: E712
        "  corregida por el usuario": int((mem["corregida"] == True).sum()),  # noqa: E712
        "tasa de aceptación de lo aprendido (%)": _pct((mem["corregida"] == False).sum(), len(mem)),  # noqa: E712
        "aplicadas solas (> 95 % y aceptado)": int((df["origen"] == "memoria_auto").sum()),
        "preparación mediana a mano (s)": None if base is None or pd.isna(base) else round(float(base), 1),
        "preparación mediana reutilizando (s)": None if reuso is None or pd.isna(reuso) else round(float(reuso), 1),
        "ahorro de tiempo de preparación (%)": (_pct(base - reuso, base) if base and reuso is not None and not pd.isna(reuso) else None),
        "cruces que pidieron la llave a la IA": len(ia),
        "  propuesta de la IA validada": int((ia["ia_llave_validada"] == True).sum()),  # noqa: E712
        "llamadas a la IA": int(df["llamadas_ia"].fillna(0).sum()),
        "tokens de IA": int(df["tokens_ia"].fillna(0).sum()),
        "cruce mediano (s)": round(float(df["segundos_cruce"].median()), 2) if n and df["segundos_cruce"].notna().any() else None,
    }


def por_origen(eventos: list[dict]) -> pd.DataFrame:
    df = tabla_eventos(eventos)
    if df.empty:
        return pd.DataFrame(columns=["origen", "cruces", "preparación mediana (s)", "corregidas", "tokens IA"])
    g = df.groupby("origen").agg(cruces=("origen", "size"), prep=("segundos_preparacion", "median"),
                                 corregidas=("corregida", lambda s: int((s == True).sum())),  # noqa: E712
                                 tokens=("tokens_ia", "sum")).reset_index()
    g["origen"] = g["origen"].map(lambda o: ORIGENES.get(o, o))
    g["prep"] = g["prep"].round(1)
    return g.rename(columns={"prep": "preparación mediana (s)", "tokens": "tokens IA"})


def evolucion_confianza(eventos: list[dict]) -> pd.DataFrame:
    """Confianza de la memoria en cada reutilización, por estructura de archivos (para graficar el aprendizaje)."""
    df = tabla_eventos(eventos)
    df = df[df["memoria_confianza"].notna()].copy()
    if df.empty:
        return df
    df["uso"] = df.groupby("firma_archivos").cumcount() + 1
    return df[["firma_archivos", "uso", "memoria_confianza", "origen", "corregida", "created_at"]]


def a_excel(eventos: list[dict], memorias: list[dict]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(list(resumen(eventos).items()), columns=["indicador", "valor"]).to_excel(w, sheet_name="Resumen", index=False)
        por_origen(eventos).to_excel(w, sheet_name="Por origen", index=False)
        evolucion_confianza(eventos).to_excel(w, sheet_name="Evolución confianza", index=False)
        tabla_eventos(eventos).to_excel(w, sheet_name="Eventos", index=False)
        pd.DataFrame(memorias).to_excel(w, sheet_name="Memoria", index=False)
    return buf.getvalue()
