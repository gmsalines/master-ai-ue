"""Batería de archivos de prueba para la evaluación funcional.

A partir de la especificación de referencia (gold) se generan archivos
válidos y archivos con UN error inyectado cada uno. Luego se mide si una
especificación extraída acepta los válidos y rechaza los erróneos, sin
importar cómo haya nombrado los campos o las reglas: es una medida de
equivalencia por comportamiento.
"""
from __future__ import annotations

import ast
import copy
import random
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable, Optional

from lxml import etree

from ..archivos import escribir
from ..dsl import Campo, Especificacion
from ..expr import FUNCIONES, parsear
from ..sintetico import generar_registros
from ..validador import validar
from ..valores import ErrorValor


@dataclass
class CasoPrueba:
    id: str
    categoria: str  # "valido" | "campo" | "estructura" | "regla"
    descripcion: str
    contenido: str

    @property
    def esperado_valido(self) -> bool:
        return self.categoria == "valido"


# ---------------------------------------------------------------- utilidades de texto


def _lineas(contenido: str) -> tuple[list[str], str]:
    eol = "\r\n" if "\r\n" in contenido else "\n"
    ls = contenido.split(eol)
    if ls and ls[-1] == "":
        ls.pop()
    return ls, eol


def _reemplazar_txt(contenido: str, n_linea: int, c: Campo, crudo: str) -> str:
    ls, eol = _lineas(contenido)
    l = ls[n_linea]
    crudo = crudo[: c.longitud].ljust(c.longitud)
    ls[n_linea] = l[: c.inicio - 1] + crudo + l[c.inicio - 1 + c.longitud :]
    return eol.join(ls) + eol


