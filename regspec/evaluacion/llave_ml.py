"""E7 · Sugerencia de llave con un modelo aprendido y aprendizaje por uso (sin LLM, sin tokens).

E7a · Generalización. Validación cruzada dejando un escenario fuera: el modelo se entrena con casos sintéticos de
      tres escenarios y se prueba con los 24 casos de E3 (semilla 1000) del escenario reservado, con y sin una
      columna señuelo (numeración de filas: única y contenida al 100 % entre archivos, el error típico de una
      heurística de contención). Compara la heurística, el modelo entrenado sin señuelos y el modelo entrenado con
      señuelos.
E7b · Aprendizaje por uso. Un usuario empieza a recibir archivos con numeración de filas. En cada ejecución llega
      un archivo con otra estructura (escenario, idioma y nombres distintos). Se compara:
        - heurística: siempre la misma regla;
        - memoria de configuración: acierta solo si ya vio esa misma estructura (firma de columnas);
        - modelo + uso: el modelo base (entrenado sin señuelos) se reentrena con cada cruce confirmado.
      Cada sugerencia incorrecta cuenta como una corrección del usuario.

Uso:
    python -m regspec.evaluacion.llave_ml -o resultados/eval_llave_ml
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ..grupos import SOLIDEZ_MINIMA, sugerir_llaves, transformar_serie
from ..llave_ml import (NUMERICAS, RUTA_DATOS, ModeloLlave, ajustar, candidatos, ejemplos, ejemplos_de_cruce, entrenar,
                        sugerir_llaves_modelo)
from .conciliacion import ESCENARIOS, Caso, _misma_llave, casos, generar_caso

SENUELO = {"es": ("Nro_Linea", "Linea"), "pt": ("Num_Linha", "Linha"), "en": ("Line_No", "Row_Number")}
SEMILLAS_ENTRENAMIENTO = (101, 131, 161, 191, 221)
SEMILLA_PRUEBA = 1000


def con_senuelo(c: Caso, rng: random.Random) -> Caso:
    """Agrega a A y B una numeración de filas al principio (como en muchas exportaciones)."""
    if c.dificultad == "dificil":
        na, nb = f"Col{c.a.shape[1] + 1}", f"Campo_{chr(ord('A') + c.b.shape[1])}"
    else:
        na, nb = SENUELO[c.variante]
    a, b = c.a.copy(), c.b.copy()
    a.insert(0, na, [str(i) for i in range(1, len(a) + 1)])
    b.insert(0, nb, [str(i) for i in range(1, len(b) + 1)])
    return Caso(c.id + "-senuelo", c.escenario, c.variante, c.dificultad, a, b, c.llave_a, c.llave_b, c.importe_a,
                c.importe_b, c.estado_b, c.anulado, c.transformacion, c.verdad)


def _tr_validas(c: Caso) -> set:
    out = set()
    for tr in ("exacta", "solo_numeros"):
        ka, kb = set(transformar_serie(c.a[c.llave_a], tr)), set(transformar_serie(c.b[c.llave_b], tr))
        if len(ka & kb) / max(1, min(len(ka), len(kb))) >= 0.5:
            out.add(tr)
    return out


def ejemplos_caso(c: Caso) -> tuple[np.ndarray, np.ndarray]:
    return ejemplos(candidatos([c.a, c.b]), {1: (c.llave_a, c.llave_b)}, {1: _tr_validas(c)})


def conjunto(cs: list[Caso]) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = zip(*(ejemplos_caso(c) for c in cs))
    return np.vstack(xs), np.concatenate(ys)


def casos_entrenamiento(escenarios: set, senuelos: bool) -> list[Caso]:
    out = []
    for s in SEMILLAS_ENTRENAMIENTO:
        for c in casos(s):
            if c.escenario in escenarios:
                out.append(c)
                if senuelos:
                    out.append(con_senuelo(c, random.Random(s)))
    return out


def acierto_heuristica(c: Caso) -> tuple[bool, float]:
    s = sugerir_llaves([c.a, c.b])
    (ca, _, _), (cb, trb, sol) = s[0], s[1]
    return _misma_llave(c, ca, cb, trb), sol


def acierto_modelo(c: Caso, m: ModeloLlave) -> tuple[bool, float]:
    s = sugerir_llaves_modelo([c.a, c.b], m)
    (ca, _, _), (cb, trb, sol) = s[0], s[1]
    return _misma_llave(c, ca, cb, trb), sol


# ------------------------------------------------------------------ E7a


def e7a(semilla_prueba: int = SEMILLA_PRUEBA) -> pd.DataFrame:
    prueba = casos(semilla_prueba)
    filas = []
    for esc in ESCENARIOS:
        resto = set(ESCENARIOS) - {esc}
        m_sin = entrenar(*conjunto(casos_entrenamiento(resto, senuelos=False)))
        m_con = entrenar(*conjunto(casos_entrenamiento(resto, senuelos=True)))
        for c in [x for x in prueba if x.escenario == esc]:
            for tipo, caso in (("estandar", c), ("con_senuelo", con_senuelo(c, random.Random(semilla_prueba)))):
                h, hs = acierto_heuristica(caso)
                a, as_ = acierto_modelo(caso, m_sin)
                b, bs = acierto_modelo(caso, m_con)
                filas.append({"caso": c.id, "escenario": esc, "variante": c.variante, "dificultad": c.dificultad,
                              "archivos": tipo, "heuristica": h, "heuristica_clara": hs >= SOLIDEZ_MINIMA,
                              "modelo_sin_senuelos": a, "modelo_sin_senuelos_clara": as_ >= SOLIDEZ_MINIMA,
                              "modelo_con_senuelos": b, "modelo_con_senuelos_clara": bs >= SOLIDEZ_MINIMA})
    return pd.DataFrame(filas)


# ------------------------------------------------------------------ E7b


IDX_SECUENCIAL = [NUMERICAS.index("secuencial_ancla"), NUMERICAS.index("secuencial_otra")]


def e7b(ejecuciones: int = 20, repeticiones: int = 5, semilla: int = 5000) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (detalle por ejecución, control por usuario: no regresión y peso aprendido de la numeración de filas)."""
    base_X, base_y = conjunto(casos_entrenamiento(set(ESCENARIOS), senuelos=False))
    m_base = entrenar(base_X, base_y)
    estandar = casos(SEMILLA_PRUEBA)
    combos = [(e, v, d) for e in ESCENARIOS for v in ("es", "pt", "en") for d in ("estandar", "dificil")]
    filas, control = [], []
    for rep in range(repeticiones):
        rng = random.Random(semilla + rep)
        modelo, uso, memoria = m_base, [], {}
        for i in range(1, ejecuciones + 1):
            esc, var, dif = rng.choice(combos)
            c = con_senuelo(generar_caso(esc, var, dif, semilla + rep * 1000 + i), rng)
            firma = (tuple(c.a.columns), tuple(c.b.columns))
            h, _ = acierto_heuristica(c)
            mem = memoria.get(firma, False) or h
            t0 = time.perf_counter()
            mo, _ = acierto_modelo(c, modelo)
            ms = (time.perf_counter() - t0) * 1000
            filas.append({"repeticion": rep + 1, "ejecucion": i, "caso": c.id, "heuristica": h,
                          "memoria_configuracion": mem, "modelo_uso": mo, "modelo_base": acierto_modelo(c, m_base)[0],
                          "ejemplos_usuario": len(uso), "ms_sugerencia": round(ms, 1)})
            # el usuario confirma el cruce con la llave correcta (aceptando o corrigiendo): eso alimenta la memoria y el modelo
            memoria[firma] = True
            tr = sorted(_tr_validas(c))[0]
            uso.append(ejemplos_de_cruce([c.a, c.b], [c.llave_a, c.llave_b], tr))
            t0 = time.perf_counter()
            modelo = ajustar(base_X, base_y, uso)
            filas[-1]["ms_reentreno"] = round((time.perf_counter() - t0) * 1000, 1)
        control.append({"repeticion": rep + 1,
                        "estandar_modelo_base": float(np.mean([acierto_modelo(c, m_base)[0] for c in estandar])),
                        "estandar_tras_uso": float(np.mean([acierto_modelo(c, modelo)[0] for c in estandar])),
                        "peso_numeracion_base": float(m_base.coef[IDX_SECUENCIAL].mean()),
                        "peso_numeracion_tras_uso": float(modelo.coef[IDX_SECUENCIAL].mean())})
    return pd.DataFrame(filas), pd.DataFrame(control)


