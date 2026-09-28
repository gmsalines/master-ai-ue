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
ss.setdefault("spec", None)
ss.setdefault("manual", "")
ss.setdefault("iteraciones", [])

# ---------------------------------------------------------------- barra lateral
with st.sidebar:
    st.header("Modelo")
    opcion = st.selectbox("Proveedor", ["groq", "gemini", "openai", "openrouter", "ollama", "anthropic", "sin IA (línea base)"])
    if opcion == "sin IA (línea base)":
        proveedor = None
    else:
        defecto = PRESETS.get(opcion, (None, None, "claude-haiku-4-5-20251001"))[2]
        if opcion == "groq":
            modelo = st.selectbox("Modelo", ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "(otro)"],
                                  help="Cada modelo tiene su propio cupo diario en el plan gratuito.")
            if modelo == "(otro)":
                modelo = st.text_input("Nombre del modelo", "")
        else:
            modelo = st.text_input("Modelo", defecto)
        env = PRESETS.get(opcion, (None, "ANTHROPIC_API_KEY", None))[1] if opcion != "anthropic" else "ANTHROPIC_API_KEY"
        def _secreto(nombre):
            try:
                return st.secrets.get(nombre, "")  # Streamlit Cloud: Settings -> Secrets
            except Exception:  # noqa: BLE001 (sin archivo de secrets en local)
                return ""
        clave = st.text_input("API key", value=os.environ.get(env or "", "") or _secreto(env or ""), type="password",
                              help="No se guarda; solo se usa en esta sesión.") if env else None
        if opcion == "anthropic":
            proveedor = Anthropic(modelo=modelo, api_key=clave)
        else:
            extra = {"reasoning_effort": "low"} if ("gpt-oss" in modelo and opcion == "groq") else {}
            proveedor = OpenAICompatible(modelo=modelo, base_url=PRESETS[opcion][0], api_key=clave, nombre=opcion, extra=extra)
    max_it = st.slider("Máximo de iteraciones de autocorrección", 1, 6, 4)
    usar_cache = st.checkbox("Reutilizar respuestas guardadas (no gasta tokens al repetir)", value=True)
    modo = st.radio("Modo de extracción", ["secciones", "completo"],
                    help="Secciones: una llamada por parte (entra en los planes gratuitos). Completo: una sola respuesta.")
    st.divider()
    st.caption("Cargar una especificación existente")
    subida = st.file_uploader("spec.json", type=["json"], key="spec_json")
    if subida is not None and st.button("Usar esta especificación"):
        d = json.loads(subida.read())
        ss.spec = Especificacion.model_validate(d.get("spec", d))

st.title("Conciliación de archivos regulatorios")
st.caption("Subí dos archivos (CSV, Excel, TXT posicional o XML) y el sistema los cruza. Si un formato es nuevo, "
           "lo aprende de su manual técnico: un LLM lo convierte en una especificación formal y un verificador la comprueba.")
BIBLIO = RAIZ / "especificaciones"


