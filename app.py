"""Demo interactiva: de la normativa al archivo validado.

    streamlit run app.py
"""
from __future__ import annotations

import difflib
import io
import json
import os
import re
from decimal import Decimal
from typing import Optional
from pathlib import Path

import pandas as pd
import streamlit as st

from regspec.archivos import escribir, generar_xsd
from regspec.base_sin_ia import extraer_base
from regspec.dsl import Especificacion
from regspec.generador import generar, registros_desde_tabla
from regspec.llm.extractor import ConfigExtraccion, extraer
from regspec.llm.proveedores import PRESETS, Anthropic, OpenAICompatible
from regspec.sintetico import generar_registros
from regspec.validador import validar
from regspec.verificador import verificar

RAIZ = Path(__file__).parent
st.set_page_config(page_title="Compilador de especificaciones", page_icon="🧩", layout="wide")
ss = st.session_state

from regspec.i18n import IDIOMAS, traducir, traducir_df  # noqa: E402


def _idioma_inicial() -> str:
    try:
        loc = (getattr(st.context, "locale", None) or "").lower()
    except Exception:  # noqa: BLE001
        loc = ""
    return "pt" if loc.startswith("pt") else "es"


ss.setdefault("idioma", _idioma_inicial())


def _(texto):
    """Texto de interfaz en el idioma elegido (español es la base)."""
    return traducir(texto, ss.idioma)


def _df(df, **kw):
    return traducir_df(df, ss.idioma, **kw)


ss.setdefault("spec", None)
ss.setdefault("manual", "")
ss.setdefault("iteraciones", [])

# ---------------------------------------------------------------- barra lateral
with st.sidebar:
    st.selectbox("🌐 Idioma", list(IDIOMAS), format_func=IDIOMAS.get, key="idioma")
    st.header(_("Modelo"))
    opcion = st.selectbox(_("Proveedor"), ["groq", "gemini", "openai", "openrouter", "ollama", "anthropic", "sin IA (línea base)"],
                          format_func=_)
    if opcion == "sin IA (línea base)":
        proveedor = None
    else:
        defecto = PRESETS.get(opcion, (None, None, "claude-haiku-4-5-20251001"))[2]
        if opcion == "groq":
            modelo = st.selectbox(_("Modelo"), ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "(otro)"], format_func=_,
                                  help=_("Cada modelo tiene su propio cupo diario en el plan gratuito."))
            if modelo == "(otro)":
                modelo = st.text_input(_("Nombre del modelo"), "")
        else:
            modelo = st.text_input(_("Modelo"), defecto)
        env = PRESETS.get(opcion, (None, "ANTHROPIC_API_KEY", None))[1] if opcion != "anthropic" else "ANTHROPIC_API_KEY"
        def _secreto(nombre):
            try:
                return st.secrets.get(nombre, "")  # Streamlit Cloud: Settings -> Secrets
            except Exception:  # noqa: BLE001 (sin archivo de secrets en local)
                return ""
        clave = st.text_input(_("API key"), value=os.environ.get(env or "", "") or _secreto(env or ""), type="password",
                              help=_("No se guarda; solo se usa en esta sesión.")) if env else None
        if opcion == "anthropic":
            proveedor = Anthropic(modelo=modelo, api_key=clave)
        else:
            extra = {"reasoning_effort": "low"} if ("gpt-oss" in modelo and opcion == "groq") else {}
            proveedor = OpenAICompatible(modelo=modelo, base_url=PRESETS[opcion][0], api_key=clave, nombre=opcion, extra=extra)
    max_it = st.slider(_("Máximo de iteraciones de autocorrección"), 1, 6, 4)
    usar_cache = st.checkbox(_("Reutilizar respuestas guardadas (no gasta tokens al repetir)"), value=True)
    modo = st.radio(_("Modo de extracción"), ["secciones", "completo"], format_func=_,
                    help=_("Secciones: una llamada por parte (entra en los planes gratuitos). Completo: una sola respuesta."))
    st.divider()
    st.caption(_("Cargar una especificación existente"))
    subida = st.file_uploader(_("spec.json"), type=["json"], key="spec_json")
    if subida is not None and st.button(_("Usar esta especificación")):
        d = json.loads(subida.read())
        ss.spec = Especificacion.model_validate(d.get("spec", d))

st.title(_("Conciliación de archivos regulatorios"))
st.caption(_("Subí tus archivos (CSV, Excel, TXT posicional o XML) y el sistema los cruza. Si un formato es nuevo, "
           "lo aprende de su manual técnico: un LLM lo convierte en una especificación formal y un verificador la comprueba."))
BIBLIO = RAIZ / "especificaciones"


