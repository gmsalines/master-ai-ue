"""Generación de archivos sintéticos válidos a partir de una especificación.

Se usa (1) en el verificador, para probar que una especificación es
ejecutable y sus reglas satisfacibles, y (2) en la evaluación, como base de
los archivos con errores inyectados.

Estrategia: se muestrean valores que respetan cada campo y luego se
"reparan" las reglas: igualdades de la forma `campo == expr` se resuelven
asignando, `existe()` toma un valor existente, `implica()` completa o vacía
el consecuente, y el resto se resuelve por re-muestreo.
"""
from __future__ import annotations

import ast
import random
import re
import string
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

try:  # Python 3.11+
    import re._parser as sre_parse  # type: ignore
    from re._constants import (  # type: ignore
        ANY, BRANCH, CATEGORY, CATEGORY_DIGIT, CATEGORY_SPACE, CATEGORY_WORD, IN, LITERAL, MAX_REPEAT,
        MIN_REPEAT, NEGATE, RANGE, SUBPATTERN, AT,
    )
except ImportError:  # pragma: no cover
    import sre_parse  # type: ignore
    from sre_constants import (  # type: ignore
        ANY, BRANCH, CATEGORY, CATEGORY_DIGIT, CATEGORY_SPACE, CATEGORY_WORD, IN, LITERAL, MAX_REPEAT,
        MIN_REPEAT, NEGATE, RANGE, SUBPATTERN, AT,
    )

from .archivos import Registro
from .dsl import Campo, Especificacion, TipoRegistro
from .expr import ContextoArchivo, ErrorExpresion, Evaluador, Nulo, parsear

_CAT = {
    CATEGORY_DIGIT: string.digits,
    CATEGORY_WORD: string.ascii_letters + string.digits + "_",
    CATEGORY_SPACE: " ",
}


def muestra_regex(patron: str, rng: random.Random) -> str:
    """Muestrea un texto que cumple un patrón regex simple (clases, rangos, repeticiones, alternativas)."""

    def gen(items) -> str:
        out = []
        for op, av in items:
            if op == LITERAL:
                out.append(chr(av))
            elif op == ANY:
                out.append(rng.choice(string.ascii_uppercase))
            elif op == IN:
                chars, neg = [], False
                for sop, sav in av:
                    if sop == NEGATE:
                        neg = True
                    elif sop == LITERAL:
                        chars.append(chr(sav))
                    elif sop == RANGE:
                        chars.extend(chr(c) for c in range(sav[0], sav[1] + 1))
                    elif sop == CATEGORY:
                        chars.extend(_CAT.get(sav, string.ascii_letters))
                if neg:
                    chars = [c for c in string.ascii_uppercase + string.digits if c not in chars]
                out.append(rng.choice(chars))
            elif op in (MAX_REPEAT, MIN_REPEAT):
                lo, hi, sub = av
                hi = min(hi, lo + 5) if hi != sre_parse.MAXREPEAT else lo + 5
                out.extend(gen(sub) for _ in range(rng.randint(lo, hi)))
            elif op == SUBPATTERN:
                out.append(gen(av[-1]))
            elif op == BRANCH:
                out.append(gen(rng.choice(av[1])))
            elif op == CATEGORY:
                out.append(rng.choice(_CAT.get(av, string.ascii_letters)))
            elif op == AT:
                continue
            else:
                raise ValueError(f"patrón no soportado para muestreo: {patron}")
        return "".join(out)

    for _ in range(50):
        s = gen(sre_parse.parse(patron))
        if re.fullmatch(patron, s):
            return s
    raise ValueError(f"no se pudo muestrear el patrón {patron}")


_RELLENO = re.compile(r"^(relleno|filler|reservado|blancos?)(_\d+)?$")