def guardar_en_biblioteca(spec: Especificacion, nombre: str) -> str:
    import re as _re
    BIBLIO.mkdir(exist_ok=True)
    slug = _re.sub(r"[^a-z0-9]+", "_", nombre.lower()).strip("_")[:50] or "especificacion"
    (BIBLIO / f"{slug}.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return slug

tc, t1, t2, t3, t4 = st.tabs(["🔀 Cruzar archivos", "📘 Aprender un formato (manual)", "📋 Especificación",
                              "✅ Validar archivo", "🧾 Generar archivo"])

# ---------------------------------------------------------------- 1. compilar
with t1:
    ejemplos = {p.stem: p for p in sorted([*(RAIZ / "manuales").glob("*.md"), *(RAIZ / "manuales").glob("*.txt")])}
    c1, c2 = st.columns([1, 1])
    with c1:
        elegido = st.selectbox("Manual de ejemplo", ["(subir uno propio)"] + list(ejemplos))
    with c2:
        propio = st.file_uploader("…o subí un manual (md, txt, pdf)", type=["md", "txt", "pdf"])
    if propio is not None:
        if propio.name.lower().endswith(".pdf"):
            from pypdf import PdfReader

            ss.manual = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(propio.read())).pages)
        else:
            ss.manual = propio.read().decode("utf-8", errors="replace")
    elif elegido in ejemplos:
        ss.manual = ejemplos[elegido].read_text(encoding="utf-8")
    with st.expander("Ver manual", expanded=False):
        st.markdown(ss.manual or "_(vacío)_")

    if st.button("Compilar especificación", type="primary", disabled=not ss.manual):
        ss.iteraciones = []
        zona = st.container()
        if proveedor is None:
            d = extraer_base(ss.manual)
            res = verificar(d, ss.manual)
            ss.spec = res.spec
            zona.info(f"Línea base sin IA: {'válida' if res.valida else f'{len(res.errores)} errores'}")
            for p in res.errores[:30]:
                zona.write(f"- `{p.codigo}` {p.ruta}: {p.mensaje}")
        else:
            def al_iterar(it):
                ss.iteraciones.append(it)
                if it.errores == 0:
                    zona.success(f"Iteración {it.numero}: especificación verificada ✔ ({it.tokens_entrada + it.tokens_salida} tokens, {it.latencia_s:.1f} s)")
                else:
                    zona.warning(f"Iteración {it.numero}: {it.errores} errores → se devuelven al modelo · {it.errores_por_etapa}")
                    with zona.expander(f"Problemas detectados en la iteración {it.numero}"):
                        for p in it.problemas[:40]:
                            st.write("- " + p)
            with st.spinner("El modelo está leyendo el manual…"):
                def al_paso(p):
                    icono = "✔" if p.ok else "✖"
                    costo = ("sin IA" if "por código" in p.nombre else
                             "desde caché, 0 tokens" if p.latencia_s == 0 else f"{p.tokens_entrada + p.tokens_salida} tokens")
                    zona.write(f"{icono} Paso **{p.nombre}** ({costo}, {p.latencia_s:.0f} s)"
                               + ("" if p.ok else f" — {p.detalle}"))

                from regspec.llm.cache import ProveedorConCache

                proveedor = ProveedorConCache(proveedor, usar=usar_cache)
                r = extraer(ss.manual, proveedor, ConfigExtraccion(max_iteraciones=max_it, modo=modo),
                            al_iterar=al_iterar, al_paso=al_paso)
            if r.error:
                st.error(r.error)
            ss.spec = r.spec
            if r.spec is not None and r.valida:
                st.success(f"Guardada en la biblioteca como '{guardar_en_biblioteca(r.spec, r.spec.nombre)}': "
                           "desde ahora el sistema reconoce este formato al cruzar archivos.")
            if r.spec is not None:
                st.metric("Resultado", "Válida" if r.valida else "Con errores", f"{len(r.iteraciones)} iteraciones · {r.tokens} tokens")

# ---------------------------------------------------------------- 2. especificación
with t2:
    spec: Especificacion | None = ss.spec
    if spec is None:
        st.info("Primero compilá un manual o cargá una especificación.")
    else:
        res = verificar(spec, ss.manual or None, chequear_evidencia=bool(ss.manual))
        a, b, c, d = st.columns(4)
        a.metric("Formato", spec.formato)
        b.metric("Tipos de registro", len(spec.tipos_registro))
        c.metric("Campos", sum(len(t.campos) for t in spec.tipos_registro))
        d.metric("Reglas", len(spec.reglas))
        (st.success if res.valida else st.error)("Verificador: " + ("sin errores" if res.valida else f"{len(res.errores)} errores"))
        for p in res.errores:
            st.write(f"- `{p.codigo}` {p.ruta}: {p.mensaje}")
        for tr in spec.tipos_registro:
            st.subheader(f"{tr.codigo} · {tr.nombre}")
            st.caption(f"ocurrencias {tr.min_ocurrencias}..{tr.max_ocurrencias or 'n'} · posición {tr.posicion}")
            st.dataframe(pd.DataFrame([{k: v for k, v in c.model_dump().items() if k != "etiqueta_xml" or spec.formato == "xml"} for c in tr.campos]),
                         hide_index=True, width="stretch")
        if spec.reglas:
            st.subheader("Reglas")
            st.dataframe(pd.DataFrame([r.model_dump() for r in spec.reglas]), hide_index=True, width="stretch")
        x1, x2, x3 = st.columns(3)
        x1.download_button("Descargar especificación (JSON)", spec.model_dump_json(indent=2), "especificacion.json")
        if res.valida:
            ejemplo = escribir(spec, generar_registros(spec, 0)[0])
            x2.download_button("Archivo de ejemplo", ejemplo, "ejemplo." + ("xml" if spec.formato == "xml" else "txt"))
        x3.download_button("Esquema XSD", generar_xsd(spec), "esquema.xsd")