def guardar_en_biblioteca(spec: Especificacion, nombre: str) -> str:
    import re as _re
    BIBLIO.mkdir(exist_ok=True)
    slug = _re.sub(r"[^a-z0-9]+", "_", nombre.lower()).strip("_")[:50] or "especificacion"
    (BIBLIO / f"{slug}.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return slug

from regspec.almacen import AlmacenLocal, ArchivoCfg, ConfigCruce, FiltroCfg, GrupoCfg

almacen = AlmacenLocal(RAIZ / "cruces_guardados")


def usuario_actual() -> str:
    """Mail del usuario si la app corre con login (Streamlit); si no, 'local'."""
    try:
        u = getattr(st, "user", None)
        mail = u.get("email") if u is not None else None
        return mail or "local"
    except Exception:  # noqa: BLE001
        return "local"


def aplicar_config(cruce) -> None:
    """Deja lista una configuración guardada para aplicarla a los archivos que se suban."""
    ss.cfg = cruce.config if cruce is not None else None
    ss.cfg_nombre = cruce.nombre if cruce is not None else None
    if cruce is not None:
        ss.n_grupos = max(2, len(cruce.config.grupos))
    ss.cfg_ver = ss.get("cfg_ver", 0) + 1
    ss.pop("res_grupos", None)


def describir_filtro(x) -> str:
    from regspec.grupos import ACCIONES, OPERADORES_TEXTO
    vals = x.valores or ([x.valor] if x.valor else [])
    accion = _(ACCIONES.get(x.accion, x.accion)).rstrip("…")
    return f"{accion} {x.columna} {_(OPERADORES_TEXTO.get(x.operador, x.operador))} " + " | ".join(vals)


ss.setdefault("cfg", None)
ss.setdefault("cfg_nombre", None)
ss.setdefault("cfg_ver", 0)

tc, tm, t1, t2, t3, t4 = st.tabs([_(x) for x in ["🔀 Cruzar archivos", "🗂 Mis cruces", "📘 Aprender un formato (manual)",
                                                 "📋 Especificación", "✅ Validar archivo", "🧾 Generar archivo"]])

# ---------------------------------------------------------------- 1. compilar
with t1:
    ejemplos = {p.stem: p for p in sorted([*(RAIZ / "manuales").glob("*.md"), *(RAIZ / "manuales").glob("*.txt")])}
    c1, c2 = st.columns([1, 1])
    with c1:
        elegido = st.selectbox(_("Manual de ejemplo"), ["(subir uno propio)"] + list(ejemplos), format_func=_)
    with c2:
        propio = st.file_uploader(_("…o subí un manual (md, txt, pdf)"), type=["md", "txt", "pdf"])
    if propio is not None:
        if propio.name.lower().endswith(".pdf"):
            from pypdf import PdfReader

            ss.manual = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(propio.read())).pages)
        else:
            ss.manual = propio.read().decode("utf-8", errors="replace")
    elif elegido in ejemplos:
        ss.manual = ejemplos[elegido].read_text(encoding="utf-8")
    with st.expander(_("Ver manual"), expanded=False):
        st.markdown(ss.manual or "_(vacío)_")

    if st.button(_("Compilar especificación"), type="primary", disabled=not ss.manual):
        ss.iteraciones = []
        zona = st.container()
        if proveedor is None:
            d = extraer_base(ss.manual)
            res = verificar(d, ss.manual)
            ss.spec = res.spec
            zona.info(_('Línea base sin IA: {0}').format(_('válida') if res.valida else _('{0} errores').format(len(res.errores))))
            for p in res.errores[:30]:
                zona.write(f"- `{p.codigo}` {p.ruta}: {p.mensaje}")
        else:
            def al_iterar(it):
                ss.iteraciones.append(it)
                if it.errores == 0:
                    zona.success(_('Iteración {0}: especificación verificada ✔ ({1} tokens, {2:.1f} s)').format(it.numero, it.tokens_entrada + it.tokens_salida, it.latencia_s))
                else:
                    zona.warning(_('Iteración {0}: {1} errores → se devuelven al modelo · {2}').format(it.numero, it.errores, it.errores_por_etapa))
                    with zona.expander(_('Problemas detectados en la iteración {0}').format(it.numero)):
                        for p in it.problemas[:40]:
                            st.write("- " + p)
            with st.spinner(_("El modelo está leyendo el manual…")):
                def al_paso(p):
                    icono = "✔" if p.ok else "✖"
                    costo = (_("sin IA") if "por código" in p.nombre else
                             _("desde caché, 0 tokens") if p.latencia_s == 0 else _("{0} tokens").format(p.tokens_entrada + p.tokens_salida))
                    zona.write(_("{0} Paso **{1}** ({2}, {3:.0f} s)").format(icono, p.nombre, costo, p.latencia_s)
                               + ("" if p.ok else f" — {p.detalle}"))

                from regspec.llm.cache import ProveedorConCache

                proveedor = ProveedorConCache(proveedor, usar=usar_cache)
                r = extraer(ss.manual, proveedor, ConfigExtraccion(max_iteraciones=max_it, modo=modo),
                            al_iterar=al_iterar, al_paso=al_paso)
            if r.error:
                st.error(r.error)
            ss.spec = r.spec
            if r.spec is not None and r.valida:
                st.success(_("Guardada en la biblioteca como '{0}': desde ahora el sistema reconoce este formato al cruzar archivos.").format(guardar_en_biblioteca(r.spec, r.spec.nombre)))
            if r.spec is not None:
                st.metric(_("Resultado"), _("Válida") if r.valida else _("Con errores"),
                          _("{0} iteraciones · {1} tokens").format(len(r.iteraciones), r.tokens))

