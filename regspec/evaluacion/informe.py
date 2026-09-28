"""Resumen del banco de evaluación para la memoria del TFM.

    python -m regspec.evaluacion.informe resultados/<carpeta> [resultados/<otra carpeta> ...]

Agrega detalle.csv de una o varias corridas (por ejemplo, una por modelo) y
produce tablas en Markdown: por condición (promedio sobre los manuales) y por
manual × condición.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ORDEN = ["base_sin_ia", "llm_1_intento", "llm_bucle_solo_ia", "llm_bucle_sin_evidencia", "llm_bucle_sin_ejecucion", "llm_bucle"]
NOMBRES = {
    "base_sin_ia": "Parser sin IA",
    "llm_1_intento": "LLM, 1 intento",
    "llm_bucle_solo_ia": "LLM + verificador (sin parser)",
    "llm_bucle_sin_evidencia": "Sistema sin evidencia",
    "llm_bucle_sin_ejecucion": "Sistema sin ejecución",
    "llm_bucle": "Sistema completo",
}
METRICAS = {"spec_valida": "Spec válida", "campos_f1": "F1 campos", "campos_exactos": "Campos exactos",
            "especificidad": "Especificidad", "sensibilidad": "Sensibilidad", "sens_regla": "Detección reglas",
            "exactitud_balanceada": "Exact. balanceada", "iteraciones": "Iteraciones", "tokens": "Tokens"}


def cargar(carpetas: list[str]) -> pd.DataFrame:
    dfs = [pd.read_csv(Path(c) / "detalle.csv") for c in carpetas]
    df = pd.concat(dfs, ignore_index=True)
    df = df[df["condicion"] != "referencia"]
    df["modelo"] = df["modelo"].fillna("").replace("", "—")
    df.loc[df["condicion"] == "base_sin_ia", "modelo"] = "—"
    return df.drop_duplicates(subset=["manual", "condicion", "modelo", "repeticion"], keep="last")


def _fmt(v, k):
    if pd.isna(v):
        return "–"
    if k == "tokens":
        return f"{v:,.0f}".replace(",", ".")
    if k == "iteraciones":
        return f"{v:.1f}"
    return f"{v:.2f}"


def tabla_condiciones(df: pd.DataFrame) -> str:
    df = df.copy()
    df["spec_valida"] = df["spec_valida"].astype(float)
    g = df.groupby(["modelo", "condicion"])[list(METRICAS)].mean(numeric_only=True).reset_index()
    g["_o"] = g["condicion"].map({c: i for i, c in enumerate(ORDEN)})
    g = g.sort_values(["modelo", "_o"])
    filas = ["| Modelo | Condición | " + " | ".join(METRICAS.values()) + " |", "|---|---|" + "---|" * len(METRICAS)]
    for _, r in g.iterrows():
        filas.append(f"| {r['modelo']} | {NOMBRES.get(r['condicion'], r['condicion'])} | "
                     + " | ".join(_fmt(r[k], k) for k in METRICAS) + " |")
    return "\n".join(filas)


def tabla_manuales(df: pd.DataFrame, metrica: str = "exactitud_balanceada") -> str:
    p = df.pivot_table(index="manual", columns=["modelo", "condicion"], values=metrica, aggfunc="mean")
    cols = sorted(p.columns, key=lambda c: (c[0], ORDEN.index(c[1]) if c[1] in ORDEN else 99))
    p = p[cols]
    cab = "| Manual | " + " | ".join(f"{NOMBRES.get(c, c)} ({m})" if m != "—" else NOMBRES.get(c, c) for m, c in cols) + " |"
    filas = [cab, "|---|" + "---|" * len(cols)]
    for man, r in p.iterrows():
        filas.append(f"| {man} | " + " | ".join(_fmt(r[c], metrica) for c in cols) + " |")
    return "\n".join(filas)


def main(argv=None) -> None:
    carpetas = (argv or sys.argv[1:])
    df = cargar(carpetas)
    print("## Promedio por condición\n")
    print(tabla_condiciones(df))
    print("\n## Exactitud balanceada por manual\n")
    print(tabla_manuales(df))


if __name__ == "__main__":
    main()
