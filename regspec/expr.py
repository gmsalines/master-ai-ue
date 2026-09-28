"""Lenguaje de reglas: análisis estático (tipos y referencias) y evaluación segura.

Sintaxis: subconjunto de expresiones Python. Nunca se usa eval(); el árbol se
recorre con una lista blanca de nodos.

Ámbito "registro": los nombres sueltos son campos del registro en curso.
Ámbito "archivo": no hay nombres sueltos; se accede a los datos con funciones.

Funciones:
  implica(a, b)                       a -> b
  vacio(campo)                        True si el campo no fue informado
  abs(x), redondear(x, n)
  longitud(texto)
  contar("TIPO" [, "campo", valor]...)            cantidad de registros (con filtros opcionales)
  sumar("TIPO", "campo" [, "campo", valor]...)    suma de un campo numérico (con filtros opcionales)
  valor("TIPO", "campo")              valor del campo en el único registro de ese tipo
  existe("TIPO", "campo", valor)      True si algún registro de TIPO tiene campo == valor
Operadores: + - * /, == != < <= > >=, in / not in (con lista de constantes), and / or / not.
"""
from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from .dsl import Especificacion, TipoRegistro

NUM, STR, DATE, BOOL = "num", "texto", "fecha", "bool"
_TIPO_CAMPO = {"numerico": NUM, "decimal": NUM, "fecha": DATE, "alfanumerico": STR, "constante": STR}

FUNCIONES = {"implica", "vacio", "abs", "redondear", "longitud", "contar", "sumar", "valor", "existe"}

_CMP = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}
_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


class ErrorExpresion(Exception):
    """Error estático (la regla está mal formada) con sugerencia opcional."""

    def __init__(self, mensaje: str, sugerencia: str = ""):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.sugerencia = sugerencia


class Nulo(Exception):
    """Se referenció un valor no informado: la regla no es aplicable a este registro."""


def _sugerir(nombre: str, opciones: list[str]) -> str:
    cerca = difflib.get_close_matches(nombre, opciones, n=3, cutoff=0.5)
    base = f"Disponibles: {', '.join(opciones)}." if len(opciones) <= 30 else ""
    return (f"¿Quisiste decir {', '.join(repr(c) for c in cerca)}? " if cerca else "") + base


def parsear(expresion: str) -> ast.AST:
    try:
        return ast.parse(expresion.strip(), mode="eval").body
    except SyntaxError as e:
        raise ErrorExpresion(
            f"sintaxis inválida: {e.msg}",
            "Usá sintaxis tipo Python: and/or/not, ==, comillas dobles para textos, funciones del lenguaje de reglas.",
        )


# ------------------------------------------------------------------ chequeo estático


