"""Estilo visual de la app: sobrio, de aplicación de trabajo.

Fondo gris muy claro, superficies blancas, texto casi negro y un verde profundo para lo importante; el acento
turquesa se usa solo donde hay que mirar. Los colores de estado son apagados: verde si coincide, rojo si hay
diferencias y gris si la llave está en un solo grupo. Cada sección dice si la hace el código («Determinístico»)
o si la asiste un modelo de lenguaje («Asistido por IA»): la IA propone y el verificador decide.
"""
from __future__ import annotations

import html

import pandas as pd

pd.set_option("styler.render.max_elements", 5_000_000)  # tablas de hasta 5.000 filas con muchas columnas

FONDO, SUPERFICIE, BORDE = "#F7F8F7", "#FFFFFF", "#E3E7E5"
TEXTO, TEXTO_2, PROFUNDO = "#17211F", "#66716D", "#0D2927"
ACENTO, ACENTO_SUAVE = "#2FAF9F", "#DDEFEA"
OK, DIF, SOLO, AVISO = "#2E7D5B", "#B5473A", "#9AA5A1", "#B7791F"

CSS = f"""
<style>
[data-testid="stMainBlockContainer"] {{ padding-top: 2.4rem; max-width: 1320px; }}
[data-testid="stHeader"] {{ background: transparent; }}
.stApp {{ font-feature-settings: "cv11", "ss01"; }}

/* encabezado: título, bajada y una línea fina */
.encabezado {{ margin: 0 0 1.3rem 0; padding: 0 0 1.05rem 0; border-bottom: 1px solid {BORDE}; }}
.encabezado .meta {{ font-size: .78rem; font-weight: 500; letter-spacing: .06em; text-transform: uppercase;
                     color: {TEXTO_2}; margin: 0 0 .45rem 0; }}
.encabezado h1 {{ font-size: 1.95rem; font-weight: 650; letter-spacing: -0.015em; line-height: 1.2;
                  color: {TEXTO}; margin: 0; padding: 0; }}
.encabezado p {{ color: {TEXTO_2}; margin: .45rem 0 0 0; font-size: .98rem; line-height: 1.5; max-width: 78ch; }}

/* pestañas: texto gris, la activa en verde profundo con subrayado */
.stTabs [data-baseweb="tab-list"] {{ gap: 1.6rem; border-bottom: 1px solid {BORDE}; }}
.stTabs [data-baseweb="tab"] {{ padding: .4rem 0; font-weight: 500; color: {TEXTO_2}; }}
.stTabs [aria-selected="true"] {{ color: {PROFUNDO}; }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: {PROFUNDO}; height: 2px; }}

h2, h3 {{ font-weight: 600 !important; letter-spacing: -0.01em; color: {TEXTO}; }}
[data-testid="stMain"] h2 {{ font-size: 1.4rem !important; }}
[data-testid="stMain"] h3 {{ font-size: 1.22rem !important; padding-top: 1.1rem !important; }}

/* qué hace cada sección: el código o un modelo de lenguaje */
.etiquetas {{ display: flex; flex-wrap: wrap; gap: .45rem 1.4rem; align-items: baseline; margin: .2rem 0 1.1rem 0; }}
.etiquetas div {{ display: flex; align-items: baseline; gap: .55rem; color: {TEXTO_2}; font-size: .88rem; }}
.etiqueta {{ display: inline-block; white-space: nowrap; font-size: .74rem; font-weight: 600; letter-spacing: .02em;
             padding: .12rem .5rem; border-radius: 4px; }}
.etiqueta.det {{ color: {TEXTO_2}; background: {SUPERFICIE}; border: 1px solid {BORDE}; }}
.etiqueta.ia  {{ color: {PROFUNDO}; background: {ACENTO_SUAVE}; border: 1px solid {ACENTO_SUAVE}; }}
.lema {{ margin: 0 0 1.2rem 0; padding: .7rem 1rem; background: {SUPERFICIE}; border: 1px solid {BORDE};
         border-left: 3px solid {ACENTO}; border-radius: 4px; }}
.lema b {{ display: block; color: {PROFUNDO}; font-weight: 600; font-size: 1rem; }}
.lema span {{ color: {TEXTO_2}; font-size: .9rem; }}

/* pasos del cruce: número, nombre y una línea que avanza */
ol.pasos {{ list-style: none; display: flex; gap: 0; margin: .2rem 0 1.5rem 0 !important; padding: 0 !important; }}
ol.pasos li {{ margin: 0 !important; flex: 1; display: flex; align-items: center; gap: .6rem; padding: 0 0 .65rem 0;
              color: {TEXTO_2}; font-size: .93rem; border-bottom: 2px solid {BORDE}; }}
ol.pasos b {{ display: inline-flex; align-items: center; justify-content: center; width: 1.45rem; height: 1.45rem;
             border-radius: 50%; border: 1px solid {BORDE}; background: {SUPERFICIE}; font-size: .78rem; font-weight: 600; line-height: 1;
             color: {TEXTO_2}; font-variant-numeric: tabular-nums; flex: none; }}
ol.pasos li.hecho {{ color: {TEXTO}; border-bottom-color: {ACENTO}; }}
ol.pasos li.hecho b {{ background: {ACENTO_SUAVE}; border-color: {ACENTO_SUAVE}; color: {PROFUNDO}; font-size: 0; }}
ol.pasos li.hecho b::after {{ content: "✓"; font-size: .78rem; }}
ol.pasos li.actual {{ color: {TEXTO}; font-weight: 600; border-bottom-color: {PROFUNDO}; }}
ol.pasos li.actual b {{ background: {PROFUNDO}; border-color: {PROFUNDO}; color: #fff; }}

/* resultado: cifras chicas y una barra fina de proporción */
.cuadre {{ margin: .4rem 0 1.3rem 0; padding: 1rem 1.2rem 1.05rem 1.2rem; background: {SUPERFICIE};
           border: 1px solid {BORDE}; border-radius: 6px; }}
.cuadre dl {{ display: flex; flex-wrap: wrap; gap: .9rem 2.6rem; margin: 0 0 .95rem 0; }}
.cuadre dl div {{ display: flex; flex-direction: column; gap: .15rem; min-width: 8rem; }}
.cuadre dt {{ display: flex; align-items: center; gap: .45rem; color: {TEXTO_2}; font-size: .84rem; font-weight: 500; }}
.cuadre dt i {{ display: inline-block; width: .55rem; height: .55rem; border-radius: 2px; }}
.cuadre dd {{ margin: 0; font-size: 1.55rem; font-weight: 600; letter-spacing: -0.01em; color: {TEXTO};
              font-variant-numeric: tabular-nums; }}
.cuadre .barra {{ display: flex; gap: 2px; height: .45rem; border-radius: 3px; overflow: hidden; background: {BORDE}; }}
.cuadre .barra span {{ display: block; height: 100%; }}
.cuadre .ok {{ background: {OK}; }}
.cuadre .dif {{ background: {DIF}; }}
.cuadre .solo {{ background: {SOLO}; }}
.cuadre .total {{ color: {TEXTO_2}; font-size: .84rem; margin-top: .5rem; }}

[data-testid="stSidebar"] [data-testid="stExpander"] summary {{ font-weight: 600; }}
[data-testid="stExpander"] details {{ border-radius: 6px; }}
</style>
"""