def valor_aleatorio(c: Campo, rng: random.Random, formato: str, forzar: bool = False, grande: bool = False) -> Any:
    if c.tipo == "constante":
        return c.valor_constante
    if not c.obligatorio and not forzar and (_RELLENO.match(c.nombre) or rng.random() < 0.3):
        return None
    if c.valores_permitidos:
        v = rng.choice(c.valores_permitidos).strip()
        return Decimal(int(v)) if c.tipo in ("numerico", "decimal") and v.isdigit() else v
    if c.patron:
        return muestra_regex(c.patron, rng)
    if c.tipo == "alfanumerico":
        n = rng.randint(1, min(c.longitud, 12))
        return "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(n)).strip() or "X"
    if c.tipo == "fecha":
        d = date(2025, 1, 1) + timedelta(days=rng.randint(0, 364))
        return d if "DD" in (c.formato_fecha or "").upper() else d.replace(day=1)
    enteros = max(1, min(c.longitud - c.decimales - 4, 6))
    if grande:
        enteros = max(1, min(c.longitud - c.decimales - 2, 10))
    if c.tipo == "numerico":
        return Decimal(rng.randint(1, 10**enteros - 1))
    cent = rng.randint(1, 10 ** (enteros + c.decimales) - 1)
    return Decimal(cent).scaleb(-c.decimales)


def _cantidad(tr: TipoRegistro, rng: random.Random) -> int:
    lo = tr.min_ocurrencias
    hi = tr.max_ocurrencias if tr.max_ocurrencias is not None else max(lo, 4)
    if hi == lo:
        return lo
    return rng.randint(max(lo, 1), min(hi, max(lo, 4)))


def _ajustar(c: Campo, v: Any) -> Any:
    """Normaliza un valor calculado al tipo del campo (redondeo a sus decimales)."""
    if v is None:
        return None
    if c.tipo in ("numerico", "decimal") and isinstance(v, Decimal):
        return v.quantize(Decimal(1).scaleb(-(c.decimales if c.tipo == "decimal" else 0)), rounding=ROUND_HALF_UP)
    return v


def _lado_asignable(nodo: ast.AST, tr: Optional[TipoRegistro]):
    """Si la regla es `campo == expr` (o simétrica) devuelve (campo, expr_ast)."""
    if isinstance(nodo, ast.Compare) and len(nodo.ops) == 1 and isinstance(nodo.ops[0], ast.Eq):
        izq, der = nodo.left, nodo.comparators[0]
        for a, b in ((izq, der), (der, izq)):
            if isinstance(a, ast.Name):
                return a.id, b
            # valor("T", "campo") dentro de una regla del propio registro T equivale al campo suelto
            if (tr is not None and isinstance(a, ast.Call) and getattr(a.func, "id", "") == "valor" and len(a.args) == 2
                    and isinstance(a.args[0], ast.Constant) and a.args[0].value == tr.codigo
                    and isinstance(a.args[1], ast.Constant)):
                return a.args[1].value, b
    return None


def _valor_asignable(nodo: ast.AST):
    """Si la regla (archivo) es `valor("T","c") == expr` devuelve (T, c, expr_ast)."""
    if isinstance(nodo, ast.Compare) and len(nodo.ops) == 1 and isinstance(nodo.ops[0], ast.Eq):
        for a, b in ((nodo.left, nodo.comparators[0]), (nodo.comparators[0], nodo.left)):
            if isinstance(a, ast.Call) and isinstance(a.func, ast.Name) and a.func.id == "valor":
                return a.args[0].value, a.args[1].value, b
    return None