def _reemplazar_xml(contenido: str, n_elem: int, etiqueta: str, texto: Optional[str]) -> str:
    raiz = etree.fromstring(contenido.encode())
    el = [e for e in raiz if isinstance(e.tag, str)][n_elem]
    h = el.find(etiqueta)
    if texto is None:
        if h is not None:
            el.remove(h)
    else:
        if h is None:
            h = etree.SubElement(el, etiqueta)
        h.text = texto
    return etree.tostring(raiz, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode()


def _fuera_de_dominio(c: Campo) -> str:
    permitidos = {v.strip() for v in c.valores_permitidos or []}
    candidatos = ["Z" * c.longitud, "9" * c.longitud, "Q" * min(c.longitud, 3), "8" * c.longitud]
    for x in candidatos:
        if x not in permitidos and (c.tipo not in ("numerico", "decimal") or x.isdigit()):
            if not (x.isdigit() and any(p.isdigit() and int(p) == int(x) for p in permitidos)):
                return x
    return "Z" * c.longitud


# ---------------------------------------------------------------- mutaciones de campo


def mutaciones_de_campo(gold: Especificacion, contenido: str, registros) -> list[CasoPrueba]:
    casos = []
    xml = gold.formato == "xml"
    vistos = set()
    for idx, (cod, _) in enumerate(registros):
        if cod in vistos:
            continue
        vistos.add(cod)
        tr = gold.tipo(cod)
        ident = None if xml else gold.campo_identificador(tr)
        for c in tr.campos:
            if ident is not None and c.nombre == ident.nombre:
                continue
            etq = c.etiqueta_xml or c.nombre

            def caso(tipo: str, desc: str, crudo: Optional[str]):
                if xml:
                    nuevo = _reemplazar_xml(contenido, idx, etq, crudo)
                else:
                    nuevo = _reemplazar_txt(contenido, idx, c, "" if crudo is None else crudo)
                casos.append(CasoPrueba(f"campo:{cod}.{c.nombre}:{tipo}", "campo", desc, nuevo))

            if c.tipo in ("numerico", "decimal"):
                caso("no_numerico", f"{cod}.{c.nombre} con letras", "12A4" if xml else ("1" * (c.longitud - 1) + "A"))
            if c.tipo == "fecha":
                caso("fecha_invalida", f"{cod}.{c.nombre} con fecha inexistente", "2025-13-45"[: len(c.formato_fecha or "")] if xml else "9" * c.longitud)
            if c.valores_permitidos:
                caso("fuera_de_dominio", f"{cod}.{c.nombre} con valor no permitido", _fuera_de_dominio(c))
            if c.patron:
                caso("patron", f"{cod}.{c.nombre} sin respetar el patrón", "1" * min(c.longitud, 3))
            if c.tipo == "constante":
                caso("constante", f"{cod}.{c.nombre} con valor distinto al fijo", "X" * c.longitud)
            if c.obligatorio and c.tipo != "constante":
                caso("vacio", f"{cod}.{c.nombre} obligatorio vacío", None)
            if xml and c.tipo == "alfanumerico" and not c.valores_permitidos and not c.patron:
                caso("largo", f"{cod}.{c.nombre} más largo que el máximo", "W" * (c.longitud + 1))
    return casos


# ---------------------------------------------------------------- mutaciones de estructura


def mutaciones_de_estructura(gold: Especificacion, contenido: str, registros) -> list[CasoPrueba]:
    casos = []
    if gold.formato == "ancho_fijo":
        ls, eol = _lineas(contenido)
        casos.append(CasoPrueba("estructura:longitud", "estructura", "una línea con un carácter de más",
                                eol.join(ls[:1] + [ls[1] + "X"] + ls[2:]) + eol))
        ident = gold.campo_identificador(gold.tipos_registro[0])
        l = ls[1]
        l = l[: ident.inicio - 1] + "?" * ident.longitud + l[ident.inicio - 1 + ident.longitud :]
        casos.append(CasoPrueba("estructura:tipo_desconocido", "estructura", "registro de tipo inexistente", eol.join(ls[:1] + [l] + ls[2:]) + eol))
    else:
        raiz = etree.fromstring(contenido.encode())
        el = [e for e in raiz if isinstance(e.tag, str)][1]
        etree.SubElement(el, "ElementoInexistente").text = "X"
        casos.append(CasoPrueba("estructura:elemento_extra", "estructura", "elemento no definido",
                                etree.tostring(raiz, xml_declaration=True, encoding="UTF-8").decode()))
    # orden / ocurrencias a nivel de registros
    for tr in gold.tipos_registro:
        if tr.posicion == "primero" and len(registros) > 2:
            r2 = registros[1:] + registros[:1]
            casos.append(CasoPrueba(f"estructura:{tr.codigo}_no_primero", "estructura", f"{tr.codigo} fuera de lugar", escribir(gold, r2)))
        if tr.max_ocurrencias == 1:
            r2 = [r for r in registros if r[0] != tr.codigo]
            casos.append(CasoPrueba(f"estructura:{tr.codigo}_faltante", "estructura", f"falta el registro {tr.codigo}", escribir(gold, r2)))
    return [c for c in casos if not validar(gold, c.contenido).ok]


# ---------------------------------------------------------------- mutaciones de regla


def _campos_de_regla(gold: Especificacion, regla) -> list[tuple[str, str]]:
    """(tipo_registro, campo) involucrados en la regla."""
    out = []
    nodo = parsear(regla.expresion)
    for n in ast.walk(nodo):
        if isinstance(n, ast.Name) and n.id not in FUNCIONES and regla.tipo_registro:
            out.append((regla.tipo_registro, n.id))
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("valor", "sumar", "existe") and len(n.args) >= 2:
            out.append((n.args[0].value, n.args[1].value))
    return list(dict.fromkeys(out))


def _variantes(c: Campo, v, rng: random.Random) -> list:
    out = []
    if isinstance(v, Decimal):
        paso = Decimal(1).scaleb(-(c.decimales if c.tipo == "decimal" else 0))
        out += [v + paso, v + 100 * paso, max(Decimal(0), v - paso), Decimal(0)]
    elif hasattr(v, "year"):
        out += [v + timedelta(days=1), v + timedelta(days=400), v - timedelta(days=400)]
    if c.valores_permitidos:
        out += [x for x in c.valores_permitidos if x != v]
    if not c.obligatorio:
        out.append(None)
        if v is None:
            from ..sintetico import valor_aleatorio
            out.append(valor_aleatorio(c, rng, "ancho_fijo", forzar=True))
    if c.tipo == "alfanumerico" and v and not c.valores_permitidos:
        out.append("ZZ" + str(v)[: max(0, c.longitud - 2)])
    return out


def mutaciones_de_regla(gold: Especificacion, registros, semilla: int = 0) -> list[CasoPrueba]:
    rng = random.Random(semilla)
    casos = []
    for regla in gold.reglas:
        encontrado = False
        candidatos: list[tuple[str, Callable]] = []
        # las constantes de la propia regla son buenos candidatos (p. ej. moneda = "MNL")
        consts = [n.value for n in ast.walk(parsear(regla.expresion)) if isinstance(n, ast.Constant)
                  and isinstance(n.value, str) and not (gold.tipo(n.value))]
        for cod, campo in _campos_de_regla(gold, regla):
            c = gold.tipo(cod).campo(campo) if gold.tipo(cod) else None
            if c is None:
                continue
            idxs = [i for i, (k, _) in enumerate(registros) if k == cod][:3]
            for i in idxs:
                extra = [k for k in consts if c.tipo == "alfanumerico" and len(k) <= c.longitud
                         and k != registros[i][1].get(campo) and not gold.tipo(cod).campo(k)]
                for nv in _variantes(c, registros[i][1].get(campo), rng) + extra:
                    candidatos.append((f"{cod}[{i}].{campo}={nv}", [(i, campo, nv)]))
        # combinaciones de dos cambios en el mismo registro (p. ej. activar el antecedente de implica())
        simples = [x for x in candidatos]
        for a in range(len(simples)):
            for b in range(a + 1, len(simples)):
                (da, ca), (db, cb) = simples[a], simples[b]
                if ca[0][0] == cb[0][0] and ca[0][1] != cb[0][1]:
                    candidatos.append((f"{da} y {db}", ca + cb))
        # mutaciones de cantidad: quitar / duplicar un registro repetible
        for tr in gold.tipos_registro:
            idxs = [i for i, (k, _) in enumerate(registros) if k == tr.codigo]
            if len(idxs) > 1 or (idxs and tr.max_ocurrencias != 1):
                candidatos.append((f"quitar {tr.codigo}", ("quitar", idxs[-1])))
                candidatos.append((f"duplicar {tr.codigo}", ("duplicar", idxs[-1])))
        for desc, accion in candidatos:
            r2 = copy.deepcopy(registros)
            if accion[0] == "quitar":
                r2.pop(accion[1])
            elif accion[0] == "duplicar":
                r2.insert(accion[1], copy.deepcopy(r2[accion[1]]))
            else:
                for i, campo, nv in accion:
                    r2[i][1][campo] = nv
            try:
                contenido = escribir(gold, r2)
            except ErrorValor:
                continue
            inf = validar(gold, contenido)
            solo_reglas = all(h.origen == "regla" for h in inf.errores)
            if regla.id in inf.reglas_violadas() and solo_reglas:
                casos.append(CasoPrueba(f"regla:{regla.id}", "regla", f"viola {regla.id} ({desc})", contenido))
                encontrado = True
                break
        if not encontrado:
            casos.append(CasoPrueba(f"regla:{regla.id}:sin_mutacion", "sin_mutacion", "no se encontró una mutación aislada", ""))
    return casos


def construir_bateria(gold: Especificacion, n_validos: int = 5, semilla_base: int = 100) -> list[CasoPrueba]:
    casos: list[CasoPrueba] = []
    for k in range(n_validos):
        regs, _ = generar_registros(gold, semilla_base + k)
        casos.append(CasoPrueba(f"valido:{k}", "valido", "archivo válido", escribir(gold, regs)))
    regs, _ = generar_registros(gold, semilla_base)
    base = escribir(gold, regs)
    casos += mutaciones_de_campo(gold, base, regs)
    casos += mutaciones_de_estructura(gold, base, regs)
    casos += mutaciones_de_regla(gold, regs, semilla_base)
    # control de calidad: la batería debe ser coherente con la especificación de referencia
    for c in casos:
        if c.categoria == "sin_mutacion":
            continue
        ok = validar(gold, c.contenido).ok
        if ok != c.esperado_valido:
            raise AssertionError(f"caso {c.id} inconsistente con la referencia")
    return casos