# ---------------------------------------------------------------- 2. especificación
with t2:
    spec: Especificacion | None = ss.spec
    if spec is None:
        st.info(_("Primero compilá un manual o cargá una especificación."))
    else:
        res = verificar(spec, ss.manual or None, chequear_evidencia=bool(ss.manual))
        a, b, c, d = st.columns(4)
        a.metric(_("Formato"), spec.formato)
        b.metric(_("Tipos de registro"), len(spec.tipos_registro))
        c.metric(_("Campos"), sum(len(t.campos) for t in spec.tipos_registro))
        d.metric(_("Reglas"), len(spec.reglas))
        (st.success if res.valida else st.error)(_("Verificador: sin errores") if res.valida else _("Verificador: {0} errores").format(len(res.errores)))
        for p in res.errores:
            st.write(f"- `{p.codigo}` {p.ruta}: {p.mensaje}")
        for tr in spec.tipos_registro:
            st.subheader(f"{tr.codigo} · {tr.nombre}")
            st.caption(_('ocurrencias {0}..{1} · posición {2}').format(tr.min_ocurrencias, tr.max_ocurrencias or 'n', tr.posicion))
            st.dataframe(pd.DataFrame([{k: v for k, v in c.model_dump().items() if k != "etiqueta_xml" or spec.formato == "xml"} for c in tr.campos]),
                         hide_index=True, width="stretch")
        if spec.reglas:
            st.subheader(_("Reglas"))
            st.dataframe(pd.DataFrame([r.model_dump() for r in spec.reglas]), hide_index=True, width="stretch")
        x1, x2, x3 = st.columns(3)
        x1.download_button(_("Descargar especificación (JSON)"), spec.model_dump_json(indent=2), "especificacion.json")
        if res.valida:
            ejemplo = escribir(spec, generar_registros(spec, 0)[0])
            x2.download_button(_("Archivo de ejemplo"), ejemplo, "ejemplo." + ("xml" if spec.formato == "xml" else "txt"))
        x3.download_button(_("Esquema XSD"), generar_xsd(spec), "esquema.xsd")

# ---------------------------------------------------------------- 3. validar
with t3:
    from regspec.cruce import _decodificar, biblioteca_de_especificaciones as _biblio, detectar_especificacion as _detectar

    specs_v = _biblio(RAIZ / "especificaciones", RAIZ / "biblioteca", RAIZ / "gold")
    if ss.spec is not None:
        specs_v = {"(compilada en esta sesión)": ss.spec, **specs_v}
    arch = st.file_uploader(_("Archivo a validar"), type=["txt", "xml", "dat"], key="validar")
    if arch is not None:
        det = _detectar(arch.getvalue(), arch.name, specs_v)
        nombres_v = list(specs_v)
        idx = nombres_v.index(det.nombre_spec) if det.nombre_spec in nombres_v else 0
        if det.spec is not None:
            st.caption(_('Formato reconocido: {0} · {1}').format(det.nombre_spec, _(det.detalle)))
        elegido = st.selectbox(_("Especificación"), nombres_v, index=idx, key="spec_validar", format_func=_)
        spec = specs_v[elegido]
        with st.spinner(_("Validando campo a campo y reglas…")):
            inf = validar(spec, _decodificar(arch.getvalue(), spec.codificacion))
        (st.success if inf.ok else st.error)(_(inf.resumen(0).splitlines()[0]))
        if inf.hallazgos:
            st.dataframe(_df(pd.DataFrame(inf.a_dicts())), hide_index=True, width="stretch")

# ---------------------------------------------------------------- 4. generar
with t4:
    spec = ss.spec
    if spec is None:
        st.info(_("Primero compilá un manual o cargá una especificación."))
    else:
        st.write(_("Cargá los datos de detalle (por ejemplo, el resultado de una conciliación). "
                 "Los registros únicos (cabecera) se completan abajo; los totales de control se calculan solos a partir de las reglas."))
        repetibles = [t.codigo for t in spec.tipos_registro if t.max_ocurrencias != 1]
        cod = st.selectbox(_("Tipo de registro de detalle"), repetibles or [t.codigo for t in spec.tipos_registro])
        csv = st.file_uploader(_("CSV con los datos"), type=["csv"], key="csv")
        tr = spec.tipo(cod)
        if csv is not None and tr is not None:
            df = pd.read_csv(csv, dtype=str, sep=None, engine="python")
            st.dataframe(df.head(), hide_index=True)
            campos = [c.nombre for c in tr.campos if c.tipo != "constante"]
            mapeo = {}
            cols = st.columns(3)
            for k, col in enumerate(df.columns):
                sug = difflib.get_close_matches(col.lower(), campos, n=1, cutoff=0.4)
                mapeo[col] = cols[k % 3].selectbox(f"{col} →", ["(ignorar)"] + campos, format_func=_,
                                                   index=(campos.index(sug[0]) + 1) if sug else 0, key=f"map_{col}")
            mapeo = {k: v for k, v in mapeo.items() if v != "(ignorar)"}
            unicos = {}
            for t in spec.tipos_registro:
                if t.codigo == cod:
                    continue
                with st.expander(_('Registro {0} · {1}').format(t.codigo, t.nombre)):
                    unicos[t.codigo] = {}
                    for c in t.campos:
                        if c.tipo == "constante":
                            continue
                        v = st.text_input(f"{c.descripcion or c.nombre} ({c.tipo})", key=f"u_{t.codigo}_{c.nombre}",
                                          help=_("Dejar vacío si se calcula a partir de las reglas"))
                        if v:
                            unicos[t.codigo][c.nombre] = v
            if st.button(_("Generar archivo"), type="primary"):
                regs = []
                for t in spec.tipos_registro:
                    if t.codigo == cod:
                        regs += registros_desde_tabla(df.to_dict("records"), cod, mapeo)
                    else:
                        regs.append((t.codigo, unicos.get(t.codigo, {})))
                r = generar(spec, regs)
                if r.error_escritura:
                    st.error(r.error_escritura)
                else:
                    if r.autocompletados:
                        st.info(_("Calculado automáticamente: {0}").format(", ".join(sorted(set(x.split(" (")[0].split("[")[0] for x in r.autocompletados)))))
                    (st.success if r.informe.ok else st.warning)(_("Control previo: {0}").format(_(r.informe.resumen(0).splitlines()[0])))
                    if r.informe.hallazgos:
                        st.dataframe(_df(pd.DataFrame(r.informe.a_dicts())), hide_index=True)
                    st.download_button(_("Descargar archivo"), r.contenido, "informe." + ("xml" if spec.formato == "xml" else "txt"))



