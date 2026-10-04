"""Estilo visual de la app: «papel continuo de auditoría».

Los archivos de ancho fijo nacieron para las impresoras de formulario continuo (papel con bandas verdes y
agujeros de arrastre), y la conciliación tiene su propio gesto: el auditor tilda en azul lo que cuadra y marca
en rojo lo que no. De ahí salen todas las decisiones: una sola tira de papel continuo como encabezado, casillas
numeradas de formulario para los pasos y una barra de cuadre con tildes para el resultado. Nada más decora.
"""
from __future__ import annotations

import html

import pandas as pd

pd.set_option("styler.render.max_elements", 5_000_000)  # tablas de hasta 5.000 filas con muchas columnas

PAPEL, BANDA, TINTA, GRAFITO = "#FDFDFB", "#E6F1E7", "#1C2A33", "#5E6B73"
AZUL, ROJO, BORDE, SOLO = "#2747A0", "#B42318", "#CFD8D3", "#9AA7A0"

CSS = f"""
<style>
[data-testid="stMainBlockContainer"] {{ padding-top: 2.2rem; max-width: 1320px; }}
[data-testid="stHeader"] {{ background: transparent; }}

/* encabezado: una tira de papel continuo con agujeros de arrastre */
.continuo {{
  --banda: 1.6rem;
  position: relative; margin: 0 0 1.4rem 0; padding: 0 4.2rem var(--banda) 4.2rem;
  background: repeating-linear-gradient(180deg, {BANDA} 0 var(--banda), #ffffff var(--banda) calc(2 * var(--banda)));
  border-top: 1px solid {BORDE}; border-bottom: 1px solid {BORDE};
}}
.continuo::before, .continuo::after {{
  content: ""; position: absolute; top: 0; bottom: 0; width: 2.6rem;
  background-color: {PAPEL};
  background-image: radial-gradient(circle at 50% 50%, #E3E8E5 0 0.33rem, {BORDE} 0.33rem 0.4rem, transparent 0.42rem);
  background-size: 2.6rem var(--banda); background-repeat: repeat-y;
}}
.continuo::before {{ left: 0; border-right: 1px dashed {BORDE}; }}
.continuo::after  {{ right: 0; border-left: 1px dashed {BORDE}; }}
.continuo h1 {{
  font-family: "Archivo", sans-serif; font-stretch: 72%; font-weight: 800; letter-spacing: -0.005em;
  font-size: 2.55rem; line-height: calc(2 * var(--banda)); color: {TINTA}; margin: 0; padding: 0;
}}
.continuo p {{ color: {GRAFITO}; margin: 0; font-size: .98rem; line-height: var(--banda); max-width: 76ch; }}

/* pestañas sobrias, subrayado en lápiz azul */
.stTabs [data-baseweb="tab-list"] {{ gap: 1.4rem; border-bottom: 1px solid {BORDE}; }}
.stTabs [data-baseweb="tab"] {{ padding: .35rem 0; font-weight: 500; color: {GRAFITO}; }}
.stTabs [aria-selected="true"] {{ color: {TINTA}; }}

h2, h3 {{ font-stretch: 84%; }}

/* pasos como casillas numeradas de un formulario */
ol.casillas {{ list-style: none; display: flex; gap: 0; margin: .2rem 0 1.4rem 0 !important; padding: 0 !important;
              border: 1px solid {BORDE}; background: #fff; }}
ol.casillas li {{ margin: 0 !important; flex: 1; display: flex; align-items: baseline; gap: .6rem; padding: .55rem .9rem;
                 color: {GRAFITO}; border-right: 1px solid {BORDE}; font-size: .95rem; }}
ol.casillas li:last-child {{ border-right: 0; }}
ol.casillas b {{ display: inline-block; min-width: 1.55rem; padding: 0 .25rem; text-align: center;
                border: 1px solid {BORDE}; font-weight: 600; color: {GRAFITO}; font-variant-numeric: tabular-nums; }}
ol.casillas li.hecho {{ color: {TINTA}; }}
ol.casillas li.hecho b {{ border-color: {AZUL}; color: {AZUL}; }}
ol.casillas li.hecho b::after {{ content: " ✓"; }}
ol.casillas li.actual {{ color: {TINTA}; font-weight: 600; background: {BANDA}; }}
ol.casillas li.actual b {{ background: {TINTA}; border-color: {TINTA}; color: #fff; }}

/* resultado: barra de cuadre con tildes de auditor */
.cuadre {{ margin: .4rem 0 1.2rem 0; }}
.cuadre .barra {{ display: flex; height: .9rem; border: 1px solid {BORDE}; background: #fff; }}
.cuadre .barra span {{ display: block; height: 100%; }}
.cuadre .ok {{ background: {AZUL}; }}
.cuadre .dif {{ background: {ROJO}; }}
.cuadre .solo {{ background: repeating-linear-gradient(135deg, {SOLO} 0 3px, #fff 3px 6px); }}
.cuadre dl {{ display: flex; flex-wrap: wrap; gap: .4rem 2.2rem; margin: .7rem 0 0 0; }}
.cuadre dl div {{ display: flex; align-items: baseline; gap: .5rem; }}
.cuadre dt {{ color: {GRAFITO}; font-size: .92rem; }}
.cuadre dd {{ margin: 0; font-family: "IBM Plex Mono", monospace; font-size: 1.35rem; font-weight: 500; color: {TINTA}; }}
.cuadre .m-ok {{ color: {AZUL}; font-weight: 700; }}
.cuadre .m-dif {{ color: {ROJO}; font-weight: 700; }}
.cuadre .m-solo {{ color: {SOLO}; font-weight: 700; }}
.cuadre .total {{ color: {GRAFITO}; font-size: .9rem; margin-top: .35rem; }}

[data-testid="stSidebar"] [data-testid="stExpander"] summary {{ font-weight: 600; }}
</style>
"""