# ------------------------------------------------------------------ modelo base que usa la app


def entrenar_modelo_app() -> ModeloLlave:
    """Modelo que se distribuye con la app: todos los escenarios, con y sin señuelos."""
    X, y = conjunto(casos_entrenamiento(set(ESCENARIOS), senuelos=True))
    m = entrenar(X, y)
    m.guardar()
    np.savez_compressed(RUTA_DATOS, X=np.round(X, 4).astype(np.float32), y=y.astype(np.int8))
    return m


# ------------------------------------------------------------------ informe


def _p(x) -> str:
    return f"{100 * x:.1f} %"


def informe(a: pd.DataFrame, b: pd.DataFrame, ctrl: pd.DataFrame, coef: dict) -> str:
    L = ["# E7 · Sugerencia de llave con modelo aprendido y aprendizaje por uso", "",
         f"Generado el {time.strftime('%Y-%m-%d %H:%M')} con `python -m regspec.evaluacion.llave_ml`. Sin LLM ni tokens.", "",
         "## E7a · Generalización (validación cruzada dejando un escenario fuera)", "",
         "Prueba: los 24 casos de E3 (semilla 1000), sin y con una columna señuelo (numeración de filas). "
         "Cada modelo se entrenó sin el escenario que se prueba.", "",
         "| archivos | casos | heurística | modelo entrenado sin señuelos | modelo entrenado con señuelos |",
         "|---|---|---|---|---|"]
    for tipo, d in a.groupby("archivos", sort=False):
        L.append(f"| {tipo} | {len(d)} | {_p(d.heuristica.mean())} | {_p(d.modelo_sin_senuelos.mean())} | "
                 f"{_p(d.modelo_con_senuelos.mean())} |")
    L += ["", "Por dificultad (con señuelo):", ""]
    for dif, d in a[a.archivos == "con_senuelo"].groupby("dificultad"):
        L.append(f"- {dif}: heurística {_p(d.heuristica.mean())} · modelo sin señuelos {_p(d.modelo_sin_senuelos.mean())} · "
                 f"modelo con señuelos {_p(d.modelo_con_senuelos.mean())}")
    L += ["", "## E7b · Aprendizaje por uso", "",
          f"{b.repeticion.nunique()} usuarios simulados × {b.ejecucion.nunique()} ejecuciones. Todos los archivos traen "
          "numeración de filas; cada ejecución puede tener otra estructura. El modelo base se entrenó sin señuelos.", "",
          "| condición | acierto | correcciones del usuario (media por usuario) |", "|---|---|---|"]
    n_us = b.repeticion.nunique()
    for col, nombre in (("heuristica", "heurística"), ("memoria_configuracion", "heurística + memoria de configuración"),
                        ("modelo_base", "modelo base, sin uso"), ("modelo_uso", "modelo + aprendizaje por uso")):
        L.append(f"| {nombre} | {_p(b[col].mean())} | {(~b[col]).sum() / n_us:.1f} de {b.ejecucion.nunique()} |")
    curva = b.groupby("ejecucion")[["heuristica", "memoria_configuracion", "modelo_uso"]].mean()
    L += ["", "Acierto por ejecución (media de los usuarios):", "",
          "| ejecución | heurística | memoria | modelo + uso |", "|---|---|---|---|"]
    for i, r in curva.iterrows():
        L.append(f"| {i} | {_p(r.heuristica)} | {_p(r.memoria_configuracion)} | {_p(r.modelo_uso)} |")
    prim = b[~b.modelo_uso].groupby("repeticion").ejecucion.max()
    L += ["", f"Última corrección necesaria con el modelo + uso: ejecución {int(prim.max()) if len(prim) else 0} "
          f"(media {prim.mean():.1f}).",
          f"Tiempo medio: sugerencia {b.ms_sugerencia.mean():.0f} ms; reentrenamiento {b.ms_reentreno.mean():.0f} ms.", "",
          "**Controles.** No regresión: acierto en los 24 casos de E3 sin señuelo con el modelo base "
          f"{_p(ctrl.estandar_modelo_base.mean())} y con el modelo de cada usuario tras el uso {_p(ctrl.estandar_tras_uso.mean())} "
          f"(mínimo {_p(ctrl.estandar_tras_uso.min())}). Qué aprendió: peso medio de «parece numeración de filas» "
          f"{ctrl.peso_numeracion_base.mean():+.2f} en el modelo base y {ctrl.peso_numeracion_tras_uso.mean():+.2f} tras el uso.", "",
          "## Coeficientes del modelo de la app (características numéricas)", "",
          "| característica | coeficiente |", "|---|---|"]
    for k, v in coef.items():
        L.append(f"| {k} | {v:+.2f} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-o", "--salida", default="resultados/eval_llave_ml")
    ap.add_argument("--ejecuciones", type=int, default=20)
    ap.add_argument("--repeticiones", type=int, default=5)
    args = ap.parse_args(argv)
    out = Path(args.salida)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    a = e7a()
    a.to_csv(out / "e7a_generalizacion.csv", index=False)
    b, ctrl = e7b(args.ejecuciones, args.repeticiones)
    b.to_csv(out / "e7b_uso.csv", index=False)
    ctrl.to_csv(out / "e7b_controles.csv", index=False)
    m = entrenar_modelo_app()
    coef = dict(zip(NUMERICAS, m.coef[:len(NUMERICAS)]))
    (out / "resumen.md").write_text(informe(a, b, ctrl, coef), encoding="utf-8")
    print((out / "resumen.md").read_text(encoding="utf-8"))
    print(f"[{time.time() - t0:.0f} s]")


if __name__ == "__main__":
    main()