# ---------------------------------------------------------------- flujo principal: cruzar
with tc:
    from decimal import Decimal

    from regspec.cruce import (
        biblioteca_de_especificaciones, cruzar, detectar_especificacion, llave_clara, sugerir_comparaciones,
        sugerir_llave, sugerir_llave_ia, tabla_desde_archivo,
    )
    from regspec.llm.cache import ProveedorConCache

    biblioteca = biblioteca_de_especificaciones(BIBLIO, RAIZ / "biblioteca", RAIZ / "gold")

    @st.cache_resource(show_spinner="Reconociendo el formato…", max_entries=16)
    def detectar_cacheado(contenido: bytes, nombre: str, nombres_specs: tuple, _biblio: dict):
        return detectar_especificacion(contenido, nombre, _biblio)

    @st.cache_resource(show_spinner="Leyendo el archivo…", max_entries=16)
    def tabla_cacheada(contenido: bytes, nombre: str, spec_json: Optional[str], tipo: Optional[str], hoja: Optional[str]):
        spec = Especificacion.model_validate_json(spec_json) if spec_json else None
        return tabla_desde_archivo(contenido, nombre, spec, tipo, hoja)

    @st.cache_resource(show_spinner=False, max_entries=16)
    def hojas_cacheado(contenido: bytes):
        from regspec.cruce import hoja_por_defecto, hojas_excel
        return hojas_excel(contenido), hoja_por_defecto(contenido)
    if ss.spec is not None:
        biblioteca["(compilada en esta sesión)"] = ss.spec

    guardados = almacen.listar()
    if guardados:
        g1c, g2c = st.columns([4, 1])
        etiquetas = [_("(configuración nueva)")] + [f"{g.nombre} · {g.fecha[:10]}" for g in guardados]
        actual = next((i + 1 for i, g in enumerate(guardados) if g.nombre == ss.cfg_nombre), 0)
        sel = g1c.selectbox(_("Partir de un cruce guardado"), range(len(etiquetas)), index=actual,
                            format_func=lambda i: etiquetas[i],
                            help=_("Aplica los mismos grupos, llaves, filtros, importes y tolerancia a los archivos nuevos."))
        g2c.write("")
        if g2c.button(_("Aplicar"), use_container_width=True):
            aplicar_config(guardados[sel - 1] if sel else None)
            st.rerun()
    cfg: Optional[ConfigCruce] = ss.cfg
    V = f"v{ss.cfg_ver}"  # sufijo de claves: cambia al aplicar una configuración y reinicia los controles
    if cfg is not None:
        st.info(_('Usando la configuración **{0}**. Subí los archivos del período en el mismo orden (grupo y posición dentro del grupo) y revisá las llaves antes de cruzar.').format(ss.cfg_nombre))

    st.subheader(_("1 · Armá los grupos de archivos"))

    def leer_uno(f, lado: str, i: int, hoja_def: Optional[str] = None):
        if True:
            k = f"{lado}{i}"
            det = detectar_cacheado(f.getvalue(), f.name, tuple(biblioteca), biblioteca)
            spec_sel, tipo, hoja = det.spec, det.tipo_registro, None
            if f.name.lower().endswith((".xlsx", ".xls", ".xlsm")):
                hojas, por_defecto = hojas_cacheado(f.getvalue())
                if len(hojas) > 1:
                    hoja_ini = hoja_def if hoja_def in hojas else por_defecto
                    hoja = st.selectbox(_("Hoja"), hojas, index=hojas.index(hoja_ini), key=f"hoja_{k}_{V}",
                                        help=_("Se elige sola la hoja con más datos; la fila de encabezado también se detecta."))
                else:
                    st.caption(_("Tabla (Excel)"))
            elif f.name.lower().endswith(".csv"):
                st.caption(_("Tabla (CSV)"))
            elif det.spec is not None and det.confianza >= 0.9:
                st.success(_('Formato reconocido: **{0}** · {1}').format(det.nombre_spec, _(det.detalle)))
                with st.expander(_("Cambiar formato o tipo de registro")):
                    nombres = list(biblioteca)
                    n = st.selectbox(_("Especificación"), nombres, index=nombres.index(det.nombre_spec), key=f"spec_{k}", format_func=_)
                    spec_sel = biblioteca[n]
                    tipos = [t.codigo for t in spec_sel.tipos_registro]
                    tipo = st.selectbox(_("Tipo de registro a cruzar"), tipos,
                                        index=tipos.index(tipo) if tipo in tipos else 0, key=f"tipo_{k}")
            else:
                st.warning(_("No reconozco el formato de este archivo. Subí su **manual técnico** y lo aprendo "
                           "(usa IA una sola vez; después queda guardado)."))
                man = st.file_uploader(_("Manual técnico (PDF, MD o TXT)"), type=["pdf", "md", "txt"], key=f"manual_{k}")
                if man is not None and st.button(_("Aprender el formato"), key=f"aprender_{k}", type="primary"):
                    if man.name.lower().endswith(".pdf"):
                        from pypdf import PdfReader
                        texto = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(man.getvalue())).pages)
                    else:
                        texto = man.getvalue().decode("utf-8", errors="replace")
                    with st.status(_("Leyendo el manual…"), expanded=True) as estado:
                        if proveedor is None:
                            d = extraer_base(texto)
                            ver = verificar(d, texto)
                            spec_nueva, valida = ver.spec, ver.valida
                        else:
                            r = extraer(texto, ProveedorConCache(proveedor, usar=usar_cache),
                                        ConfigExtraccion(max_iteraciones=max_it, modo=modo),
                                        al_paso=lambda p: estado.write(f"{'✔' if p.ok else '✖'} {p.nombre}"),
                                        al_iterar=lambda it: estado.write(
                                            _("verificación: OK") if it.errores == 0 else _("verificación: {0} errores → corrigiendo").format(it.errores)))
                            spec_nueva, valida = r.spec, r.valida
                            if r.error:
                                st.error(r.error)
                        if spec_nueva is not None and valida:
                            nombre = guardar_en_biblioteca(spec_nueva, spec_nueva.nombre)
                            estado.update(label=_("Formato aprendido y guardado como '{0}'").format(nombre), state="complete")
                            st.rerun()
                        else:
                            estado.update(label=_("No se obtuvo una especificación válida"), state="error")
                            st.info(_("Podés revisarla y corregirla en la pestaña «Aprender un formato»."))
                return None
            try:
                df = tabla_cacheada(f.getvalue(), f.name, spec_sel.model_dump_json() if spec_sel is not None else None, tipo, hoja)
            except Exception as e:  # noqa: BLE001
                st.error(_('No se pudo leer {0}: {1}').format(f.name, e))
                return None
            st.caption(_("{0} registros").format(f"{len(df):,}".replace(",", ".")))
            return df

    from regspec.grupos import (ACCIONES, MODOS, OPERADORES, OPERADORES_MULTIVALOR, OPERADORES_TEXTO, TRANSFORMACIONES,
                                ArchivoGrupo, Filtro, Grupo, cruzar_grupos, transformar)

    from regspec.grupos import sugerir_llaves

    @st.cache_resource(show_spinner="Buscando la llave que vincula los archivos…", max_entries=8)
    def llaves_sugeridas(firma: tuple, _tablas: list):
        """Sugerencia conjunta de llaves (sin IA): la columna del primer archivo que mejor vincula a todos."""
        return sugerir_llaves(_tablas)

    def tabla_silenciosa(f):
        """Lectura sin interfaz (para la sugerencia de llaves); None si el formato no se reconoce."""
        try:
            det = detectar_cacheado(f.getvalue(), f.name, tuple(biblioteca), biblioteca)
            if not f.name.lower().endswith((".csv", ".xlsx", ".xls", ".xlsm")) and (det.spec is None or det.confianza < 0.9):
                return None
            return tabla_cacheada(f.getvalue(), f.name, det.spec.model_dump_json() if det.spec is not None else None,
                                  det.tipo_registro, None)
        except Exception:  # noqa: BLE001
            return None

    ss.setdefault("n_grupos", 2)
    st.caption(_("Cada **grupo** reúne uno o varios archivos. En cada archivo definís su **llave** (y cómo normalizarla); "
               "el cruce se hace sobre esa llave entre todos los grupos."))
    grupos_ui = []
    hojas_sel: dict = {}
    # sugerencia conjunta de llaves con todos los archivos ya subidos (se recalcula solo si cambian los archivos)
    subidos = [(gi, f) for gi in range(ss.n_grupos) for f in (ss.get(f"gfiles_{gi}") or [])]
    leidas = [(gi, f, tabla_silenciosa(f)) for gi, f in subidos]
    leidas = [x for x in leidas if x[2] is not None]
    sugeridas = {}
    if leidas:
        firma = tuple((gi, f.name, f.size) for gi, f, _ in leidas)
        for (gi, f, _tabla), s in zip(leidas, llaves_sugeridas(firma, [d for _, _, d in leidas])):
            if s is not None:
                sugeridas[(gi, f.name)] = s
    for gi in range(ss.n_grupos):
        with st.container(border=True):
            c_nom, c_modo = st.columns([1, 2])
            gcfg = cfg.grupos[gi] if cfg is not None and gi < len(cfg.grupos) else None
            nombre_g = c_nom.text_input(_("Nombre del grupo"), gcfg.nombre if gcfg else _("Grupo {0}").format(gi + 1), key=f"gnom_{gi}_{V}_{ss.idioma}")
            archivos = st.file_uploader(_('Archivos de «{0}» (uno o varios)').format(nombre_g), type=["csv", "xlsx", "xls", "txt", "xml", "dat"],
                                        accept_multiple_files=True, key=f"gfiles_{gi}")
            modo_ini = list(MODOS).index(gcfg.modo) if gcfg and gcfg.modo in MODOS else 0
            modo_g = c_modo.selectbox(_("Cómo combinar los archivos del grupo"), list(MODOS), format_func=lambda k: _(MODOS[k]), index=modo_ini,
                                      key=f"gmodo_{gi}_{V}", disabled=not archivos or len(archivos) < 2)
            if not archivos:
                grupos_ui.append(None)
                continue
            arch_objs, completo = [], True
            for fi, f in enumerate(archivos):
                acfg = cfg.archivo(gi, fi) if cfg is not None else None
                with st.expander(f"📄 {f.name}", expanded=True):
                    df = leer_uno(f, f"g{gi}", fi, acfg.hoja if acfg else None)
                    if df is None:
                        completo = False
                        continue
                    cols = [c for c in df.columns if not c.startswith("_")]
                    s = sugeridas.get((gi, f.name))
                    if acfg is not None:  # configuración guardada: manda sobre la sugerencia
                        faltan = [c for c in acfg.llave + acfg.traer + [x.columna for x in acfg.filtros] if c not in cols]
                        if faltan:
                            st.warning(_("Columnas de la configuración que no están en este archivo: {0}").format(", ".join(faltan)))
                        sug = [c for c in acfg.llave if c in cols]
                        tr_def = acfg.transformacion if acfg.transformacion in TRANSFORMACIONES else "exacta"
                        k_sug = "cfg"
                    else:
                        sug = [s[0]] if s and s[0] in cols else []
                        tr_def = s[1] if s else "exacta"
                        k_sug = f"{'_'.join(map(str, sug))}_{len(leidas)}"
                    c1, c2 = st.columns([2, 1])
                    llave = c1.multiselect(_("Llave (una o varias columnas)"), cols, default=sug, placeholder=_("Elegí una o varias columnas"), key=f"llave_{gi}_{fi}_{V}_{k_sug}",
                                           help=_("Sugerida por código: la columna cuyos valores coinciden con los de los otros archivos."))
                    tr = c2.selectbox(_("Normalizar la llave"), list(TRANSFORMACIONES), format_func=lambda k: _(TRANSFORMACIONES[k]),
                                      index=list(TRANSFORMACIONES).index(tr_def), key=f"tr_{gi}_{fi}_{V}_{k_sug}_{tr_def}")
                    if acfg is None and s and s[2] > 0 and llave == sug:
                        st.caption(_('Coincidencia de valores con la llave del primer archivo: {0:.0%}').format(s[2]))
                    if llave:
                        st.caption(_("Ejemplos de llave: {0}").format(", ".join(
                            "|".join(transformar(v, tr) for v in fila) for fila in df[llave].head(3).itertuples(index=False))))
                    filtros = []
                    previos = [x for x in (acfg.filtros if acfg is not None else []) if x.columna in cols]
                    kn = f"nfil_{gi}_{fi}_{V}"
                    ss.setdefault(kn, max(1, len(previos)))
                    for qi in range(ss[kn]):
                        f0 = previos[qi] if qi < len(previos) else None
                        fk = f"{gi}_{fi}_{qi}_{V}"
                        fc0, fc1, fc2 = st.columns(3)
                        fc3 = st
                        acciones = ["ninguno", *ACCIONES]
                        accion = fc0.selectbox(_("Filtro") if qi == 0 else _("Otra condición"), acciones,
                                               index=acciones.index(f0.accion) if f0 and f0.accion in acciones else 0,
                                               format_func=lambda a: _("(sin filtro)") if a == "ninguno" else _(ACCIONES[a]),
                                               key=f"facc_{fk}")
                        if accion == "ninguno":
                            continue
                        fcol = fc1.selectbox(_("Columna"), cols, index=cols.index(f0.columna) if f0 else 0, key=f"fcol_{fk}")
                        fop = fc2.selectbox(_("condición"), OPERADORES, format_func=lambda o: _(OPERADORES_TEXTO[o]),
                                            index=OPERADORES.index(f0.operador) if f0 and f0.operador in OPERADORES else 0,
                                            key=f"fop_{fk}")
                        valores, fval = [], ""
                        if fop in OPERADORES_MULTIVALOR:
                            distintos = pd.unique(df[fcol].dropna().astype(str).str.strip())
                            distintos = sorted(v for v in distintos if v)
                            previos_v = (f0.lista() if hasattr(f0, "lista") else (f0.valores or ([f0.valor] if f0.valor else []))) if f0 else []
                            if fop != "contiene" and len(distintos) <= 500:
                                valores = fc3.multiselect(_("Valores"), distintos, default=[v for v in previos_v if v in distintos],
                                                          placeholder=_("Elegí uno o varios valores"), key=f"fvals_{fk}",
                                                          help=_("Se toman las filas que coinciden con cualquiera de los valores elegidos."))
                            else:
                                texto = fc3.text_area(_("Valores (uno por línea)"), "\n".join(previos_v), height=80, key=f"fvalt_{fk}",
                                                      help=_("Escribí un valor por línea; se toman las filas que coinciden con cualquiera."))
                                valores = [v.strip() for v in texto.splitlines() if v.strip()]
                        elif fop not in ("vacío", "no vacío"):
                            fval = fc3.text_input(_("valor"), f0.valor if f0 else "", key=f"fval_{fk}")
                        filtros.append(Filtro(fcol, fop, fval, accion, valores))
                    if st.button(_("➕ Otra condición"), key=f"masfil_{gi}_{fi}_{V}"):
                        ss[kn] += 1
                        st.rerun()
                    traer = []
                    if modo_g == "base_referencia" and fi > 0:
                        opciones_t = [c for c in cols if c not in llave]
                        traer = st.multiselect(_("Columnas a traer a la base"), opciones_t, placeholder=_("Elegí una o varias columnas"),
                                               default=[c for c in (acfg.traer if acfg else []) if c in opciones_t], key=f"traer_{gi}_{fi}_{V}")
                    if llave:
                        arch_objs.append(ArchivoGrupo(f.name, df, llave, tr, filtros, traer))
                        hojas_sel[(gi, len(arch_objs) - 1)] = ss.get(f"hoja_g{gi}{fi}_{V}")
                    else:
                        completo = False
            if not completo or not arch_objs:
                grupos_ui.append(None)
                continue
            cols_g = sorted({c for a in arch_objs for c in a.df.columns if not c.startswith("_")} |
                            {c for a in arch_objs for c in a.traer})
            grupos_ui.append((nombre_g, arch_objs, modo_g, cols_g))
    b1, b2, _hueco = st.columns([1, 1, 3])
    if b1.button(_("➕ Agregar grupo")):
        ss.n_grupos += 1
        st.rerun()
    if ss.n_grupos > 2 and b2.button(_("➖ Quitar el último")):
        ss.n_grupos -= 1
        st.rerun()

    listos = [g for g in grupos_ui if g is not None]
    if len(listos) >= 2 and len(listos) == len(grupos_ui):
        st.subheader(_("2 · ¿Qué comparo en cada llave?"))
        st.caption(_("Elegí la columna de importe de cada grupo; se suman por llave y se comparan con la tolerancia."))
        n_cmp = st.number_input(_("Cantidad de importes a comparar"), 0, 5, min(5, len(cfg.comparaciones)) if cfg is not None else 1, key=f"ncmp_{V}")
        comparaciones = []
        for k in range(int(n_cmp)):
            cc = st.columns(len(listos))
            fila = []
            for j, (nom, _a, _m, cols_g) in enumerate(listos):
                num = [c for c in cols_g if re.search(r"monto|importe|total|base|valor|saldo|retenid|retenci|prima|pagad", c, re.I)
                       and not re.search(r"diferencia|control|fecha|date|tipo|codigo|número|numero", c, re.I)] or cols_g
                num.sort(key=lambda c: 0 if re.search(r"monto|importe", c, re.I) else 1)
                previo = cfg.comparaciones[k][j] if cfg is not None and k < len(cfg.comparaciones) and j < len(cfg.comparaciones[k]) else None
                if previo in cols_g:
                    elegido = previo
                elif j == 0:
                    elegido = num[min(k, len(num) - 1)]
                else:  # la más parecida a la elegida en el primer grupo
                    elegido = max(num, key=lambda c: difflib.SequenceMatcher(None, c.lower().replace("_", " "),
                                                                          fila[0].lower().replace("_", " ")).ratio())
                fila.append(cc[j].selectbox(f"{nom}", cols_g, index=cols_g.index(elegido), key=f"cmp_{k}_{j}_{V}"))
            comparaciones.append(tuple(fila))
        tol = st.number_input(_("Tolerancia en importes"), value=float(cfg.tolerancia) if cfg is not None else 0.01, min_value=0.0,
                              step=0.01, format="%.2f", key=f"tol_g_{V}")

        st.subheader(_("3 · Resultado"))
        if st.button(_("Cruzar"), type="primary"):
            grupos = [Grupo(nom, arch, modo, []) for nom, arch, modo, _ in listos]
            with st.spinner(_("Cruzando…")):
                ss.res_grupos = cruzar_grupos(grupos, comparaciones, Decimal(str(tol)))
                ss.res_excel = ss.res_grupos.a_excel()
            ss.cfg_actual = ConfigCruce(
                grupos=[GrupoCfg(nombre=nom, modo=modo, archivos=[
                    ArchivoCfg(nombre_original=a.nombre, llave=a.llave, transformacion=a.transformacion,
                               filtros=[FiltroCfg(columna=x.columna, operador=x.operador, valor=x.valor, accion=x.accion,
                                                  valores=x.valores) for x in a.filtros],
                               traer=a.traer, hoja=hojas_sel.get((gi, ai)))
                    for ai, a in enumerate(arch)]) for gi, (nom, arch, modo, _) in enumerate(listos)],
                comparaciones=[list(c) for c in comparaciones], tolerancia=str(tol))
            ss.archivos_actual = [a.nombre for _, arch, _, _ in listos for a in arch]
        r = ss.get("res_grupos")
        if r is not None:
            nombres = [n for n, *_ in listos]
            mets = st.columns(2 + len(nombres))
            mets[0].metric(_("En todos los grupos"), r.resumen["en todos los grupos"])
            mets[1].metric(_("…con diferencias"), r.resumen["en todos, con diferencias"])
            for j, n in enumerate(nombres):
                mets[2 + j].metric(_('Solo en {0}').format(n), r.resumen.get(f"solo en {n}", 0))
            with st.expander(_("Estadísticas por grupo (filas, llaves, duplicados, exclusiones, lookups)")):
                est = pd.DataFrame({g: {k: str(v) for k, v in s.items()} for g, s in r.estadisticas.items()}).fillna("–")
                st.dataframe(_df(est), width="stretch")
            if len(r.diferencias):
                st.markdown(_("**Diferencias de importe**"))
                st.dataframe(_df(r.diferencias), hide_index=True, width="stretch")
            estados = ["(todos)"] + sorted(r.matriz["estado"].unique())
            ver = st.selectbox(_("Ver llaves"), estados, key="ver_estado", format_func=_)
            m = r.matriz if ver == "(todos)" else r.matriz[r.matriz["estado"] == ver]
            vista = m.map(lambda v: "✔" if v is True else ("" if v is False else v)).astype(str).replace({"None": "", "nan": ""})
            st.dataframe(_df(vista), hide_index=True, width="stretch")
            excel = ss.get("res_excel") or r.a_excel()
            st.download_button(_("Descargar resultado (Excel)"), excel, "conciliacion.xlsx")
            if ss.get("cfg_actual") is not None:
                with st.expander(_("💾 Guardar este cruce"), expanded=False):
                    st.caption(_("Se guardan la configuración (para repetirla con los archivos del próximo período) y el resultado."))
                    nom_def = ss.cfg_nombre or " vs ".join(nombres)
                    nombre_c = st.text_input(_("Nombre del cruce"), nom_def, key=f"nom_guardar_{V}")
                    notas = st.text_area(_("Notas (opcional)"), key=f"notas_guardar_{V}")
                    if st.button(_("Guardar"), type="primary", key="btn_guardar"):
                        g = almacen.guardar(nombre_c, ss.cfg_actual, r.resumen, ss.get("archivos_actual", []), excel,
                                            usuario_actual(), notas)
                        st.success(_('Guardado «{0}». Lo encontrás en la pestaña «Mis cruces» y arriba, en «Partir de un cruce guardado».').format(g.nombre))
    elif any(g is None for g in grupos_ui):
        st.caption(_("Para probar: Grupo 1 = ejemplos/grupos/g1_retenciones_sistema.csv + g1_padron_referencia.csv "
                   "(modo base + referencia) · Grupo 2 = ejemplos/grupos/g2_reporte_agente.csv"))