# ---------------------------------------------------------------- 3. validar
with t3:
    spec = ss.spec
    if spec is None:
        st.info("Primero compilá un manual o cargá una especificación.")
    else:
        arch = st.file_uploader("Archivo a validar", type=["txt", "xml", "dat"], key="validar")
        if arch is not None:
            inf = validar(spec, arch.read().decode("utf-8", errors="replace"))
            (st.success if inf.ok else st.error)(inf.resumen(0).splitlines()[0])
            if inf.hallazgos:
                st.dataframe(pd.DataFrame(inf.a_dicts()), hide_index=True, width="stretch")

# ---------------------------------------------------------------- 4. generar
with t4:
    spec = ss.spec
    if spec is None:
        st.info("Primero compilá un manual o cargá una especificación.")
    else:
        st.write("Cargá los datos de detalle (por ejemplo, el resultado de una conciliación). "
                 "Los registros únicos (cabecera) se completan abajo; los totales de control se calculan solos a partir de las reglas.")
        repetibles = [t.codigo for t in spec.tipos_registro if t.max_ocurrencias != 1]
        cod = st.selectbox("Tipo de registro de detalle", repetibles or [t.codigo for t in spec.tipos_registro])
        csv = st.file_uploader("CSV con los datos", type=["csv"], key="csv")
        tr = spec.tipo(cod)
        if csv is not None and tr is not None:
            df = pd.read_csv(csv, dtype=str, sep=None, engine="python")
            st.dataframe(df.head(), hide_index=True)
            campos = [c.nombre for c in tr.campos if c.tipo != "constante"]
            mapeo = {}
            cols = st.columns(3)
            for k, col in enumerate(df.columns):
                sug = difflib.get_close_matches(col.lower(), campos, n=1, cutoff=0.4)
                mapeo[col] = cols[k % 3].selectbox(f"{col} →", ["(ignorar)"] + campos,
                                                   index=(campos.index(sug[0]) + 1) if sug else 0, key=f"map_{col}")
            mapeo = {k: v for k, v in mapeo.items() if v != "(ignorar)"}
            unicos = {}
            for t in spec.tipos_registro:
                if t.codigo == cod:
                    continue
                with st.expander(f"Registro {t.codigo} · {t.nombre}"):
                    unicos[t.codigo] = {}
                    for c in t.campos:
                        if c.tipo == "constante":
                            continue
                        v = st.text_input(f"{c.descripcion or c.nombre} ({c.tipo})", key=f"u_{t.codigo}_{c.nombre}",
                                          help="Dejar vacío si se calcula a partir de las reglas")
                        if v:
                            unicos[t.codigo][c.nombre] = v
            if st.button("Generar archivo", type="primary"):
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
                        st.info("Calculado automáticamente: " + ", ".join(sorted(set(x.split(" (")[0].split("[")[0] for x in r.autocompletados))))
                    (st.success if r.informe.ok else st.warning)("Control previo: " + r.informe.resumen(0).splitlines()[0])
                    if r.informe.hallazgos:
                        st.dataframe(pd.DataFrame(r.informe.a_dicts()), hide_index=True)
                    st.download_button("Descargar archivo", r.contenido, "informe." + ("xml" if spec.formato == "xml" else "txt"))