def encabezado(titulo: str, bajada: str, meta: str = "") -> str:
    m = f'<div class="meta">{html.escape(meta)}</div>' if meta else ""
    return f'<header class="encabezado">{m}<h1>{html.escape(titulo)}</h1><p>{html.escape(bajada)}</p></header>'


def etiquetas(*items: tuple[str, str, str]) -> str:
    """Una línea por sección con quién hace el trabajo: ("det" | "ia", nombre de la etiqueta, aclaración)."""
    partes = [f'<div><span class="etiqueta {tipo}">{html.escape(nombre)}</span>{html.escape(texto)}</div>'
              for tipo, nombre, texto in items]
    return f'<div class="etiquetas">{"".join(partes)}</div>'


def lema(titulo: str, texto: str) -> str:
    return f'<div class="lema"><b>{html.escape(titulo)}</b><span>{html.escape(texto)}</span></div>'


def casillas(actual: int, etiquetas_pasos: list[str]) -> str:
    """Pasos del cruce: los anteriores al actual quedan tildados."""
    items = []
    for i, e in enumerate(etiquetas_pasos, start=1):
        clase = "hecho" if i < actual else ("actual" if i == actual else "")
        items.append(f'<li class="{clase}"><b>{i}</b>{html.escape(e)}</li>')
    return f'<ol class="pasos">{"".join(items)}</ol>'


def _n(x) -> str:
    return f"{int(x):,}".replace(",", ".")


def cuadre(coinciden: int, con_dif: int, solos: list[tuple[str, int]], total: int,
           textos: dict) -> str:
    """Cifras del resultado y una barra fina con la proporción de cada estado."""
    tramos = [("ok", coinciden), ("dif", con_dif)] + [("solo", n) for _g, n in solos]
    barra = "".join(f'<span class="{c}" style="flex:{n}"></span>' for c, n in tramos if n > 0)
    filas = [f'<div><dt><i class="ok"></i>{html.escape(textos["ok"])}</dt><dd>{_n(coinciden)}</dd></div>',
             f'<div><dt><i class="dif"></i>{html.escape(textos["dif"])}</dt><dd>{_n(con_dif)}</dd></div>']
    filas += [f'<div><dt><i class="solo"></i>{html.escape(textos["solo"].format(g))}</dt><dd>{_n(n)}</dd></div>'
              for g, n in solos]
    return (f'<div class="cuadre"><dl>{"".join(filas)}</dl><div class="barra">{barra}</div>'
            f'<div class="total">{html.escape(textos["total"].format(_n(total)))}</div></div>')


def tabla_continua(df, marcas: list[str] | None = None, col_estado: int | None = None):
    """Tabla blanca; `marcas` ("ok", "dif" o "solo", una por fila) colorea la columna de estado en la posición
    `col_estado`: verde si coincide, rojo si hay diferencias y gris si la llave está en un solo grupo."""
    df = df.reset_index(drop=True)
    df = df.astype(object).where(df.notna(), "").replace({"None": "", "nan": ""})

    def estado(fila):
        estilos = [""] * len(fila)
        if marcas is not None and col_estado is not None and fila.name < len(marcas):
            estilos[col_estado] = {"ok": f"color: {OK}; font-weight: 600", "dif": f"color: {DIF}; font-weight: 600"}.get(
                marcas[fila.name], f"color: {TEXTO_2}")
        return estilos

    return df.style.apply(estado, axis=1)
