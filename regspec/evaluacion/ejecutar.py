"""Banco de evaluación: condiciones × manuales × repeticiones.

Condiciones (estudio de ablación):
  referencia                 la especificación gold (control del propio banco: debe dar 100 %)
  base_sin_ia                parser convencional por reglas
  llm_1_intento              LLM, una sola llamada, sin verificador
  llm_bucle                  LLM + verificador completo + autocorrección   <- sistema propuesto
  llm_bucle_sin_evidencia    ídem sin el control de evidencia literal
  llm_bucle_sin_ejecucion    ídem sin la prueba de ejecución (ida y vuelta)
  llm_bucle_solo_ia          ídem sin el parser convencional como primer paso (todo lo extrae la IA)

Todas las especificaciones se re-verifican al final con el verificador completo,
de modo que "spec_valida" es comparable entre condiciones.
"""
from __future__ import annotations

import csv
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from ..base_sin_ia import extraer_base
from ..dsl import Especificacion
from ..llm.extractor import ConfigExtraccion, extraer
from ..llm.proveedores import Proveedor
from ..verificador import verificar
from .metricas import metricas_estructurales, metricas_funcionales
from .mutaciones import construir_bateria

RAIZ = Path(__file__).resolve().parents[2]

CONDICIONES_LLM = {
    "llm_1_intento": ConfigExtraccion(usar_verificador=False),
    "llm_bucle": ConfigExtraccion(),
    "llm_bucle_solo_ia": ConfigExtraccion(usar_codigo=False),
    "llm_bucle_sin_evidencia": ConfigExtraccion(chequear_evidencia=False),
    "llm_bucle_sin_ejecucion": ConfigExtraccion(chequear_ejecucion=False),
}

METRICAS_TABLA = ["spec_valida", "campos_f1", "campos_exactos", "atributos_exactitud", "reglas_extraidas",
                  "especificidad", "sensibilidad", "sens_campo", "sens_regla", "exactitud_balanceada",
                  "iteraciones", "tokens", "latencia_s"]


def cargar_casos(dir_manuales: Path = RAIZ / "manuales", dir_gold: Path = RAIZ / "gold") -> list[dict]:
    casos = []
    for m in sorted(dir_manuales.glob("*.md")):
        g = dir_gold / (m.stem + ".json")
        if g.exists():
            casos.append({"id": m.stem, "manual": m.read_text(encoding="utf-8"),
                          "gold": Especificacion.model_validate(json.loads(g.read_text(encoding="utf-8")))})
    return casos


def evaluar_spec(spec_dict: Optional[dict], manual: str, gold: Especificacion, bateria) -> dict:
    ver = verificar(spec_dict, manual) if spec_dict is not None else None
    spec = ver.spec if ver else None
    fila = {"spec_valida": bool(ver and ver.valida), "errores_verificador": len(ver.errores) if ver else None}
    fila.update(metricas_estructurales(spec, gold))
    fila.update(metricas_funcionales(spec, bateria))
    return fila


def ejecutar(condiciones: list[str], proveedor: Optional[Proveedor] = None, repeticiones: int = 1,
             salida: Optional[Path] = None, manuales: Optional[list[str]] = None, log=print,
             usar_cache: bool = True) -> Path:
    """Con caché, cada (condición, repetición) se consulta una sola vez: re-correr el banco no gasta tokens."""
    from ..llm.cache import ProveedorConCache
    casos = cargar_casos()
    if manuales:
        casos = [c for c in casos if c["id"] in manuales]
    salida = salida or RAIZ / "resultados" / time.strftime("%Y%m%d_%H%M%S")
    (salida / "specs").mkdir(parents=True, exist_ok=True)
    filas = []
    for caso in casos:
        bateria = construir_bateria(caso["gold"])
        for cond in condiciones:
            reps = 1 if cond in ("referencia", "base_sin_ia") else repeticiones
            for rep in range(reps):
                t0 = time.time()
                traza: dict = {}
                if cond == "referencia":
                    d = caso["gold"].model_dump()
                elif cond == "base_sin_ia":
                    d = extraer_base(caso["manual"])
                else:
                    if proveedor is None:
                        raise ValueError(f"la condición {cond} requiere un proveedor de LLM")
                    prov = ProveedorConCache(proveedor, usar=usar_cache, sal=f"{cond}#{rep}")
                    r = extraer(caso["manual"], prov, CONDICIONES_LLM[cond])
                    d = r.spec_dict
                    traza = r.traza()
                fila = {"manual": caso["id"], "condicion": cond, "repeticion": rep,
                        "modelo": getattr(proveedor, "modelo", "") if cond.startswith("llm") else "",
                        "iteraciones": len(traza.get("iteraciones", [])) or (0 if cond in ("referencia", "base_sin_ia") else None),
                        "tokens": traza.get("tokens", 0), "latencia_s": round(traza.get("latencia_s", time.time() - t0), 2),
                        "error_llm": traza.get("error")}
                fila.update(evaluar_spec(d, caso["manual"], caso["gold"], bateria))
                filas.append(fila)
                nombre = f"{caso['id']}__{cond}__{rep}"
                (salida / "specs" / f"{nombre}.json").write_text(json.dumps({"spec": d, "traza": traza}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                log(f"{caso['id']:<22} {cond:<26} rep {rep}  valida={fila['spec_valida']!s:<5} "
                    f"F1 campos={fila['campos_f1']:.2f}  exact. balanceada={fila['exactitud_balanceada']:.2f}")
    _guardar(filas, salida)
    return salida


def _guardar(filas: list[dict], salida: Path) -> None:
    with open(salida / "detalle.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
        w.writeheader()
        w.writerows(filas)
    grupos: dict[tuple, list[dict]] = {}
    for f_ in filas:
        grupos.setdefault((f_["manual"], f_["condicion"]), []).append(f_)
    lineas = ["| manual | condición | n | " + " | ".join(METRICAS_TABLA) + " |",
              "|---|---|---|" + "---|" * len(METRICAS_TABLA)]
    for (man, cond), fs in grupos.items():
        celdas = []
        for k in METRICAS_TABLA:
            vals = [float(x[k]) for x in fs if x.get(k) is not None]
            if not vals:
                celdas.append("–")
            elif len(vals) == 1:
                celdas.append(f"{vals[0]:.2f}" if k not in ("tokens", "reglas_extraidas", "iteraciones") else f"{vals[0]:.0f}")
            else:
                celdas.append(f"{statistics.mean(vals):.2f} ± {statistics.stdev(vals):.2f}")
        lineas.append(f"| {man} | {cond} | {len(fs)} | " + " | ".join(celdas) + " |")
    (salida / "resumen.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
