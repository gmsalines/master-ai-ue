"""Métricas de evaluación: estructurales (contra la referencia) y funcionales (por comportamiento)."""
from __future__ import annotations

from typing import Optional

from ..dsl import Campo, Especificacion
from ..validador import validar
from .mutaciones import CasoPrueba

ATRIBUTOS = ["tipo", "decimales", "formato_fecha", "obligatorio", "valor_constante", "valores_permitidos", "patron"]


def _norm_fecha(f: Optional[str]) -> Optional[str]:
    return f.upper().replace("YYYY", "AAAA") if f else None


def _igual_atributo(a: Campo, g: Campo, atr: str) -> bool:
    va, vg = getattr(a, atr), getattr(g, atr)
    if atr == "formato_fecha":
        return _norm_fecha(va) == _norm_fecha(vg)
    if atr == "valores_permitidos":
        return {x.strip() for x in va or []} == {x.strip() for x in vg or []}
    if atr == "valor_constante":
        return (va or "").strip() == (vg or "").strip()
    if atr == "patron":
        return bool(va) == bool(vg)  # presencia; la equivalencia real se mide funcionalmente
    return va == vg


def _clave(c: Campo, xml: bool):
    return (c.etiqueta_xml or "").lower() if xml else (c.inicio, c.longitud)


def _f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


def metricas_estructurales(pred: Optional[Especificacion], gold: Especificacion) -> dict:
    m = {"registros_precision": 0.0, "registros_recall": 0.0, "campos_precision": 0.0, "campos_recall": 0.0,
         "campos_f1": 0.0, "atributos_exactitud": 0.0, "campos_exactos": 0.0, "reglas_extraidas": 0,
         "reglas_referencia": len(gold.reglas), "globales_ok": 0.0}
    for a in ATRIBUTOS:
        m[f"atr_{a}"] = 0.0
    if pred is None:
        return m
    xml = gold.formato == "xml"
    gp = {t.codigo.strip().upper(): t for t in gold.tipos_registro}
    pp = {t.codigo.strip().upper(): t for t in pred.tipos_registro}
    comunes = set(gp) & set(pp)
    m["registros_precision"] = len(comunes) / len(pp) if pp else 0.0
    m["registros_recall"] = len(comunes) / len(gp)

    n_pred = sum(len(t.campos) for t in pred.tipos_registro)
    n_gold = sum(len(t.campos) for t in gold.tipos_registro)
    pares: list[tuple[Campo, Campo]] = []
    for k in comunes:
        gi = {_clave(c, xml): c for c in gp[k].campos}
        for c in pp[k].campos:
            g = gi.get(_clave(c, xml))
            if g is not None:
                pares.append((c, g))
    m["campos_precision"] = len(pares) / n_pred if n_pred else 0.0
    m["campos_recall"] = len(pares) / n_gold
    m["campos_f1"] = _f1(m["campos_precision"], m["campos_recall"])
    if pares:
        for a in ATRIBUTOS:
            m[f"atr_{a}"] = sum(_igual_atributo(p, g, a) for p, g in pares) / len(pares)
        m["atributos_exactitud"] = sum(m[f"atr_{a}"] for a in ATRIBUTOS) / len(ATRIBUTOS)
        m["campos_exactos"] = sum(all(_igual_atributo(p, g, a) for a in ATRIBUTOS) for p, g in pares) / n_gold
    m["reglas_extraidas"] = len(pred.reglas)
    globales = [pred.formato == gold.formato]
    if gold.formato == "ancho_fijo":
        globales += [pred.longitud_registro == gold.longitud_registro, pred.separador_lineas == gold.separador_lineas]
    else:
        globales += [pred.etiqueta_raiz_xml == gold.etiqueta_raiz_xml]
    m["globales_ok"] = sum(globales) / len(globales)
    return m


def metricas_funcionales(pred: Optional[Especificacion], bateria: list[CasoPrueba]) -> dict:
    """Sensibilidad (detecta errores) y especificidad (acepta válidos) sobre la batería de prueba.

    Una detección solo se acredita si la especificación acepta el archivo válido del que deriva la
    mutación: rechazar todo no cuenta como detectar errores.
    """

    def acepta(contenido: str) -> bool:
        try:
            return validar(pred, contenido).ok
        except Exception:  # una especificación defectuosa puede romper la lectura
            return False

    base = next((c for c in bateria if c.id == "valido:0"), None)
    acepta_base = pred is not None and base is not None and acepta(base.contenido)
    cats: dict[str, list[bool]] = {}
    for caso in bateria:
        if caso.categoria == "sin_mutacion":
            continue
        if pred is None:
            acierto = False
        elif caso.esperado_valido:
            acierto = acepta(caso.contenido)
        else:
            acierto = acepta_base and not acepta(caso.contenido)
        cats.setdefault(caso.categoria, []).append(acierto)
    tasa = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    errores = [a for k, v in cats.items() if k != "valido" for a in v]
    m = {
        "especificidad": tasa(cats.get("valido", [])),
        "sensibilidad": tasa(errores),
        "sens_campo": tasa(cats.get("campo", [])),
        "sens_estructura": tasa(cats.get("estructura", [])),
        "sens_regla": tasa(cats.get("regla", [])),
        "casos": sum(len(v) for v in cats.values()),
    }
    m["exactitud_balanceada"] = ((m["especificidad"] or 0) + (m["sensibilidad"] or 0)) / 2
    return m