# ---------------------------------------------------------------- flujo principal: cruzar
with tc:
    from decimal import Decimal

    from regspec.cruce import (
        biblioteca_de_especificaciones, cruzar, detectar_especificacion, llave_clara, sugerir_comparaciones,
        sugerir_llave, sugerir_llave_ia, tabla_desde_archivo,
    )
    from regspec.llm.cache import ProveedorConCache

    biblioteca = biblioteca_de_especificaciones(BIBLIO, RAIZ / "gold")
    if ss.spec is not None:
        biblioteca["(compilada en esta sesión)"] = ss.spec

    st.subheader("1 · Armá los grupos de archivos")

    def leer_uno(f, lado: str, i: int):
        if True:
            k = f"{lado}{i}"
            det = detectar_especificacion(f.getvalue(), f.name, biblioteca)
            spec_sel, tipo = det.spec, det.tipo_registro
            if f.name.lower().endswith((".csv", ".xlsx", ".xls")):
                st.caption("Tabla (CSV/Excel)")
            elif det.spec is not None and det.confianza >= 0.9:
                st.success(f"Formato reconocido: **{det.nombre_spec}** · {det.detalle}")
                with st.expander("Cambiar formato o tipo de registro"):
                    nombres = list(biblioteca)
                    n = st.selectbox("Especificación", nombres, index=nombres.index(det.nombre_spec), key=f"spec_{k}")
                    spec_sel = biblioteca[n]
                    tipos = [t.codigo for t in spec_sel.tipos_registro]
                    tipo = st.selectbox("Tipo de registro a cruzar", tipos,
                                        index=tipos.index(tipo) if tipo in tipos else 0, key=f"tipo_{k}")
            else:
                st.warning("No reconozco el formato de este archivo. Subí su **manual técnico** y lo aprendo "
                           "(usa IA una sola vez; después queda guardado).")
                man = st.file_uploader("Manual técnico (PDF, MD o TXT)", type=["pdf", "md", "txt"], key=f"manual_{k}")
                if man is not None and st.button("Aprender el formato", key=f"aprender_{k}", type="primary"):
                    if man.name.lower().endswith(".pdf"):
                        from pypdf import PdfReader
                        texto = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(man.getvalue())).pages)
                    else:
                        texto = man.getvalue().decode("utf-8", errors="replace")
                    with st.status("Leyendo el manual…", expanded=True) as estado:
                        if proveedor is None:
                            d = extraer_base(texto)
                            ver = verificar(d, texto)
                            spec_nueva, valida = ver.spec, ver.valida
                        else:
                            r = extraer(texto, ProveedorConCache(proveedor, usar=usar_cache),
                                        ConfigExtraccion(max_iteraciones=max_it, modo=modo),
                                        al_paso=lambda p: estado.write(f"{'✔' if p.ok else '✖'} {p.nombre}"),
                                        al_iterar=lambda it: estado.write(
                                            "verificación: OK" if it.errores == 0 else f"verificación: {it.errores} errores → corrigiendo"))
                            spec_nueva, valida = r.spec, r.valida
                            if r.error:
                                st.error(r.error)
                        if spec_nueva is not None and valida:
                            nombre = guardar_en_biblioteca(spec_nueva, spec_nueva.nombre)
                            estado.update(label=f"Formato aprendido y guardado como '{nombre}'", state="complete")
                            st.rerun()
                        else:
                            estado.update(label="No se obtuvo una especificación válida", state="error")
                            st.info("Podés revisarla y corregirla en la pestaña «Aprender un formato».")
                return None
            try:
                df = tabla_desde_archivo(f.getvalue(), f.name, spec_sel, tipo)
            except Exception as e:  # noqa: BLE001
                st.error(f"No se pudo leer {f.name}: {e}")
                return None
            st.caption(f"{len(df)} registros")
            return df

    from regspec.grupos import MODOS, OPERADORES, TRANSFORMACIONES, ArchivoGrupo, Filtro, Grupo, cruzar_grupos, transformar

    def transformacion_sugerida(serie: pd.Series) -> str:
        v = [str(x) for x in serie.dropna().astype(str).head(200) if str(x).strip()]
        if v and sum(1 for x in v if re.sub(r"\D", "", x) and not x.isdigit() and len(re.sub(r"\D", "", x)) >= len(x) * 0.7) >= len(v) * 0.3:
            return "solo_numeros"
        return "exacta"

    def columna_llave_sugerida(ref: Optional[tuple], df: pd.DataFrame) -> list[str]:
        """Llave sugerida para un archivo: la columna cuyos valores coinciden con la llave de referencia (sin IA)."""
        cols = [c for c in df.columns if not c.startswith("_")]
        if ref is not None:
            rdf, rcol, rtr = ref
            refv = {transformar(x, rtr) for x in rdf[rcol].dropna().head(2000)}
            mejor, puntaje = None, 0.0
            for c in cols:
                for tr in ("exacta", "solo_numeros", "sin_ceros"):
                    vals = {transformar(x, tr) for x in df[c].dropna().head(2000)} - {""}
                    if vals:
                        p = len(vals & refv) / len(vals)
                        if p > puntaje:
                            mejor, puntaje = c, p
            if mejor and puntaje >= 0.3:
                return [mejor]
        # sin referencia: la columna más "identificadora" (valores casi únicos y con nombre típico)
        def score(c):
            s = df[c].dropna().astype(str)
            unic = s.nunique() / max(1, len(s))
            tokens = [t for t in re.split(r"[^a-z0-9]+", c.lower()) if t]
            ids = ("id", "nro", "numero", "num", "codigo", "cod", "clave", "llave", "doc", "documento", "comprobante",
                   "identificador", "identification", "contribuyente", "cuenta", "poliza", "referencia", "ref")
            nombre = 1.0 if any(t in ids or t.startswith(("ident", "docu", "compro")) for t in tokens) else 0.0
            if any(t in ("monto", "importe", "total", "fecha", "saldo", "valor") for t in tokens):
                nombre -= 1.0
            return unic + nombre
        return [max(cols, key=score)] if cols else []

    ss.setdefault("n_grupos", 2)
    st.caption("Cada **grupo** reúne uno o varios archivos. En cada archivo definís su **llave** (y cómo normalizarla); "
               "el cruce se hace sobre esa llave entre todos los grupos.")
    grupos_ui = []
    referencia = None  # (df, col, transformación) de la primera llave definida: guía las sugerencias del resto
    for gi in range(ss.n_grupos):
        with st.container(border=True):
            c_nom, c_modo = st.columns([1, 2])
            nombre_g = c_nom.text_input("Nombre del grupo", f"Grupo {gi + 1}", key=f"gnom_{gi}")
            archivos = st.file_uploader(f"Archivos de «{nombre_g}» (uno o varios)", type=["csv", "xlsx", "xls", "txt", "xml", "dat"],
                                        accept_multiple_files=True, key=f"gfiles_{gi}")
            modo_g = c_modo.selectbox("Cómo combinar los archivos del grupo", list(MODOS), format_func=MODOS.get,
                                      key=f"gmodo_{gi}", disabled=not archivos or len(archivos) < 2)
            if not archivos:
                grupos_ui.append(None)
                continue
            arch_objs, completo = [], True
            for fi, f in enumerate(archivos):
                with st.expander(f"📄 {f.name}", expanded=True):
                    df = leer_uno(f, f"g{gi}", fi)
                    if df is None:
                        completo = False
                        continue
                    cols = [c for c in df.columns if not c.startswith("_")]
                    sug = columna_llave_sugerida(referencia, df)
                    c1, c2 = st.columns([2, 1])
                    llave = c1.multiselect("Llave (una o varias columnas)", cols, default=sug, key=f"llave_{gi}_{fi}")
                    tr_def = transformacion_sugerida(df[llave[0]]) if llave else "exacta"
                    tr = c2.selectbox("Normalizar la llave", list(TRANSFORMACIONES), format_func=TRANSFORMACIONES.get,
                                      index=list(TRANSFORMACIONES).index(tr_def), key=f"tr_{gi}_{fi}")
                    if llave:
                        st.caption("Ejemplos de llave: " + ", ".join(
                            "|".join(transformar(v, tr) for v in fila) for fila in df[llave].head(3).itertuples(index=False)))
                    filtros = []
                    fc1, fc2, fc3 = st.columns(3)
                    fcol = fc1.selectbox("Excluir filas donde…", ["(sin filtro)"] + cols, key=f"fcol_{gi}_{fi}")
                    if fcol != "(sin filtro)":
                        fop = fc2.selectbox("condición", OPERADORES, key=f"fop_{gi}_{fi}")
                        fval = fc3.text_input("valor", key=f"fval_{gi}_{fi}") if fop not in ("vacío", "no vacío") else ""
                        filtros.append(Filtro(fcol, fop, fval))
                    traer = []
                    if modo_g == "base_referencia" and fi > 0:
                        traer = st.multiselect("Columnas a traer a la base", [c for c in cols if c not in llave], key=f"traer_{gi}_{fi}")
                    if llave:
                        arch_objs.append(ArchivoGrupo(f.name, df, llave, tr, filtros, traer))
                        if referencia is None:
                            referencia = (df, llave[0], tr)
                    else:
                        completo = False
            if not completo or not arch_objs:
                grupos_ui.append(None)
                continue
            cols_g = sorted({c for a in arch_objs for c in a.df.columns if not c.startswith("_")} |
                            {c for a in arch_objs for c in a.traer})
            grupos_ui.append((nombre_g, arch_objs, modo_g, cols_g))
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("➕ Agregar grupo"):
        ss.n_grupos += 1
        st.rerun()
    if ss.n_grupos > 2 and b2.button("➖ Quitar el último"):
        ss.n_grupos -= 1
        st.rerun()

    listos = [g for g in grupos_ui if g is not None]
    if len(listos) >= 2 and len(listos) == len(grupos_ui):
        st.subheader("2 · ¿Qué comparo en cada llave?")
        st.caption("Elegí la columna de importe de cada grupo; se suman por llave y se comparan con la tolerancia.")
        n_cmp = st.number_input("Cantidad de importes a comparar", 0, 5, 1, key="ncmp")
        comparaciones = []
        for k in range(int(n_cmp)):
            cc = st.columns(len(listos))
            fila = []
            for j, (nom, _, _, cols_g) in enumerate(listos):
                num = [c for c in cols_g if re.search(r"monto|importe|total|base|valor|saldo|retenid|prima|pagad", c, re.I)] or cols_g
                fila.append(cc[j].selectbox(f"{nom}", cols_g, index=cols_g.index(num[min(k, len(num) - 1)]), key=f"cmp_{k}_{j}"))
            comparaciones.append(tuple(fila))
        tol = st.number_input("Tolerancia en importes", value=0.01, min_value=0.0, step=0.01, format="%.2f", key="tol_g")

        st.subheader("3 · Resultado")
        if st.button("Cruzar", type="primary"):
            grupos = [Grupo(nom, arch, modo, []) for nom, arch, modo, _ in listos]
            with st.spinner("Cruzando…"):
                ss.res_grupos = cruzar_grupos(grupos, comparaciones, Decimal(str(tol)))
        r = ss.get("res_grupos")
        if r is not None:
            nombres = [n for n, *_ in listos]
            mets = st.columns(2 + len(nombres))
            mets[0].metric("En todos los grupos", r.resumen["en todos los grupos"])
            mets[1].metric("…con diferencias", r.resumen["en todos, con diferencias"])
            for j, n in enumerate(nombres):
                mets[2 + j].metric(f"Solo en {n}", r.resumen.get(f"solo en {n}", 0))
            with st.expander("Estadísticas por grupo (filas, llaves, duplicados, exclusiones, lookups)"):
                st.dataframe(pd.DataFrame(r.estadisticas).astype(str), width="stretch")
            if len(r.diferencias):
                st.markdown("**Diferencias de importe**")
                st.dataframe(r.diferencias, hide_index=True, width="stretch")
            estados = ["(todos)"] + sorted(r.matriz["estado"].unique())
            ver = st.selectbox("Ver llaves", estados, key="ver_estado")
            m = r.matriz if ver == "(todos)" else r.matriz[r.matriz["estado"] == ver]
            st.dataframe(m.astype(str), hide_index=True, width="stretch")
            st.download_button("Descargar resultado (Excel)", r.a_excel(), "conciliacion.xlsx")
    elif any(g is None for g in grupos_ui):
        st.caption("Para probar: Grupo 1 = ejemplos/grupos/g1_retenciones_sistema.csv + g1_padron_referencia.csv "
                   "(modo base + referencia) · Grupo 2 = ejemplos/grupos/g2_reporte_agente.csv")