@dataclass
class Chequeador:
    spec: Especificacion
    registro: Optional[TipoRegistro]  # None => ámbito archivo

    def verificar(self, expresion: str) -> None:
        nodo = parsear(expresion)
        t = self.tipo(nodo)
        if t != BOOL:
            raise ErrorExpresion(f"la expresión debe ser booleana y es de tipo {t}", "Una regla debe ser una condición que dé verdadero/falso.")

    # -- helpers
    def _tipo_registro(self, nodo: ast.AST) -> TipoRegistro:
        if not (isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)):
            raise ErrorExpresion("el primer argumento debe ser el código del tipo de registro entre comillas")
        tr = self.spec.tipo(nodo.value)
        if tr is None:
            cods = [t.codigo for t in self.spec.tipos_registro]
            raise ErrorExpresion(f"tipo de registro '{nodo.value}' inexistente", _sugerir(nodo.value, cods))
        return tr

    def _campo_de(self, tr: TipoRegistro, nodo: ast.AST):
        if not (isinstance(nodo, ast.Constant) and isinstance(nodo.value, str)):
            raise ErrorExpresion("el nombre de campo debe ir entre comillas")
        c = tr.campo(nodo.value)
        if c is None:
            raise ErrorExpresion(
                f"el campo '{nodo.value}' no existe en el registro '{tr.codigo}'",
                _sugerir(nodo.value, [x.nombre for x in tr.campos]),
            )
        return c

    def _filtros(self, tr: TipoRegistro, args: list[ast.AST]) -> None:
        if len(args) % 2:
            raise ErrorExpresion("los filtros van de a pares: \"campo\", valor")
        for i in range(0, len(args), 2):
            c = self._campo_de(tr, args[i])
            tv = self.tipo(args[i + 1])
            if tv != _TIPO_CAMPO[c.tipo]:
                raise ErrorExpresion(f"el filtro sobre '{c.nombre}' compara {_TIPO_CAMPO[c.tipo]} con {tv}")

    # -- inferencia de tipos
    def tipo(self, n: ast.AST) -> str:
        if isinstance(n, ast.Constant):
            if isinstance(n.value, bool):
                return BOOL
            if isinstance(n.value, (int, float)):
                return NUM
            if isinstance(n.value, str):
                return STR
            raise ErrorExpresion(f"constante no soportada: {n.value!r}")
        if isinstance(n, ast.Name):
            if self.registro is None:
                raise ErrorExpresion(
                    f"'{n.id}' no está definido: en reglas de ámbito archivo no hay campos sueltos",
                    'Usá valor("TIPO","campo"), sumar(...) o contar(...).',
                )
            c = self.registro.campo(n.id)
            if c is None:
                raise ErrorExpresion(
                    f"el campo '{n.id}' no existe en el registro '{self.registro.codigo}'",
                    _sugerir(n.id, [x.nombre for x in self.registro.campos]),
                )
            return _TIPO_CAMPO[c.tipo]
        if isinstance(n, ast.BoolOp):
            for v in n.values:
                if self.tipo(v) != BOOL:
                    raise ErrorExpresion("and/or requieren operandos booleanos")
            return BOOL
        if isinstance(n, ast.UnaryOp):
            t = self.tipo(n.operand)
            if isinstance(n.op, ast.Not):
                if t != BOOL:
                    raise ErrorExpresion("not requiere un operando booleano")
                return BOOL
            if isinstance(n.op, (ast.USub, ast.UAdd)):
                if t != NUM:
                    raise ErrorExpresion("el signo requiere un operando numérico")
                return NUM
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            a, b = self.tipo(n.left), self.tipo(n.right)
            if a == NUM and b == NUM:
                return NUM
            if a == DATE and b == DATE and isinstance(n.op, ast.Sub):
                return NUM
            raise ErrorExpresion(f"operación {_BIN[type(n.op)]} no válida entre {a} y {b}")
        if isinstance(n, ast.Compare):
            izq = self.tipo(n.left)
            for op, der in zip(n.ops, n.comparators):
                if isinstance(op, (ast.In, ast.NotIn)):
                    if not isinstance(der, (ast.Tuple, ast.List)) or not der.elts:
                        raise ErrorExpresion("'in' requiere una lista de constantes, p. ej. x in (\"A\", \"B\")")
                    for e in der.elts:
                        if not isinstance(e, ast.Constant) or self.tipo(e) != izq:
                            raise ErrorExpresion(f"la lista de 'in' debe contener constantes de tipo {izq}")
                elif type(op) in _CMP:
                    t = self.tipo(der)
                    if t != izq:
                        raise ErrorExpresion(f"comparación {_CMP[type(op)]} entre tipos distintos ({izq} y {t})",
                                             "Si comparás un código numérico con texto, revisá el tipo del campo o usá comillas.")
                else:
                    raise ErrorExpresion("operador de comparación no soportado")
            return BOOL
        if isinstance(n, ast.Call):
            return self._tipo_llamada(n)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            raise ErrorExpresion(
                f"'{n.value.id}.{n.attr}' no es válido en el lenguaje de reglas",
                f'Para un campo de otro registro único escribí valor("{n.value.id}", "{n.attr}").',
            )
        raise ErrorExpresion(f"construcción no permitida: {type(n).__name__}",
                             "Solo se admiten campos, constantes, operadores aritméticos/lógicos/de comparación y las funciones del lenguaje.")

    def _tipo_llamada(self, n: ast.Call) -> str:
        if not isinstance(n.func, ast.Name) or n.keywords:
            raise ErrorExpresion("solo se admiten llamadas simples a funciones del lenguaje, sin argumentos con nombre")
        f, a = n.func.id, n.args
        if f not in FUNCIONES:
            raise ErrorExpresion(f"función '{f}' desconocida", _sugerir(f, sorted(FUNCIONES)))
        if f == "implica":
            if len(a) != 2 or self.tipo(a[0]) != BOOL or self.tipo(a[1]) != BOOL:
                raise ErrorExpresion("implica(condicion, consecuencia) requiere dos booleanos")
            return BOOL
        if f == "vacio":
            if len(a) != 1 or not isinstance(a[0], ast.Name):
                raise ErrorExpresion("vacio(campo) recibe un nombre de campo del registro")
            self.tipo(a[0])
            return BOOL
        if f == "abs":
            if len(a) != 1 or self.tipo(a[0]) != NUM:
                raise ErrorExpresion("abs(x) requiere un número")
            return NUM
        if f == "redondear":
            if len(a) != 2 or self.tipo(a[0]) != NUM or not (isinstance(a[1], ast.Constant) and isinstance(a[1].value, int)):
                raise ErrorExpresion("redondear(x, n) requiere un número y una cantidad entera de decimales")
            return NUM
        if f == "longitud":
            if len(a) != 1 or self.tipo(a[0]) != STR:
                raise ErrorExpresion("longitud(texto) requiere un texto")
            return NUM
        if not a:
            raise ErrorExpresion(f"{f} requiere el código de tipo de registro como primer argumento")
        tr = self._tipo_registro(a[0])
        if f == "contar":
            self._filtros(tr, a[1:])
            return NUM
        if f == "sumar":
            if len(a) < 2:
                raise ErrorExpresion('sumar("TIPO", "campo") requiere el campo a sumar')
            c = self._campo_de(tr, a[1])
            if _TIPO_CAMPO[c.tipo] != NUM:
                raise ErrorExpresion(f"sumar requiere un campo numérico y '{c.nombre}' es {c.tipo}")
            self._filtros(tr, a[2:])
            return NUM
        if f == "valor":
            if len(a) != 2:
                raise ErrorExpresion('valor("TIPO", "campo") recibe dos argumentos')
            if tr.max_ocurrencias != 1:
                raise ErrorExpresion(
                    f"valor() solo aplica a registros únicos y '{tr.codigo}' puede repetirse",
                    f"Si la validación es sobre cada registro '{tr.codigo}', usá ambito \"registro\", "
                    f"tipo_registro \"{tr.codigo}\" y el campo por su nombre (p. ej. {a[1].value if isinstance(a[1], ast.Constant) else 'campo'} > 0). "
                    "Para totales usá sumar() o contar().",
                )
            return _TIPO_CAMPO[self._campo_de(tr, a[1]).tipo]
        if f == "existe":
            if len(a) != 3:
                raise ErrorExpresion('existe("TIPO", "campo", valor) recibe tres argumentos')
            c = self._campo_de(tr, a[1])
            if self.tipo(a[2]) != _TIPO_CAMPO[c.tipo]:
                raise ErrorExpresion("existe(): el valor buscado no es del tipo del campo")
            return BOOL
        raise AssertionError(f)