def encabezado(titulo: str, bajada: str) -> str:
    return (f'<header class="continuo"><h1>{html.escape(titulo)}</h1>'
            f'<p>{html.escape(bajada)}</p></header>')


def casillas(actual: int, etiquetas: list[str]) -> str:
    """Pasos del cruce: los anteriores al actual quedan tildados."""
    items = []
    for i, e in enumerate(etiquetas, start=1):
        clase = "hecho" if i < actual else ("actual" if i == actual else "")
        items.append(f'<li class="{clase}"><b>{i}</b>{html.escape(e)}</li>')
    return f'<ol class="casillas">{"".join(items)}</ol>'


def _n(x) -> str:
    return f"{int(x):,}".replace(",", ".")


def cuadre(coinciden: int, con_dif: int, solos: list[tuple[str, int]], total: int,
           textos: dict) -> str:
    """Barra proporcional del resultado y su leyenda con tildes de auditor."""
    tramos = [("ok", coinciden), ("dif", con_dif)] + [("solo", n) for _g, n in solos]
    barra = "".join(f'<span class="{c}" style="flex:{n}"></span>' for c, n in tramos if n > 0)
    filas = [f'<div><dt><span class="m-ok">✓</span> {html.escape(textos["ok"])}</dt><dd>{_n(coinciden)}</dd></div>',
             f'<div><dt><span class="m-dif">≠</span> {html.escape(textos["dif"])}</dt><dd>{_n(con_dif)}</dd></div>']
    filas += [f'<div><dt><span class="m-solo">○</span> {html.escape(textos["solo"].format(g))}</dt><dd>{_n(n)}</dd></div>'
              for g, n in solos]
    return (f'<div class="cuadre"><div class="barra">{barra}</div><dl>{"".join(filas)}</dl>'
            f'<div class="total">{html.escape(textos["total"].format(_n(total)))}</div></div>')


def tabla_continua(df, marcas: list[str] | None = None, col_estado: int | None = None):
    """Filas en bandas verdes, como el papel continuo. `marcas` ("ok", "dif" o "solo", una por fila) colorea
    la columna de estado en la posición `col_estado`: lápiz azul si cuadra, rojo si hay diferencias."""
    df = df.reset_index(drop=True)
    df = df.astype(object).where(df.notna(), "").replace({"None": "", "nan": ""})

    def bandas(fila):
        fondo = BANDA if fila.name % 2 == 0 else "#ffffff"
        estilos = [f"background-color: {fondo}"] * len(fila)
        if marcas is not None and col_estado is not None and fila.name < len(marcas):
            color = {"ok": f"color: {AZUL}; font-weight: 600", "dif": f"color: {ROJO}; font-weight: 600"}.get(
                marcas[fila.name], f"color: {GRAFITO}")
            estilos[col_estado] += "; " + color
        return estilos

    return df.style.apply(bandas, axis=1)
