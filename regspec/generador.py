"""Generación del informe regulatorio a partir de datos del usuario.

Toma registros (por ejemplo, el resultado de una conciliación), completa
automáticamente los campos derivables de las reglas (totales de control,
cantidades) y los identificadores de registro, escribe el archivo y lo
valida antes de entregarlo.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Optional

from .archivos import Registro, escribir
from .dsl import Especificacion
from .expr import ContextoArchivo, ErrorExpresion, Evaluador, Nulo, parsear
from .sintetico import _ajustar, _lado_asignable, _valor_asignable
from .validador import Informe, validar
from .valores import ErrorValor, coercer


@dataclass
class ResultadoGeneracion:
    contenido: Optional[str]
    informe: Informe
    autocompletados: list[str]
    error_escritura: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.contenido is not None and self.informe.ok


def _vacio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def autocompletar(spec: Especificacion, registros: list[Registro]) -> list[str]:
    """Completa campos vacíos que las reglas de igualdad permiten derivar. Devuelve qué se completó.

    Orden: primero los campos derivados dentro de cada registro, luego los totales de archivo y,
    por último, otra vez los de registro (por si dependen de un total).
    """
    hechos: list[str] = []

    def ctx() -> ContextoArchivo:
        d: dict[str, list] = {}
        for cod, v in registros:
            d.setdefault(cod, []).append(v)
        return ContextoArchivo(d)

    def pasada(regla) -> int:
        try:
            nodo = parsear(regla.expresion)
        except ErrorExpresion:
            return 0
        n = 0
        if regla.ambito == "archivo":
            asig = _valor_asignable(nodo)
            if not asig or spec.tipo(asig[0]) is None or spec.tipo(asig[0]).campo(asig[1]) is None:
                return 0
            t, campo, expr = asig
            for cod, vals in registros:
                if cod == t and _vacio(vals.get(campo)):
                    try:
                        vals[campo] = _ajustar(spec.tipo(t).campo(campo), Evaluador(ctx()).ev(expr))
                        hechos.append(f"{t}.{campo} (regla {regla.id})")
                        n += 1
                    except (Nulo, TypeError):
                        pass
            return n
        tr = spec.tipo(regla.tipo_registro or "")
        asig = _lado_asignable(nodo, tr) if tr else None
        if not asig or not tr.campo(asig[0]):
            return 0
        campo, expr = asig
        for i, (cod, vals) in enumerate(registros):
            if cod == tr.codigo and _vacio(vals.get(campo)):
                try:
                    vals[campo] = _ajustar(tr.campo(campo), Evaluador(ctx(), vals).ev(expr))
                    hechos.append(f"{cod}[{i}].{campo} (regla {regla.id})")
                    n += 1
                except (Nulo, TypeError):
                    pass
        return n

    for fase in ("registro", "archivo", "registro"):
        for _ in range(4):  # dependencias encadenadas dentro de la fase
            if not sum(pasada(r) for r in spec.reglas if r.ambito == fase):
                break
    return hechos


def generar(spec: Especificacion, registros: list[Registro], completar: bool = True) -> ResultadoGeneracion:
    normalizados = []
    for i, (c, v) in enumerate(registros, 1):
        tr = spec.tipo(c)
        if tr is None:
            return ResultadoGeneracion(None, Informe(), [], f"registro {i}: tipo '{c}' no definido")
        try:
            normalizados.append((c, {k: (coercer(tr.campo(k), x) if tr.campo(k) else x) for k, x in v.items()}))
        except ErrorValor as e:
            return ResultadoGeneracion(None, Informe(), [], f"registro {i} ({c}): {e}")
    registros = normalizados
    hechos = autocompletar(spec, registros) if completar else []
    try:
        contenido = escribir(spec, registros)
    except ErrorValor as e:
        return ResultadoGeneracion(None, Informe(), hechos, str(e))
    return ResultadoGeneracion(contenido, validar(spec, contenido), hechos)


def registros_desde_tabla(filas: list[dict[str, Any]], codigo: str, mapeo: dict[str, str]) -> list[Registro]:
    """Convierte filas (p. ej. de un CSV/DataFrame) en registros de un tipo, según un mapeo columna -> campo."""
    return [(codigo, {campo: fila.get(col) for col, campo in mapeo.items()}) for fila in filas]