def generar_registros(spec: Especificacion, semilla: int = 0, max_pasadas: int = 12) -> tuple[list[Registro], list[str]]:
    """Genera registros que cumplen campos y reglas. Devuelve (registros, ids_de_reglas_no_satisfechas)."""
    rng = random.Random(semilla)
    fmt = spec.formato
    registros: list[Registro] = []
    for tr in spec.tipos_registro:
        for _ in range(_cantidad(tr, rng)):
            registros.append((tr.codigo, {c.nombre: valor_aleatorio(c, rng, fmt) for c in tr.campos}))

    def ctx() -> ContextoArchivo:
        d: dict[str, list] = {}
        for cod, v in registros:
            d.setdefault(cod, []).append(v)
        return ContextoArchivo(d)

    # campos que son "salida" de una igualdad (no se re-muestrean para arreglar otras reglas)
    objetivos: dict[str, set[str]] = {}
    for regla in spec.reglas:
        if regla.ambito == "registro":
            try:
                a = _lado_asignable(parsear(regla.expresion), spec.tipo(regla.tipo_registro or ""))
            except ErrorExpresion:
                a = None
            if a:
                objetivos.setdefault(regla.tipo_registro or "", set()).add(a[0])

    pendientes: list[str] = []
    for _ in range(max_pasadas):
        pendientes = []
        cambios = 0
        for regla in spec.reglas:
            try:
                nodo = parsear(regla.expresion)
            except ErrorExpresion:
                pendientes.append(regla.id)
                continue
            if regla.ambito == "registro":
                tr = spec.tipo(regla.tipo_registro or "")
                if tr is None:
                    pendientes.append(regla.id)
                    continue
                for cod, vals in registros:
                    if cod != tr.codigo:
                        continue
                    c0 = ctx()
                    try:
                        if Evaluador(c0, vals).ev(nodo):
                            continue
                    except Nulo:
                        continue
                    except (TypeError, ErrorExpresion):
                        pendientes.append(regla.id)
                        break
                    if _reparar(nodo, tr, vals, c0, rng, fmt, objetivos.get(tr.codigo, set())):
                        cambios += 1
                    else:
                        pendientes.append(regla.id)
            else:
                c0 = ctx()
                try:
                    if Evaluador(c0).ev(nodo):
                        continue
                except Nulo:
                    continue
                except (TypeError, ErrorExpresion):
                    pendientes.append(regla.id)
                    continue
                asig = _valor_asignable(nodo)
                if asig:
                    t, campo, expr = asig
                    trt = spec.tipo(t)
                    try:
                        v = Evaluador(c0).ev(expr)
                    except (Nulo, TypeError):
                        pendientes.append(regla.id)
                        continue
                    for cod, vals in registros:
                        if cod == t:
                            vals[campo] = _ajustar(trt.campo(campo), v)
                    cambios += 1
                else:
                    pendientes.append(regla.id)
        if not cambios and not pendientes:
            break
        if not cambios:
            break
    return registros, sorted(set(pendientes))


def _reparar(nodo: ast.AST, tr: TipoRegistro, vals: dict, ctx: ContextoArchivo, rng: random.Random, fmt: str,
             objetivos: frozenset | set = frozenset()) -> bool:
    asig = _lado_asignable(nodo, tr)
    if asig and tr.campo(asig[0]) and tr.campo(asig[0]).tipo not in ("constante",) and not tr.campo(asig[0]).valores_permitidos:
        campo, expr = asig
        c = tr.campo(campo)
        libres = [n.id for n in ast.walk(expr) if isinstance(n, ast.Name) and tr.campo(n.id)
                  and n.id not in objetivos and n.id != campo and tr.campo(n.id).tipo in ("numerico", "decimal")]
        for intento in range(60):
            try:
                v = Evaluador(ctx, vals).ev(expr)
            except (Nulo, TypeError):
                return False
            # los campos numéricos sin signo no admiten negativos: se agrandan los operandos libres
            if not (c.tipo in ("numerico", "decimal") and isinstance(v, Decimal) and v < 0) or not libres:
                break
            for n in libres:
                vals[n] = valor_aleatorio(tr.campo(n), rng, fmt, forzar=True, grande=True)
        vals[campo] = _ajustar(c, v)
        return True
    # not vacio(x): completar x
    if isinstance(nodo, ast.UnaryOp) and isinstance(nodo.op, ast.Not) and isinstance(nodo.operand, ast.Call) \
            and getattr(nodo.operand.func, "id", "") == "vacio":
        campo = nodo.operand.args[0].id
        vals[campo] = valor_aleatorio(tr.campo(campo), rng, fmt, forzar=True)
        return True
    if isinstance(nodo, ast.Call) and getattr(nodo.func, "id", "") == "vacio":
        vals[nodo.args[0].id] = None
        return True
    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name):
        if nodo.func.id == "existe":
            t, campo = nodo.args[0].value, nodo.args[1].value
            destino = nodo.args[2]
            candidatos = [r.get(campo) for r in ctx.por_tipo.get(t, []) if r.get(campo) is not None]
            if isinstance(destino, ast.Name) and candidatos:
                vals[destino.id] = rng.choice(candidatos)
                return True
            return False
        if nodo.func.id == "implica":
            cons = nodo.args[1]
            neg = isinstance(cons, ast.UnaryOp) and isinstance(cons.op, ast.Not)
            interior = cons.operand if neg else cons
            if isinstance(interior, ast.Call) and getattr(interior.func, "id", "") == "vacio":
                campo = interior.args[0].id
                c = tr.campo(campo)
                vals[campo] = valor_aleatorio(c, rng, fmt, forzar=True) if neg else None
                return True
            return _reparar(cons, tr, vals, ctx, rng, fmt, objetivos) or _remuestrear(nodo, tr, vals, ctx, rng, fmt)
    if isinstance(nodo, ast.BoolOp) and isinstance(nodo.op, ast.And):
        ok = True
        for sub in nodo.values:
            try:
                if Evaluador(ctx, vals).ev(sub):
                    continue
            except Nulo:
                continue
            ok = _reparar(sub, tr, vals, ctx, rng, fmt, objetivos) and ok
        return ok
    return _remuestrear(nodo, tr, vals, ctx, rng, fmt) or _frontera(nodo, tr, vals, ctx)


