"""Extracción por secciones, con recuperación y escalado selectivo a la IA.

En lugar de pedir la especificación completa en una sola respuesta, se arma en
pasos pequeños, y cada paso recibe SOLO los fragmentos del manual que necesita:

    1. esqueleto   formato, longitud y tipos de registro (sin campos)
    2. campos      por tipo de registro:
                     a. el parser convencional (sin IA) lee la tabla del registro;
                     b. si su lectura es completa y el registro no remite a notas o
                        tablas externas, se acepta tal cual  -> 0 tokens;
                     c. si no, la IA recibe el borrador y devuelve SOLO correcciones;
                     d. si el parser no pudo leer nada, la IA extrae los campos.
    3. reglas      con el índice de registros y campos ya extraídos.

Así la IA solo interviene donde el código no alcanza (prosa, notas al pie, reglas),
lo que reduce drásticamente los tokens y permite procesar manuales largos. El
resultado pasa por el mismo verificador y bucle de corrección que el modo completo.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..base_sin_ia import extraer_base
from .prompts import SISTEMA_BREVE
from .proveedores import ErrorProveedor, Proveedor
from .recuperacion import (
    TERMINOS_ESQUELETO, TERMINOS_NOTAS, TERMINOS_REGLAS, dividir, indice_de_titulos, patron_registro, seleccionar,
    seleccionar_indices, unir,
)


@dataclass
class PasoSeccion:
    nombre: str
    ok: bool
    tokens_entrada: int
    tokens_salida: int
    latencia_s: float
    detalle: str = ""


@dataclass
class Borrador:
    spec: Optional[dict]
    pasos: list[PasoSeccion] = field(default_factory=list)
    error: Optional[str] = None


_REMITE = re.compile(r"ver (nota|tabla)|nota \(\d+\)|tabla de c[oó]digos|(?<![9xXvV])\(\d\)", re.I)


def mensajes_con_contexto(fragmentos_texto: str, pedido: str) -> list[dict]:
    return [
        {"role": "system", "content": SISTEMA_BREVE},
        {"role": "user", "content": "FRAGMENTOS DEL MANUAL:\n" + fragmentos_texto + "\n\nTAREA:\n" + pedido},
    ]


def contexto_registro(frags, tr: dict, presupuesto: int = 4000) -> str:
    """La sección del propio registro y, si remite a notas o tablas de códigos, esas notas."""
    pats = patron_registro(tr.get("codigo", ""), tr.get("nombre", ""))
    idx = seleccionar_indices(frags, [str(tr.get("codigo", "")), str(tr.get("nombre", ""))], presupuesto,
                              patrones=pats, max_fragmentos=1)
    propio = unir(frags, idx)
    if _REMITE.search(propio):
        titulos_notas = [f.indice for f in frags if re.search(r"^notas?\b|tabla|c[oó]digos|anexo|referencias", f.titulo, re.I)]
        idx += titulos_notas[:2] if titulos_notas else seleccionar_indices(
            frags, TERMINOS_NOTAS, presupuesto // 2, patrones=(r"^\(\d\)",), max_fragmentos=1)
    return unir(frags, idx)


def contexto_reglas(frags, presupuesto: int = 3500) -> str:
    return seleccionar(frags, TERMINOS_REGLAS, presupuesto, max_fragmentos=3,
                       patrones=(r"validaci", r"control", r"regla", r"debe(r[aá]n?)? ser igual", r"coincid"))


def contexto_esqueleto(frags, presupuesto: int = 2500) -> str:
    general = seleccionar(frags, TERMINOS_ESQUELETO, presupuesto, max_fragmentos=2,
                          patrones=(r"general", r"estructura", r"caracter[ií]sticas", r"aspectos"))
    return general + "\n\nÍNDICE DEL MANUAL (título: primera línea de cada sección):\n" + indice_de_titulos(frags)


# ------------------------------------------------------------------ pedidos


def _pedido_esqueleto() -> str:
    return (
        "Extraé SOLO los datos globales y la lista de tipos de registro, SIN campos ni reglas:\n"
        '{"nombre": "..", "version": "..", "formato": "ancho_fijo|xml", "longitud_registro": n|null, '
        '"separador_lineas": "LF|CRLF", "etiqueta_raiz_xml": "..|null", '
        '"tipos_registro": [{"codigo": "..", "nombre": "..", "min_ocurrencias": n, "max_ocurrencias": n|null, '
        '"posicion": "primero|ultimo|cualquiera", "etiqueta_xml": "..|null"}]}'
    )


def _pedido_campos(esq: dict, tr: dict) -> str:
    xml = esq.get("formato") == "xml"
    return (
        f"Formato {esq.get('formato')}, longitud de registro {esq.get('longitud_registro')}. "
        f"Extraé TODOS los campos del registro \"{tr.get('codigo')}\" ({tr.get('nombre')}), en orden. "
        + ("Cada campo lleva etiqueta_xml exacta; sin inicio ni fin. " if xml else
           "Cada campo lleva inicio y longitud; incluí rellenos y el identificador constante. ")
        + 'Respondé {"campos": [ ... ]}'
    )


def _pedido_revision(esq: dict, tr: dict, borrador: list[dict]) -> str:
    compacto = json.dumps([{k: v for k, v in c.items() if k != "evidencia" and v not in (None, False, 0, "") or k == "obligatorio"}
                           for c in borrador], ensure_ascii=False, separators=(",", ":"))
    return (
        f"Un parser leyó la tabla del registro \"{tr.get('codigo')}\" ({tr.get('nombre')}) y obtuvo este borrador "
        f"(formato {esq.get('formato')}, longitud {esq.get('longitud_registro')}):\n{compacto}\n"
        "Compará con los fragmentos (incluidas notas al pie y tablas de códigos) y devolvé SOLO lo que hay que cambiar:\n"
        '{"correcciones": {"<nombre>": {atributos que cambian, p. ej. valores_permitidos, obligatorio, tipo, '
        'formato_fecha, decimales, valor_constante}}, "agregar": [campos faltantes completos], "eliminar": ["<nombre>"]}\n'
        'Si está todo bien: {"correcciones": {}}'
    )


def _pedido_reglas(spec: dict) -> str:
    indice = {str(t.get("codigo")): [c.get("nombre") for c in t.get("campos", []) if isinstance(c, dict)]
              for t in spec.get("tipos_registro", []) if isinstance(t, dict)}
    unicos = [t.get("codigo") for t in spec.get("tipos_registro", []) if isinstance(t, dict) and t.get("max_ocurrencias") == 1]
    return (
        f"Registros y campos: {json.dumps(indice, ensure_ascii=False, separators=(',', ':'))}. "
        f"Registros únicos (para valor()): {unicos}.\n"
        "Extraé TODAS las validaciones que exige el manual como reglas, usando exactamente estos códigos y nombres. "
        'Respondé {"reglas": [ ... ]} (o {"reglas": []}).'
    )


# ------------------------------------------------------------------ utilidades


def _llamar(proveedor: Proveedor, mensajes: list[dict], nombre: str, max_tokens: int, temperatura: float,
            extraer_json: Callable[[str], dict], pasos: list[PasoSeccion], reintentos: int = 1) -> dict:
    for intento in range(reintentos + 1):
        r = proveedor.completar(mensajes, modo_json=True, temperatura=temperatura, max_tokens=max_tokens)
        try:
            d = extraer_json(r.texto)
            pasos.append(PasoSeccion(nombre, True, r.tokens_entrada, r.tokens_salida, r.latencia_s))
            return d
        except (ValueError, json.JSONDecodeError) as e:
            pasos.append(PasoSeccion(nombre, False, r.tokens_entrada, r.tokens_salida, r.latencia_s, f"JSON inválido: {e}"))
            mensajes = mensajes[:-1] + [{"role": "user", "content": mensajes[-1]["content"] + "\nIMPORTANTE: respondé solo JSON válido y breve."}]
    raise ValueError(f"no se obtuvo JSON válido en el paso '{nombre}'")


def lectura_confiable(campos: list[dict], longitud: Optional[int], codigo: str) -> bool:
    """El parser leyó el registro completo: posiciones contiguas que cubren la línea e identificador constante."""
    if not campos or not longitud:
        return False
    esperado = 1
    for c in sorted(campos, key=lambda c: c.get("inicio") or 0):
        if c.get("inicio") != esperado or not c.get("longitud"):
            return False
        esperado += c["longitud"]
    ident = any(c.get("tipo") == "constante" and str(c.get("valor_constante", "")).strip() == str(codigo).strip() for c in campos)
    return esperado - 1 == longitud and ident


def aplicar_revision(borrador: list[dict], rev: dict) -> list[dict]:
    campos = [dict(c) for c in borrador]
    nombres = [c.get("nombre") for c in campos]
    for nombre, cambios in (rev.get("correcciones") or {}).items():
        if nombre in nombres and isinstance(cambios, dict):
            campos[nombres.index(nombre)].update(cambios)
    eliminar = set(rev.get("eliminar") or [])
    campos = [c for c in campos if c.get("nombre") not in eliminar]
    campos += [c for c in rev.get("agregar") or [] if isinstance(c, dict)]
    def pos(c):
        try:
            return int(c.get("inicio"))
        except (TypeError, ValueError):
            return 10**9
    if all(c.get("inicio") for c in campos):
        campos.sort(key=pos)
    return campos


# ------------------------------------------------------------------ pipeline


def borrador_por_secciones(manual: str, proveedor: Proveedor, extraer_json, max_tokens: int = 3000,
                           temperatura: float = 0.0, al_paso=None, usar_codigo: bool = True,
                           presupuesto: int = 5000) -> Borrador:
    frags = dividir(manual)
    b = Borrador(None)
    base = extraer_base(manual) if usar_codigo else {"tipos_registro": []}
    base_por_codigo = {str(t["codigo"]).strip().upper(): t for t in base.get("tipos_registro", [])}

    def avisar():
        if al_paso:
            al_paso(b.pasos[-1])

    try:
        esq = _llamar(proveedor, mensajes_con_contexto(contexto_esqueleto(frags), _pedido_esqueleto()),
                      "esqueleto", max_tokens, temperatura, extraer_json, b.pasos)
        avisar()
        trs = esq.get("tipos_registro") or []
        if not isinstance(trs, list) or not trs:
            b.error = "el paso de esqueleto no devolvió tipos de registro"
            b.spec = esq
            return b
        for tr in trs:
            if not isinstance(tr, dict):
                continue
            codigo = str(tr.get("codigo", ""))
            ctx = contexto_registro(frags, tr)
            leido = base_por_codigo.get(codigo.strip().upper())
            campos_base = (leido or {}).get("campos") or []
            if campos_base and esq.get("formato") != "xml":
                # la sección del registro, ¿remite a notas o tablas que el parser no interpreta?
                seccion = seleccionar(frags, [codigo, str(tr.get("nombre", ""))], 4000,
                                      patrones=patron_registro(codigo, str(tr.get("nombre", ""))), max_fragmentos=1)
                if lectura_confiable(campos_base, esq.get("longitud_registro"), codigo) and not _REMITE.search(seccion):
                    tr["campos"] = campos_base
                    b.pasos.append(PasoSeccion(f"campos {codigo} (por código)", True, 0, 0, 0.0))
                    avisar()
                    continue
                rev = _llamar(proveedor, mensajes_con_contexto(ctx, _pedido_revision(esq, tr, campos_base)),
                              f"campos {codigo} (revisión)", max_tokens, temperatura, extraer_json, b.pasos)
                avisar()
                tr["campos"] = aplicar_revision(campos_base, rev)
                continue
            d = _llamar(proveedor, mensajes_con_contexto(ctx, _pedido_campos(esq, tr)), f"campos {codigo}",
                        max_tokens, temperatura, extraer_json, b.pasos)
            avisar()
            tr["campos"] = d.get("campos", d if isinstance(d, list) else [])
        esq["tipos_registro"] = trs
        d = _llamar(proveedor, mensajes_con_contexto(contexto_reglas(frags), _pedido_reglas(esq)),
                    "reglas", max_tokens, temperatura, extraer_json, b.pasos)
        avisar()
        esq["reglas"] = d.get("reglas", [])
        b.spec = esq
    except ErrorProveedor as e:
        b.error = str(e)
    except ValueError as e:
        b.error = str(e)
    return b