def verificar_regla(spec: Especificacion, expresion: str, tipo_registro: Optional[str]) -> None:
    tr = spec.tipo(tipo_registro) if tipo_registro else None
    Chequeador(spec, tr).verificar(expresion)


def campos_referenciados(expresion: str) -> set[str]:
    """Nombres sueltos (campos del registro) usados en la expresión, excluidos los de funciones."""
    try:
        nodo = parsear(expresion)
    except ErrorExpresion:
        return set()
    return {n.id for n in ast.walk(nodo) if isinstance(n, ast.Name) and n.id not in FUNCIONES}


# ------------------------------------------------------------------ evaluación


@dataclass
class ContextoArchivo:
    """Registros ya convertidos a valores Python, agrupados por código."""

    por_tipo: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def _num(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        raise TypeError("booleano en contexto numérico")
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    raise TypeError(f"valor no numérico: {v!r}")


def _norm(v: Any) -> Any:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return Decimal(str(v))
    return v


class Evaluador:
    def __init__(self, ctx: ContextoArchivo, registro: Optional[dict[str, Any]] = None):
        self.ctx = ctx
        self.reg = registro

    def evaluar(self, expresion: str) -> bool:
        return bool(self.ev(parsear(expresion)))

    def _pares(self, args):
        return [(args[i].value, self.ev(args[i + 1])) for i in range(0, len(args), 2)]

    def _filtrar(self, tipo: str, pares) -> list[dict[str, Any]]:
        regs = self.ctx.por_tipo.get(tipo, [])
        return [r for r in regs if all(_norm(r.get(c)) == _norm(v) for c, v in pares)]

    def ev(self, n: ast.AST) -> Any:
        if isinstance(n, ast.Constant):
            return _norm(n.value)
        if isinstance(n, ast.Name):
            v = (self.reg or {}).get(n.id)
            if v is None:
                raise Nulo(n.id)
            return v
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                return all(self.ev(v) for v in n.values)
            return any(self.ev(v) for v in n.values)
        if isinstance(n, ast.UnaryOp):
            v = self.ev(n.operand)
            if isinstance(n.op, ast.Not):
                return not v
            return -_num(v) if isinstance(n.op, ast.USub) else _num(v)
        if isinstance(n, ast.BinOp):
            a, b = self.ev(n.left), self.ev(n.right)
            if isinstance(a, date) and isinstance(b, date):
                return Decimal((a - b).days)
            a, b = _num(a), _num(b)
            op = type(n.op)
            if op is ast.Add:
                return a + b
            if op is ast.Sub:
                return a - b
            if op is ast.Mult:
                return a * b
            if b == 0:
                raise Nulo("división por cero")
            return a / b
        if isinstance(n, ast.Compare):
            izq = self.ev(n.left)
            for op, nd in zip(n.ops, n.comparators):
                if isinstance(op, (ast.In, ast.NotIn)):
                    lista = [_norm(e.value) for e in nd.elts]
                    ok = izq in lista
                    ok = ok if isinstance(op, ast.In) else not ok
                    if not ok:
                        return False
                    continue
                der = self.ev(nd)
                t = type(op)
                ok = {
                    ast.Eq: lambda: izq == der, ast.NotEq: lambda: izq != der,
                    ast.Lt: lambda: izq < der, ast.LtE: lambda: izq <= der,
                    ast.Gt: lambda: izq > der, ast.GtE: lambda: izq >= der,
                }[t]()
                if not ok:
                    return False
                izq = der
            return True
        if isinstance(n, ast.Call):
            f, a = n.func.id, n.args
            if f == "implica":
                return (not self.ev(a[0])) or bool(self.ev(a[1]))
            if f == "vacio":
                v = (self.reg or {}).get(a[0].id)
                return v is None or (isinstance(v, str) and v.strip() == "")
            if f == "abs":
                return abs(_num(self.ev(a[0])))
            if f == "redondear":
                q = Decimal(1).scaleb(-a[1].value)
                return _num(self.ev(a[0])).quantize(q, rounding=ROUND_HALF_UP)
            if f == "longitud":
                return Decimal(len(str(self.ev(a[0]))))
            tipo = a[0].value
            if f == "contar":
                return Decimal(len(self._filtrar(tipo, self._pares(a[1:]))))
            if f == "sumar":
                campo = a[1].value
                return sum((_num(r[campo]) for r in self._filtrar(tipo, self._pares(a[2:])) if r.get(campo) is not None), Decimal(0))
            if f == "valor":
                regs = self.ctx.por_tipo.get(tipo, [])
                if not regs or regs[0].get(a[1].value) is None:
                    raise Nulo(f"{tipo}.{a[1].value}")
                return regs[0][a[1].value]
            if f == "existe":
                buscado = _norm(self.ev(a[2]))
                return any(_norm(r.get(a[1].value)) == buscado for r in self.ctx.por_tipo.get(tipo, []))
        raise ErrorExpresion(f"nodo no soportado en evaluación: {type(n).__name__}")