# ---------------------------------------------------------------- mis cruces
with tm:
    guardados = almacen.listar()
    if ss.cfg_nombre:
        st.success(_('Configuración «{0}» lista: andá a «🔀 Cruzar archivos» y subí los archivos del nuevo período.').format(ss.cfg_nombre))
    if not guardados:
        st.info(_("Todavía no hay cruces guardados. Después de cruzar, usá «💾 Guardar este cruce»."))
    for g in guardados:
        with st.expander(f"**{g.nombre}** · {g.fecha.replace('T', ' ')[:16]} · {g.usuario}"):
            a, b, c = st.columns(3)
            a.metric(_("En todos los grupos"), g.resumen.get("en todos los grupos", "–"))
            b.metric(_("…con diferencias"), g.resumen.get("en todos, con diferencias", "–"))
            c.metric(_("Llaves en el universo"), g.resumen.get("llaves en el universo", "–"))
            if g.notas:
                st.write(g.notas)
            st.caption(_("Archivos: {0}").format(", ".join(g.archivos)))
            filas = []
            for gi, gr in enumerate(g.config.grupos):
                for fi, ar in enumerate(gr.archivos):
                    filas.append({"grupo": gr.nombre, "modo": gr.modo if fi == 0 else "", "archivo": ar.nombre_original,
                                  "llave": " + ".join(ar.llave), "normalización": ar.transformacion,
                                  "filtros": "; ".join(describir_filtro(x) for x in ar.filtros),
                                  "trae": ", ".join(ar.traer)})
            st.dataframe(_df(pd.DataFrame(filas)), hide_index=True, width="stretch")
            if g.config.comparaciones:
                st.caption(_("Importes comparados: {0} · tolerancia {1}").format(
                    " · ".join(" vs ".join(c) for c in g.config.comparaciones), g.config.tolerancia))
            with st.expander(_("Resumen completo")):
                st.dataframe(_df(pd.DataFrame([{"indicador": k, "valor": v} for k, v in g.resumen.items()])), hide_index=True)
            x1, x2, x3 = st.columns(3)
            xl = almacen.excel(g.id)
            if xl:
                x1.download_button(_("Descargar Excel"), xl, f"{re.sub(r'[^A-Za-z0-9_-]+', '_', g.nombre)[:60]}.xlsx", key=f"dl_{g.id}")
            if x2.button(_("Reutilizar configuración"), key=f"reu_{g.id}"):
                aplicar_config(g)
                st.rerun()
            if x3.checkbox(_("Borrar"), key=f"conf_{g.id}") and x3.button(_("Confirmar borrado"), key=f"del_{g.id}"):
                almacen.borrar(g.id)
                st.rerun()