def _frontera(nodo: ast.AST, tr: TipoRegistro, vals: dict, ctx: ContextoArchivo) -> bool:
    """Para `campo <op> expr` (desigualdad) asigna al campo el valor límite que la cumple."""
    if not (isinstance(nodo, ast.Compare) and len(nodo.ops) == 1):
        return False
    op, izq, der = nodo.ops[0], nodo.left, nodo.comparators[0]
    espejo = {ast.Lt: ast.Gt, ast.LtE: ast.GtE, ast.Gt: ast.Lt, ast.GtE: ast.LtE}
    if not isinstance(izq, ast.Name) and isinstance(der, ast.Name) and type(op) in espejo:
        izq, der, op = der, izq, espejo[type(op)]()
    if not isinstance(izq, ast.Name) or not tr.campo(izq.id) or type(op) not in espejo:
        return False
    try:
        limite = Evaluador(ctx, vals).ev(der)
    except (Nulo, TypeError):
        return False
    c = tr.campo(izq.id)
    if isinstance(limite, date):
        paso = timedelta(days=1)
    else:
        paso = Decimal(1).scaleb(-(c.decimales if c.tipo == "decimal" else 0))
    if isinstance(op, ast.Lt):
        limite = limite - paso
    elif isinstance(op, ast.Gt):
        limite = limite + paso
    if isinstance(limite, Decimal) and limite < 0:
        return False  # los campos numéricos no tienen signo
    vals[izq.id] = _ajustar(c, limite)
    return True


def _remuestrear(nodo: ast.AST, tr: TipoRegistro, vals: dict, ctx: ContextoArchivo, rng: random.Random, fmt: str) -> bool:
    """Re-muestrea los campos referenciados por la regla hasta cumplirla."""
    nombres = [n.id for n in ast.walk(nodo) if isinstance(n, ast.Name) and tr.campo(n.id)]
    nombres = [n for n in dict.fromkeys(nombres) if tr.campo(n).tipo != "constante"]
    if not nombres:
        return False
    original = {n: vals.get(n) for n in nombres}
    for _ in range(200):
        for n in nombres:
            vals[n] = valor_aleatorio(tr.campo(n), rng, fmt)
        try:
            if Evaluador(ctx, vals).ev(nodo):
                return True
        except Nulo:
            return True
        except TypeError:
            break
    vals.update(original)
    return False
