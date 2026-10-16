"""Demo interactiva: de la normativa al archivo validado.

    streamlit run app.py
"""
from __future__ import annotations

import difflib
import copy
import io
import json
import os
import re
import time
from decimal import Decimal
from typing import Optional
from pathlib import Path

import pandas as pd
import streamlit as st


def _refrescar_regspec() -> None:
    """Al publicar una versión nueva, Streamlit recarga app.py pero puede conservar en memoria los módulos
    viejos del paquete: si cambió algún archivo de regspec/, se descartan para que se importen de nuevo."""
    import sys
    carpeta = Path(__file__).parent / "regspec"
    firma = tuple(sorted((str(p), p.stat().st_mtime_ns) for p in carpeta.rglob("*.py")))
    previa = sys.modules.get("_regspec_firma")
    if getattr(previa, "firma", None) != firma:  # sin marca = primera vez con esta guarda: también se descarta
        viejos = [m for m in sys.modules if m == "regspec" or m.startswith("regspec.")]
        for nombre in viejos:
            del sys.modules[nombre]
        if viejos:
            st.cache_resource.clear()
            st.cache_data.clear()
    import types
    marca = types.ModuleType("_regspec_firma")
    marca.firma = firma
    sys.modules["_regspec_firma"] = marca


_refrescar_regspec()

from regspec.archivos import escribir, generar_xsd
from regspec.base_sin_ia import extraer_base
from regspec.dsl import Especificacion
from regspec.generador import generar, registros_desde_tabla
from regspec.llm.extractor import ConfigExtraccion, extraer
from regspec.llm.proveedores import PRESETS, Anthropic, ConRespaldo, OpenAICompatible
from regspec.sintetico import generar_registros
from regspec.validador import validar
from regspec.verificador import verificar

RAIZ = Path(__file__).parent
st.set_page_config(page_title="Conciliación de archivos heterogéneos", page_icon="🧾", layout="wide")
ss = st.session_state

from regspec.i18n import IDIOMAS, traducir, traducir_df  # noqa: E402


def _idioma_inicial() -> str:
    try:
        loc = (getattr(st.context, "locale", None) or "").lower()
    except Exception:  # noqa: BLE001
        loc = ""
    return "pt" if loc.startswith("pt") else "es"


ss.setdefault("idioma", _idioma_inicial())
if "idioma_pendiente" in ss:  # idioma del perfil, al entrar
    ss.idioma = ss.pop("idioma_pendiente")


def _(texto):
    """Texto de interfaz en el idioma elegido (español es la base)."""
    return traducir(texto, ss.idioma)


def _df(df, **kw):
    return traducir_df(df, ss.idioma, **kw)


import ui_estilo  # noqa: E402

st.markdown(ui_estilo.CSS, unsafe_allow_html=True)


ss.setdefault("spec", None)
ss.setdefault("manual", "")
ss.setdefault("iteraciones", [])

# ---------------------------------------------------------------- barra lateral
# ---------------------------------------------------------------- acceso (solo si la app tiene Supabase configurado)
def _config_supabase():
    try:
        url, key = st.secrets.get("SUPABASE_URL", ""), st.secrets.get("SUPABASE_ANON_KEY", "")
    except Exception:  # noqa: BLE001 (sin archivo de secrets)
        url = key = ""
    return url or os.environ.get("SUPABASE_URL", ""), key or os.environ.get("SUPABASE_ANON_KEY", "")


SB_URL, SB_KEY = _config_supabase()
if SB_URL and SB_KEY and not ss.get("sesion"):
    _c1, _c2, _c3 = st.columns([1, 1.2, 1])
    with _c2:
        st.selectbox("🌐 Idioma", list(IDIOMAS), format_func=IDIOMAS.get, key="idioma")
        st.markdown(ui_estilo.encabezado(_("Conciliación de archivos heterogéneos"), _("Entrá con tu mail para ver tus cruces y configuraciones.")), unsafe_allow_html=True)
        with st.form("login"):
            st.subheader(_("Entrar"))
            email = st.text_input(_("Mail"))
            clave_login = st.text_input(_("Contraseña"), type="password")
            entrar = st.form_submit_button(_("Entrar"), type="primary", use_container_width=True)
        if entrar:
            from supabase import create_client
            try:
                cli = create_client(SB_URL, SB_KEY)
                resp = cli.auth.sign_in_with_password({"email": email.strip(), "password": clave_login})
                perfil = cli.table("perfil").select("*").eq("id", resp.user.id).execute().data
                if not perfil or not perfil[0].get("workspace_id"):
                    st.error(_("Tu usuario no tiene un espacio de trabajo asignado. Pedile acceso a la administradora."))
                else:
                    ss.sb = cli
                    ss.sesion = {"id": resp.user.id, "email": resp.user.email, "nombre": perfil[0].get("nombre") or "",
                                 "workspace_id": perfil[0]["workspace_id"], "idioma": perfil[0].get("idioma") or "es"}
                    if ss.sesion["idioma"] in IDIOMAS:
                        ss.idioma_pendiente = ss.sesion["idioma"]  # se aplica al recargar (el selector ya existe)
                    st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(_("Mail o contraseña incorrectos.") if "Invalid login" in str(e) else f"{_('No se pudo entrar')}: {e}")
    st.stop()

with st.sidebar:
    st.selectbox("🌐 Idioma", list(IDIOMAS), format_func=IDIOMAS.get, key="idioma")
    if ss.get("sesion"):
        st.caption(f"👤 {ss.sesion['nombre'] or ss.sesion['email']} · {ss.sesion['email']}")
        if ss.idioma != ss.sesion.get("idioma"):  # recordar el idioma elegido en el perfil
            try:
                ss.sb.table("perfil").update({"idioma": ss.idioma}).eq("id", ss.sesion["id"]).execute()
                ss.sesion["idioma"] = ss.idioma
            except Exception:  # noqa: BLE001
                pass
        if st.button(_("Salir")):
            try:
                ss.sb.auth.sign_out()
            except Exception:  # noqa: BLE001
                pass
            for k in list(ss.keys()):
                if k != "idioma":
                    del ss[k]
            st.rerun()
        st.divider()
    def _secreto(nombre):
        try:
            v = st.secrets.get(nombre, "")  # Streamlit Cloud: Settings -> Secrets
        except Exception:  # noqa: BLE001 (sin archivo de secrets en local)
            v = ""
        return v or os.environ.get(nombre, "")

    # Con login, la configuración del modelo solo la ven las administradoras (secret ADMINS = "mail1, mail2").
    # El resto usa el modelo por defecto con la clave de los Secrets, que nunca se muestra.
    _admins = {m.strip().lower() for m in str(_secreto("ADMINS") or "").replace(";", ",").split(",") if m.strip()}
    ver_config = not ss.get("sesion") or ss.sesion["email"].lower() in _admins
    st.caption(_("Prototipo del Trabajo Fin de Máster. El cruce siempre lo hace el código; la IA solo propone y todo se valida."))
    _panel = st.expander(_("Configuración avanzada (IA)")) if ver_config else st.container()
    with _panel:
        if ver_config:
            opcion = st.selectbox(_("Proveedor"), ["groq", "gemini", "openai", "openrouter", "ollama", "anthropic", "sin IA (línea base)"],
                                  format_func=_)
        else:
            opcion = "groq"
        if opcion == "sin IA (línea base)":
            proveedor = None
        else:
            defecto = PRESETS.get(opcion, (None, None, "claude-haiku-4-5-20251001"))[2]
            if not ver_config:
                modelo = "openai/gpt-oss-120b"
            elif opcion == "groq":
                modelo = st.selectbox(_("Modelo"), ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "(otro)"], format_func=_,
                                      help=_("Cada modelo tiene su propio cupo diario en el plan gratuito."))
                if modelo == "(otro)":
                    modelo = st.text_input(_("Nombre del modelo"), "")
            else:
                modelo = st.text_input(_("Modelo"), defecto)
            env = PRESETS.get(opcion, (None, "ANTHROPIC_API_KEY", None))[1] if opcion != "anthropic" else "ANTHROPIC_API_KEY"
            clave_config = os.environ.get(env or "", "") or _secreto(env or "")
            clave = None
            if env:
                # la clave configurada nunca se muestra en pantalla: el campo queda vacío y solo reemplaza si se escribe otra
                escrita = st.text_input(_("API key"), value="", type="password",
                                        placeholder=_("(usa la clave configurada)") if clave_config else "",
                                        help=_("No se guarda; solo se usa en esta sesión.")) if ver_config else ""
                clave = escrita or clave_config
            if opcion == "anthropic":
                proveedor = Anthropic(modelo=modelo, api_key=clave)
            else:
                extra = {"reasoning_effort": "low"} if ("gpt-oss" in modelo and opcion == "groq") else {}
                proveedor = OpenAICompatible(modelo=modelo, base_url=PRESETS[opcion][0], api_key=clave, nombre=opcion, extra=extra)
                # cada modelo de Groq tiene su propio cupo diario: si el elegido se agota, se sigue con el otro y,
                # si hay clave de OpenRouter (crédito prepago, que hace de tope de gasto), con gpt-oss-120b allí
                if opcion == "groq":
                    otro = {"openai/gpt-oss-120b": "openai/gpt-oss-20b", "openai/gpt-oss-20b": "openai/gpt-oss-120b"}.get(modelo)
                    cadena = [proveedor]
                    if otro:
                        cadena.append(OpenAICompatible(modelo=otro, base_url=PRESETS["groq"][0], api_key=clave, nombre="groq", extra=extra))
                    clave_or = os.environ.get("OPENROUTER_API_KEY", "") or _secreto("OPENROUTER_API_KEY")
                    if clave_or:
                        cadena.append(OpenAICompatible(modelo="openai/gpt-oss-120b", base_url=PRESETS["openrouter"][0],
                                                       api_key=clave_or, nombre="openrouter"))
                    respaldo = len(cadena) > 1 and (not ver_config or st.checkbox(
                        _("Si se agota el cupo, seguir con otro modelo"), value=True,
                        help=_("Orden: {0}. La parte ya hecha no se vuelve a pagar (caché) y el resultado avisa del cambio.").format(
                            " → ".join(f"{p.nombre}/{p.modelo.split('/')[-1]}" for p in cadena))))
                    if respaldo:
                        proveedor = ConRespaldo(cadena)
        if ver_config:
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
        else:
            max_it, usar_cache, modo = 4, True, "secciones"

st.markdown(ui_estilo.encabezado(
    _("Conciliación de archivos heterogéneos"),
    _("Subí los archivos que tenés que cruzar, revisá cómo se comparan y descargá el resultado. "
      "Si un formato es nuevo, el sistema lo aprende de su manual técnico."),
    _("Prototipo · Trabajo Fin de Máster")), unsafe_allow_html=True)
BIBLIO = RAIZ / "especificaciones"


def con_cache(prov):
    """Proveedor con caché; con respaldo, cada modelo de la cadena tiene la suya."""
    from regspec.llm.cache import ProveedorConCache

    return prov.con_cache(usar_cache) if isinstance(prov, ConRespaldo) else ProveedorConCache(prov, usar=usar_cache)


def avisar_respaldo(prov, r, donde=st) -> None:
    """Informa el cambio de modelo por cupo, o cómo seguir si se cortó."""
    for antes, despues in getattr(prov, "cambios", []):
        donde.info(_("Se agotó el cupo diario de {0}: la extracción siguió con {1} desde donde había quedado.").format(antes, despues))
    if r is not None and r.cupo_agotado:
        donde.warning(_("La extracción se cortó porque el proveedor agotó el cupo diario: la especificación quedó a medio "
                        "corregir. Volvé a compilar cuando se libere o con otro modelo; la parte ya hecha queda guardada "
                        "y no vuelve a gastar tokens."))
        if r.error:
            donde.caption(r.error)


def n_es(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def guardar_en_biblioteca(spec: Especificacion, nombre: str) -> str:
    import re as _re
    BIBLIO.mkdir(exist_ok=True)
    import unicodedata as _ud
    plano = _ud.normalize("NFKD", nombre.lower()).encode("ascii", "ignore").decode()  # «Padrón» -> «padron»
    slug = _re.sub(r"[^a-z0-9]+", "_", plano).strip("_")[:50] or "especificacion"
    (BIBLIO / f"{slug}.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    alm = globals().get("almacen")
    if alm is not None and alm.remoto:  # en la nube el disco no persiste: el formato queda en la cuenta del usuario
        try:
            alm.guardar_formato(slug, json.loads(spec.model_dump_json()), "ia" if globals().get("proveedor") is not None else "parser")
            ss.pop("formatos_cache", None)
        except Exception as e:  # noqa: BLE001
            st.warning(f"{_('No se pudo guardar el formato en la cuenta')}: {e}")
    return slug

from regspec.almacen import AgregacionCfg, AlmacenLocal, ArchivoCfg, ConfigCruce, ControlCfg, FiltroCfg, GrupoCfg

from regspec.almacen import AlmacenSupabase, config_equivalente, firma_archivos  # noqa: E402

if ss.get("sesion"):
    almacen = AlmacenSupabase(ss.sb, ss.sesion["id"], ss.sesion["workspace_id"], ss.sesion["email"])
else:
    almacen = AlmacenLocal(RAIZ / "cruces_guardados", usuario="local")


def formatos_del_usuario() -> dict:
    """Formatos aprendidos guardados en la cuenta (en la nube); vacío en modo local."""
    if not almacen.remoto:
        return {}
    if "formatos_cache" not in ss:
        out = {}
        try:
            for n, d in almacen.formatos().items():
                try:
                    out[n] = Especificacion.model_validate(d)
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        ss.formatos_cache = out
    return ss.formatos_cache


def usuario_actual() -> str:
    """Mail del usuario conectado; si no hay login, 'local'."""
    if ss.get("sesion"):
        return ss.sesion["email"]
    try:
        u = getattr(st, "user", None)
        mail = u.get("email") if u is not None else None
        return mail or "local"
    except Exception:  # noqa: BLE001
        return "local"


def aplicar_config(cruce, origen: str = "memoria", memoria_id: Optional[str] = None) -> None:
    """Deja lista una configuración guardada (o aprendida) para aplicarla a los archivos que se suban."""
    ss.cfg = cruce.config if cruce is not None else None
    ss.cfg_nombre = cruce.nombre if cruce is not None else None
    ss.origen = origen if cruce is not None else "heuristica"
    ss.memoria_cfg = cruce.config if cruce is not None else None
    ss.memoria_id = memoria_id
    if cruce is not None:
        ss.n_grupos = max(2, len(cruce.config.grupos))
    ss.cfg_ver = ss.get("cfg_ver", 0) + 1
    ss.pop("res_grupos", None)


def describir_filtro(x) -> str:
    from regspec.grupos import ACCIONES, OPERADORES_TEXTO
    vals = x.valores or ([x.valor] if x.valor else [])
    accion = _(ACCIONES.get(x.accion, x.accion)).rstrip("…")
    return f"{accion} {x.columna} {_(OPERADORES_TEXTO.get(x.operador, x.operador))} " + " | ".join(vals)


GRANDE = 50_000     # filas a partir de las cuales el Excel se arma a pedido
MOSTRAR = 5_000     # filas del cruce que se muestran en pantalla

ss.setdefault("cfg", None)
ss.setdefault("cfg_nombre", None)
ss.setdefault("cfg_ver", 0)

_pestanas = ["🔀 Cruzar archivos", "🗂 Mis cruces", "📘 Aprender un formato (manual)", "📋 Especificación",
             "✅ Validar archivo", "🧾 Generar archivo"] + (["📊 Métricas"] if ver_config else [])
tc, tm, t1, t2, t3, t4, *_t_met = st.tabs([_(x).split(" ", 1)[-1] for x in _pestanas])  # sin el emoji

# ---------------------------------------------------------------- 1. compilar
with t1:
    st.markdown(ui_estilo.etiquetas(
        ("ia", _("Asistido por IA"), _("Un modelo de lenguaje propone la especificación a partir del manual.")),
        ("det", _("Determinístico"), _("Un verificador con código la revisa y le devuelve los errores para que los corrija."))),
        unsafe_allow_html=True)
    st.markdown(ui_estilo.lema(_("La IA propone, el verificador decide."),
                               _("El código comprueba estructura, posiciones, reglas y evidencia en el manual, y prueba la especificación generando y releyendo un archivo.")),
                unsafe_allow_html=True)
    ejemplos = {p.stem: p for p in sorted([*(RAIZ / "manuales").glob("*.md"), *(RAIZ / "manuales").glob("*.txt")])}
    c1, c2 = st.columns([1, 1])
    with c1:
        elegido = st.selectbox(_("Manual de ejemplo"), ["(subir uno propio)"] + list(ejemplos), format_func=_)
    with c2:
        propio = st.file_uploader(_("…o subí un manual (md, txt, pdf)"), type=["md", "txt", "pdf"])
    if propio is not None:
        if propio.name.lower().endswith(".pdf"):
            from regspec.texto_pdf import texto_de_pdf

            ss.manual = texto_de_pdf(propio.read())
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

                prov_ext = con_cache(proveedor)
                r = extraer(ss.manual, prov_ext, ConfigExtraccion(max_iteraciones=max_it, modo=modo),
                            al_iterar=al_iterar, al_paso=al_paso)
            if r.error and not r.cupo_agotado:
                st.error(r.error)
            avisar_respaldo(prov_ext, r)
            ss.spec = r.spec
            if r.notas:
                with st.expander(_("Notas de interpretación ({0}): decisiones del sistema para revisar contra el manual").format(len(r.notas))):
                    for nota in r.notas:
                        st.write("- " + nota)
            # un manual puede describir varios archivos: cada formato se guarda por separado si quedó válido
            ss.sin_guardar = []
            for parte in (r.partes or [r]):
                if parte.spec is None:
                    continue
                if parte.valida:
                    st.success(_("Guardada en la biblioteca como '{0}': desde ahora el sistema reconoce este formato al cruzar archivos.").format(guardar_en_biblioteca(parte.spec, parte.spec.nombre)))
                else:
                    ss.sin_guardar.append(parte.spec)
            ss.sin_guardar_cupo = r.cupo_agotado
            if r.spec is not None:
                n_it = len(r.iteraciones)
                detalle = (_("1 iteración") if n_it == 1 else _("{0} iteraciones").format(n_it)) + " · " + \
                    _("{0} tokens").format(n_es(r.tokens))
                if r.partes:
                    detalle = _("{0} formatos ({1} válidos)").format(len(r.partes), sum(p.valida for p in r.partes)) + " · " + detalle
                estado_txt = _("Válida") if r.valida else (_("Interrumpida por cupo") if r.cupo_agotado else _("Con errores"))
                st.metric(_("Resultado"), estado_txt)
                st.caption(detalle)

    # especificaciones con errores: no se guardan solas, pero el usuario puede guardarlas igual (queda advertido)
    if ss.get("sin_guardar"):
        with st.container(border=True):
            nombres_sg = ", ".join(f"'{x.nombre}'" for x in ss.sin_guardar)
            if ss.get("sin_guardar_cupo"):
                st.warning(_("No se guardó en la biblioteca: {0}. La corrección no llegó a terminar por el cupo del modelo; "
                             "conviene volver a compilar antes de guardarla.").format(nombres_sg))
            else:
                st.warning(_("No se guardó en la biblioteca: {0}. El verificador encontró errores; revisalos en «Especificación».").format(nombres_sg))
            if st.button(_("Guardar igual en la biblioteca")):
                from regspec.llm.extractor import archivos_independientes, normalizar_decimales, separar_archivos

                for sp in ss.sin_guardar:
                    d, _notas = normalizar_decimales(json.loads(sp.model_dump_json()))
                    # varios archivos en una especificación: se guarda uno por archivo (cada uno puede quedar válido)
                    partes = separar_archivos(d) if archivos_independientes(d, ss.manual or "") else [d]
                    for pd_ in partes:
                        ver = verificar(pd_, ss.manual or None, chequear_evidencia=bool(ss.manual))
                        sp_ = ver.spec or Especificacion.model_validate(pd_)
                        slug = guardar_en_biblioteca(sp_, sp_.nombre)
                        (st.success if ver.valida else st.warning)(
                            _("Guardada en la biblioteca como '{0}'.").format(slug) + ("" if ver.valida else " " + _(
                                "Tiene {0} errores del verificador: revisá que lea bien el archivo antes de usarla.").format(len(ver.errores))))
                ss.sin_guardar = []

# ---------------------------------------------------------------- 2. especificación
with t2:
    st.markdown(ui_estilo.etiquetas(
        ("det", _("Determinístico"), _("Registros, campos y reglas de la especificación activa, con el resultado de su verificación."))),
        unsafe_allow_html=True)
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
    st.markdown(ui_estilo.etiquetas(
        ("det", _("Determinístico"), _("El archivo se valida con código contra la especificación; no se usa IA."))),
        unsafe_allow_html=True)
    from regspec.cruce import _decodificar, biblioteca_de_especificaciones as _biblio, detectar_especificacion as _detectar

    @st.cache_resource(show_spinner=False, max_entries=8)
    def validar_cacheado(firma: tuple, _contenido: bytes, _spec):
        """Valida una sola vez por archivo y especificación (antes se repetía en cada clic de la app)."""
        with st.spinner(_("Validando campo a campo y reglas…")):
            return validar(_spec, _decodificar(_contenido, _spec.codificacion))

    @st.cache_resource(show_spinner=False, max_entries=8)
    def detectar_validar(firma: tuple, _contenido: bytes, nombre: str, nombres: tuple, _specs: dict):
        return _detectar(_contenido, nombre, _specs)

    specs_v = {**_biblio(RAIZ / "especificaciones", RAIZ / "biblioteca", RAIZ / "gold"), **formatos_del_usuario()}
    if ss.spec is not None:
        specs_v = {"(compilada en esta sesión)": ss.spec, **specs_v}
    arch = st.file_uploader(_("Archivo a validar"), type=["txt", "xml", "dat"], key="validar")
    if arch is not None:
        det = detectar_validar((arch.name, arch.size, getattr(arch, "file_id", "")), arch.getvalue(), arch.name, tuple(specs_v), specs_v)
        nombres_v = list(specs_v)
        idx = nombres_v.index(det.nombre_spec) if det.nombre_spec in nombres_v else 0
        if det.spec is not None:
            st.caption(_('Formato reconocido: {0} · {1}').format(det.nombre_spec, _(det.detalle)))
        elegido = st.selectbox(_("Especificación"), nombres_v, index=idx, key="spec_validar", format_func=_)
        spec = specs_v[elegido]
        inf = validar_cacheado((arch.name, arch.size, getattr(arch, "file_id", ""), elegido), arch.getvalue(), spec)
        (st.success if inf.ok else st.error)(_(inf.resumen(0).splitlines()[0]))
        if inf.hallazgos:
            st.dataframe(_df(pd.DataFrame(inf.a_dicts())), hide_index=True, width="stretch")

# ---------------------------------------------------------------- 4. generar
with t4:
    st.markdown(ui_estilo.etiquetas(
        ("det", _("Determinístico"), _("El archivo se arma con código a partir de la especificación y se controla antes de descargarlo."))),
        unsafe_allow_html=True)
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
            from regspec.cruce import leer_csv
            df = leer_csv(csv.getvalue())
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

    biblioteca = {**biblioteca_de_especificaciones(BIBLIO, RAIZ / "biblioteca", RAIZ / "gold"), **formatos_del_usuario()}

    @st.cache_resource(show_spinner="Reconociendo el formato…", max_entries=16)
    def detectar_cacheado(firma: tuple, _contenido: bytes, nombre: str, nombres_specs: tuple, _biblio: dict):
        return detectar_especificacion(_contenido, nombre, _biblio)

    @st.cache_resource(show_spinner="Leyendo el archivo…", max_entries=16)
    def tabla_cacheada(firma: tuple, _contenido: bytes, nombre: str, spec_json: Optional[str], tipo: Optional[str], hoja: Optional[str]):
        spec = Especificacion.model_validate_json(spec_json) if spec_json else None
        return tabla_desde_archivo(_contenido, nombre, spec, tipo, hoja)

    @st.cache_resource(show_spinner=False, max_entries=16)
    def hojas_cacheado(firma: tuple, _contenido: bytes, nombre: str = ""):
        from regspec.cruce import desempaquetar, hoja_por_defecto, hojas_excel
        contenido, _n = desempaquetar(_contenido, nombre)  # un Excel dentro de un ZIP
        return hojas_excel(contenido), hoja_por_defecto(contenido)

    @st.cache_resource(show_spinner=False, max_entries=64)
    def nombre_interno_cacheado(firma: tuple, _contenido: bytes, nombre: str) -> str:
        from regspec.cruce import nombre_interno
        return nombre_interno(_contenido, nombre)

    def columnas_distintas(a: pd.DataFrame, b: pd.DataFrame) -> bool:
        """True si dos tablas comparten menos de la mitad de sus columnas (no son el mismo tipo de archivo)."""
        ca = {c for c in a.columns if not str(c).startswith("_")}
        cb = {c for c in b.columns if not str(c).startswith("_")}
        return len(ca & cb) / max(1, len(ca | cb)) < 0.5

    def firma_de(f) -> tuple:
        """Identifica un archivo subido sin recorrer su contenido (clave de caché instantánea aun para cientos de MB)."""
        return (f.name, f.size, getattr(f, "file_id", ""))

    @st.cache_resource(show_spinner=False, max_entries=64)
    def valores_distintos(firma: tuple, columna: str, _serie: pd.Series) -> tuple[list, int]:
        u = pd.unique(_serie.dropna().astype(str).str.strip())
        u = [v for v in u if v]
        return (sorted(u) if len(u) <= 500 else []), len(u)
    if ss.spec is not None:
        biblioteca["(compilada en esta sesión)"] = ss.spec

    st.markdown(ui_estilo.etiquetas(
        ("det", _("Determinístico"), _("El cruce, los importes y las diferencias los calcula el código.")),
        ("ia", _("Asistido por IA"), _("Solo si hace falta: propone la llave o la configuración y se valida antes de usarla."))),
        unsafe_allow_html=True)
    paso_ph = st.empty()  # casillas de los pasos: se completan al final, cuando se sabe en qué paso está
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
            aplicar_config(guardados[sel - 1] if sel else None, "guardado")
            st.rerun()
    cfg: Optional[ConfigCruce] = ss.cfg
    V = f"v{ss.cfg_ver}"  # sufijo de claves: cambia al aplicar una configuración y reinicia los controles
    if cfg is not None:
        st.info(_('Usando la configuración **{0}**. Subí los archivos del período en el mismo orden (grupo y posición dentro del grupo) y revisá las llaves antes de cruzar.').format(ss.cfg_nombre))

    ss.setdefault("carga_n", 0)  # sufijo de los cargadores de archivos: cambiarlo los vacía

    def limpiar_conciliacion() -> None:
        """Deja la pantalla lista para otra conciliación: sin archivos, sin resultado y sin configuración aplicada."""
        aplicar_config(None)
        for k in ("res_grupos", "res_excel", "cfg_actual", "archivos_actual", "aceptada", "memoria_id", "memoria_cfg",
                  "memoria_descartada", "auto_aplicada", "auto_firma", "memoria_confianza", "memoria_obj", "llaves_ia",
                  "recordados", "mem_cache", "t_prep", "ia_llamadas", "ia_tokens", "ia_ult", "ver_estado", "res_excel_hojas", "texto_res"):
            ss.pop(k, None)
        for k in [k for k in ss.keys() if str(k).startswith(("nfil_", "hoja_g", "ag_nt_", "ag_nu_", "nctl_"))]:
            del ss[k]
        ss.n_grupos = 2
        ss.carga_n += 1

    c_tit, c_limp = st.columns([4, 1])
    c_tit.subheader(_("1 · Armá los grupos de archivos"))
    hay_algo = any(ss.get(f"gfiles_{gi}_{ss.carga_n}") for gi in range(ss.get("n_grupos", 2))) or ss.get("res_grupos") is not None
    c_limp.write("")
    if c_limp.button(_("🧹 Nueva conciliación"), use_container_width=True, disabled=not hay_algo, key="limpiar_arriba",
                     help=_("Quita los archivos, el resultado y la configuración aplicada para empezar otra conciliación.")):
        limpiar_conciliacion()
        st.rerun()

    def leer_uno(f, lado: str, i: int, hoja_def: Optional[str] = None):
        if True:
            k = f"{lado}{i}"
            det = detectar_cacheado(firma_de(f), f.getvalue(), f.name, tuple(biblioteca), biblioteca)
            spec_sel, tipo, hoja = det.spec, det.tipo_registro, None
            nom = nombre_interno_cacheado(firma_de(f), f.getvalue(), f.name)  # el de adentro, si es un ZIP
            en_zip = _(" · dentro de {0}").format(f.name) if nom != f.name else ""
            if f.name.lower().endswith(".zip") and det.confianza == 0 and det.detalle.startswith("el ZIP"):
                st.error(_("No se pudo leer {0}: {1}").format(f.name, _(det.detalle)))
                return None
            if nom.lower().endswith((".xlsx", ".xls", ".xlsm")):
                hojas, por_defecto = hojas_cacheado(firma_de(f), f.getvalue(), f.name)
                if len(hojas) > 1:
                    hoja_ini = hoja_def if hoja_def in hojas else por_defecto
                    hoja = st.selectbox(_("Hoja"), hojas, index=hojas.index(hoja_ini), key=f"hoja_{k}_{V}",
                                        help=_("Se elige sola la hoja con más datos; la fila de encabezado también se detecta."))
                else:
                    st.caption(_("Tabla (Excel)") + en_zip)
            elif nom.lower().endswith(".csv"):
                st.caption(_("Tabla (CSV)") + en_zip)
            elif det.spec is not None and det.confianza >= 0.9:
                st.success(_('Formato reconocido: **{0}** · {1}').format(det.nombre_spec, _(det.detalle)))
                with st.expander(_("Cambiar formato o tipo de registro")):
                    nombres = list(biblioteca)
                    n = st.selectbox(_("Especificación"), nombres, index=nombres.index(det.nombre_spec), key=f"spec_{k}_{firma_de(f)}", format_func=_)
                    spec_sel = biblioteca[n]
                    tipos = [t.codigo for t in spec_sel.tipos_registro]
                    tipo = st.selectbox(_("Tipo de registro a cruzar"), tipos,
                                        index=tipos.index(tipo) if tipo in tipos else 0, key=f"tipo_{k}_{firma_de(f)}_{n}")
            else:
                st.warning(_("No reconozco el formato de este archivo. Subí su **manual técnico** y lo aprendo "
                           "(usa IA una sola vez; después queda guardado)."))
                man = st.file_uploader(_("Manual técnico (PDF, MD o TXT)"), type=["pdf", "md", "txt"], key=f"manual_{k}")
                if man is not None and st.button(_("Aprender el formato"), key=f"aprender_{k}", type="primary"):
                    if man.name.lower().endswith(".pdf"):
                        from regspec.texto_pdf import texto_de_pdf
                        texto = texto_de_pdf(man.getvalue())
                    else:
                        texto = man.getvalue().decode("utf-8", errors="replace")
                    with st.status(_("Leyendo el manual…"), expanded=True) as estado:
                        if proveedor is None:
                            d = extraer_base(texto)
                            ver = verificar(d, texto)
                            spec_nueva, valida = ver.spec, ver.valida
                        else:
                            prov_ext = con_cache(proveedor)
                            r = extraer(texto, prov_ext,
                                        ConfigExtraccion(max_iteraciones=max_it, modo=modo),
                                        al_paso=lambda p: estado.write(f"{'✔' if p.ok else '✖'} {p.nombre}"),
                                        al_iterar=lambda it: estado.write(
                                            _("verificación: OK") if it.errores == 0 else _("verificación: {0} errores → corrigiendo").format(it.errores)))
                            spec_nueva, valida = r.spec, r.valida
                            if r.error and not r.cupo_agotado:
                                st.error(r.error)
                            avisar_respaldo(prov_ext, r)
                        if spec_nueva is not None and valida:
                            nombre = guardar_en_biblioteca(spec_nueva, spec_nueva.nombre)
                            estado.update(label=_("Formato aprendido y guardado como '{0}'").format(nombre), state="complete")
                            st.rerun()
                        else:
                            estado.update(label=_("No se obtuvo una especificación válida"), state="error")
                            st.info(_("Podés revisarla y corregirla en la pestaña «Aprender un formato»."))
                return None
            try:
                df = tabla_cacheada(firma_de(f), f.getvalue(), f.name, spec_sel.model_dump_json() if spec_sel is not None else None, tipo, hoja)
            except Exception as e:  # noqa: BLE001
                st.error(_('No se pudo leer {0}: {1}').format(f.name, e))
                return None
            st.caption(_("{0} registros").format(f"{len(df):,}".replace(",", ".")))
            return df

    from regspec.grupos import (ACCIONES, MODOS, OPERADORES, OPERADORES_MULTIVALOR, OPERADORES_TEXTO, TRANSFORMACIONES,
                                ArchivoGrupo, Filtro, Grupo, cruzar_grupos, transformar)

    from regspec.grupos import (ACOMPANAN, AGREGACIONES, COLUMNAS_AUXILIARES, COMPARADORES, COMPARADORES_TEXTO,
                                CONDICIONES, FUNCIONES_COMPARACION, HOJAS_EXCEL, OPERACIONES, SALIDAS, Agregacion, Control,
                                Termino, Umbral)

    def editor_controles(cols: list, previos: list, k: str) -> list:
        """«Controles entre columnas» de un grupo: compara dos columnas de cada fila (p. ej. alícuota informada contra la
        de la tabla de referencia). Devuelve la lista de `Control`."""
        kn = f"nctl_{k}"
        ss.setdefault(kn, len(previos))
        with st.expander(_("✓ Controles entre columnas ({0})").format(ss[kn]), expanded=bool(ss[kn])):
            st.caption(_("Compara dos columnas de cada fila del grupo, por ejemplo la alícuota informada contra la que "
                         "corresponde según el padrón. Las filas que no coinciden van a la hoja «Controles» del Excel."))
            out = []
            for ci in range(ss[kn]):
                c0 = previos[ci] if ci < len(previos) else None
                c1, c2, c3 = st.columns([2, 2, 1])
                a = c1.selectbox(_("Columna"), cols, index=cols.index(c0.columna_a) if c0 and c0.columna_a in cols else 0,
                                 key=f"ctla_{k}_{ci}")
                b = c2.selectbox(_("tiene que coincidir con"), cols,
                                 index=cols.index(c0.columna_b) if c0 and c0.columna_b in cols else min(1, len(cols) - 1),
                                 key=f"ctlb_{k}_{ci}")
                tol = c3.number_input(_("Tolerancia"), value=float(c0.tolerancia) if c0 else 0.0, min_value=0.0, step=0.001,
                                      format="%.3f", key=f"ctlt_{k}_{ci}")
                out.append(Control(a, b, tol, c0.nombre if c0 else ""))
            b1, b2, _h = st.columns([1, 1, 2])
            if b1.button(_("➕ Agregar control"), key=f"ctlmas_{k}"):
                ss[kn] += 1
                st.rerun()
            if ss[kn] and b2.button(_("➖ Quitar el último"), key=f"ctlmenos_{k}"):
                ss[kn] -= 1
                st.rerun()
        return out

    def editor_agregacion(f, df, cols: list, llave: list, previa, k: str):
        """«Agrupar y filtrar por total»: fórmula por fila, agrupación y condición con umbral por categoría.
        Devuelve una `Agregacion` o None si está desactivada."""
        activo = st.toggle(_("Agrupar y filtrar por total"), value=previa is not None, key=f"ag_on_{k}",
                           help=_("Calcula una columna con una fórmula, la agrupa (por ejemplo, por documento y mes) y "
                                  "excluye los grupos cuyo total no cumple un umbral, que puede depender de una columna."))
        if not activo:
            return None
        with st.container(border=True):
            # 1. fórmula
            st.markdown(_("**Columna calculada (por fila)**"))
            nombre_calc = st.text_input(
                _("Nombre de la columna calculada"), value=previa.nombre if previa else "", key=f"ag_nom_{k}",
                placeholder=_("por ejemplo: débito + crédito"),
                help=_("Con un nombre, las columnas nuevas se llaman «<nombre>» y «total <nombre>», y así aparecen al elegir "
                       "qué comparar y en el Excel. Vacío: «valor calculado» y «total del grupo»."))
            kn = f"ag_nt_{k}"
            ss.setdefault(kn, max(1, len(previa.terminos) if previa else 2))
            terminos = []
            ops = list(OPERACIONES)
            for ti in range(ss[kn]):
                t0 = previa.terminos[ti] if previa and ti < len(previa.terminos) else None
                c_op, c_col, c_abs = st.columns([1, 2, 1])
                op = "+"
                if ti > 0:
                    op = c_op.selectbox(_("Operación"), ops, index=ops.index(t0.operacion) if t0 and t0.operacion in ops else 0,
                                        format_func=lambda o: _(OPERACIONES[o]), key=f"ag_op_{k}_{ti}")
                else:
                    c_op.caption(_("Primera columna"))
                col_def = t0.columna if t0 and t0.columna in cols else cols[min(ti, len(cols) - 1)]
                col = c_col.selectbox(_("Columna"), cols, index=cols.index(col_def), key=f"ag_col_{k}_{ti}")
                absv = c_abs.checkbox(_("en valor absoluto"), value=bool(t0.absoluto) if t0 else False, key=f"ag_abs_{k}_{ti}")
                terminos.append(Termino(col, op, absv))
            b1, b2, _h = st.columns([1, 1, 2])
            if b1.button(_("➕ Otra columna"), key=f"ag_mas_{k}"):
                ss[kn] += 1
                st.rerun()
            if ss[kn] > 1 and b2.button(_("➖ Quitar la última"), key=f"ag_menos_{k}"):
                ss[kn] -= 1
                st.rerun()
            # 2. agrupación
            st.markdown(_("**Agrupar**"))
            g1, g2 = st.columns([2, 1])
            por_def = [c for c in (previa.agrupar_por if previa else llave) if c in cols]
            agrupar = g1.multiselect(_("Agrupar por"), cols, default=por_def, placeholder=_("Elegí una o varias columnas"),
                                     key=f"ag_por_{k}")
            funcs = list(AGREGACIONES)
            funcion = g2.selectbox(_("Función"), funcs, index=funcs.index(previa.funcion) if previa and previa.funcion in funcs else 0,
                                   format_func=lambda x: _(AGREGACIONES[x]), key=f"ag_fn_{k}")
            # 3. condición
            st.markdown(_("**Condición sobre el total del grupo**"))
            conds = list(CONDICIONES)
            condicion = st.selectbox(_("Condición"), conds, index=conds.index(previa.condicion) if previa and previa.condicion in conds else 2,
                                     format_func=lambda x: _(CONDICIONES[x]), key=f"ag_cond_{k}", label_visibility="collapsed")
            categoria, umbrales, otros = None, [], "excluir"
            if condicion == "unica":
                u0 = previa.umbrales[0] if previa and previa.umbrales else None
                c1, c2 = st.columns(2)
                comp = c1.selectbox(_("El total tiene que ser"), COMPARADORES, format_func=lambda c: _(COMPARADORES_TEXTO[c]),
                                    index=COMPARADORES.index(u0.comparador) if u0 and u0.comparador in COMPARADORES else 0, key=f"ag_uc_{k}")
                val = c2.number_input(_("Umbral"), value=float(u0.umbral) if u0 else 0.0, step=100.0, format="%.2f", key=f"ag_uv_{k}")
                umbrales = [Umbral(comp, val)]
            elif condicion == "categoria":
                cat_def = previa.categoria if previa and previa.categoria in cols else cols[0]
                categoria = st.selectbox(_("Columna que define el umbral (por ejemplo, el tipo de documento)"), cols,
                                         index=cols.index(cat_def), key=f"ag_cat_{k}")
                distintos, _n = valores_distintos(firma_de(f), categoria, df[categoria])
                ku = f"ag_nu_{k}"
                ss.setdefault(ku, max(1, len(previa.umbrales) if previa else 2))
                for ui in range(ss[ku]):
                    u0 = previa.umbrales[ui] if previa and ui < len(previa.umbrales) else None
                    c1, c2, c3 = st.columns([2, 1, 1])
                    if distintos:
                        vals = c1.multiselect(_("Si la columna vale"), distintos, default=[v for v in (u0.valores if u0 else []) if v in distintos],
                                              placeholder=_("Elegí uno o varios valores"), key=f"ag_uvals_{k}_{ui}")
                    else:
                        texto = c1.text_input(_("Si la columna vale (separá con «;»)"), "; ".join(u0.valores) if u0 else "",
                                              key=f"ag_uvalt_{k}_{ui}")
                        vals = [v.strip() for v in texto.split(";") if v.strip()]
                    comp = c2.selectbox(_("el total tiene que ser"), COMPARADORES, format_func=lambda c: _(COMPARADORES_TEXTO[c]),
                                        index=COMPARADORES.index(u0.comparador) if u0 and u0.comparador in COMPARADORES else 0,
                                        key=f"ag_ucomp_{k}_{ui}")
                    val = c3.number_input(_("Umbral"), value=float(u0.umbral) if u0 else 0.0, step=100.0, format="%.2f",
                                          key=f"ag_uval_{k}_{ui}")
                    if vals:
                        umbrales.append(Umbral(comp, val, vals))
                    elif val:
                        c1.caption(_("Elegí al menos un valor: sin valores, esta línea no se aplica."))
                b1, b2, _h = st.columns([1, 1, 2])
                if b1.button(_("➕ Otro umbral"), key=f"ag_umas_{k}"):
                    ss[ku] += 1
                    st.rerun()
                if ss[ku] > 1 and b2.button(_("➖ Quitar el último"), key=f"ag_umenos_{k}"):
                    ss[ku] -= 1
                    st.rerun()
                ots = ["excluir", "sin_umbral"]
                otros = st.radio(_("Filas con otros valores"), ots, horizontal=True,
                                 index=ots.index(previa.otros) if previa and previa.otros in ots else 0,
                                 format_func=lambda x: _("se excluyen") if x == "excluir" else _("pasan sin umbral"), key=f"ag_otros_{k}")
            # 4. salida
            st.markdown(_("**Resultado de la agrupación**"))
            sals = list(SALIDAS)
            salida = st.radio(_("Resultado"), sals, index=sals.index(previa.salida) if previa and previa.salida in sals else 0,
                              format_func=lambda x: _(SALIDAS[x]), key=f"ag_sal_{k}", label_visibility="collapsed")
            acompanan = "suma"
            if salida == "resumir":
                acs = list(ACOMPANAN)
                acompanan = st.selectbox(_("Las demás columnas se llevan con"), acs,
                                         index=acs.index(previa.acompanan) if previa and previa.acompanan in acs else 0,
                                         format_func=lambda x: _(ACOMPANAN[x]), key=f"ag_acomp_{k}")
                fuera = [c for c in llave if c not in agrupar]
                if fuera:
                    st.warning(_("La llave del cruce ({0}) no está entre las columnas del grupo: después de resumir, "
                                 "cada grupo lleva un solo valor de esa columna. Agregala a «Agrupar por» o cambiá la llave.")
                               .format(", ".join(fuera)))
            mostrar = st.multiselect(_("Columnas que se agregan al resultado"), COLUMNAS_AUXILIARES,
                                     default=[c for c in (previa.mostrar if previa else COLUMNAS_AUXILIARES) if c in COLUMNAS_AUXILIARES],
                                     format_func=lambda c, n=nombre_calc.strip(): Agregacion(nombre=n).etiqueta(c) if n else _(c),
                                     key=f"ag_most_{k}_{nombre_calc.strip()}",  # con el nombre: así se ven las etiquetas nuevas
                                     help=_("Se ven en el detalle de cada grupo y en el Excel; también se pueden elegir como importe a comparar."))
            ag = Agregacion(terminos, agrupar, funcion, condicion, categoria, umbrales, otros, salida, acompanan, mostrar,
                            nombre=nombre_calc.strip())
            st.caption(_("Fórmula: {0}").format(ag.formula()) +
                       (" · " + " · ".join(f"{', '.join(u.valores) or _('todas')}: {u.texto()}" for u in umbrales) if umbrales else ""))
            if condicion == "categoria" and not umbrales:
                st.warning(_("Elegí al menos un valor y su umbral."))
            else:
                aviso_sin_umbral(ag, df)
        return ag

    def aviso_sin_umbral(ag, df):
        """Umbral por categoría: avisa qué valores no tienen umbral (sus filas se excluyen), con la cantidad de filas."""
        if ag is None or ag.condicion != "categoria" or ag.categoria not in df.columns or ag.otros != "excluir" or not ag.umbrales:
            return
        sin = ~ag._umbral_por_fila(df)[3]
        if sin.any():
            vc = df.loc[sin, ag.categoria].astype("string").fillna("").str.strip().value_counts()
            vals_txt = ", ".join(f"«{v or _('(vacío)')}» ({n:,})".replace(",", ".") for v, n in vc.head(5).items())
            aviso = (_("Ningún valor de «{0}» tiene umbral: se excluyen TODAS las filas del archivo ({1}).")
                     if sin.all() else _("Valores de «{0}» sin umbral, sus filas se excluyen: {1}."))
            (st.error if sin.all() else st.warning)(aviso.format(ag.categoria, vals_txt))

    from regspec.grupos import sugerir_llaves
    from regspec.llave_ml import ejemplos_de_cruce, modelo_usuario, sugerir_llaves_modelo

    def modelo_llave():
        """Modelo de llave del usuario: el base, reentrenado con los cruces que confirmó (aprendizaje por uso)."""
        if "modelo_llave" not in ss:
            try:
                ss.modelo_llave = modelo_usuario(almacen.uso_llave())
            except Exception:  # noqa: BLE001
                ss.modelo_llave = None
        return ss.modelo_llave

    @st.cache_resource(show_spinner="Buscando la llave que vincula los archivos…", max_entries=8)
    def llaves_sugeridas(firma: tuple, usuario: str, version_modelo: int, _tablas: list, _modelo):
        """Sugerencia conjunta de llaves (sin IA): el modelo aprendido elige el par de columnas; si no hay modelo,
        la heurística (la columna del primer archivo que mejor vincula a todos)."""
        if _modelo is not None:
            try:
                return sugerir_llaves_modelo(_tablas, _modelo)
            except Exception:  # noqa: BLE001
                pass
        return sugerir_llaves(_tablas)

    def tabla_silenciosa(f):
        """Lectura sin interfaz (para la sugerencia de llaves); None si el formato no se reconoce."""
        try:
            det = detectar_cacheado(firma_de(f), f.getvalue(), f.name, tuple(biblioteca), biblioteca)
            nom = nombre_interno_cacheado(firma_de(f), f.getvalue(), f.name)
            if not nom.lower().endswith((".csv", ".xlsx", ".xls", ".xlsm")) and (det.spec is None or det.confianza < 0.9):
                return None
            return tabla_cacheada(firma_de(f), f.getvalue(), f.name, det.spec.model_dump_json() if det.spec is not None else None,
                                  det.tipo_registro, None)
        except Exception:  # noqa: BLE001
            return None

    ss.setdefault("n_grupos", 2)
    st.caption(_("Cada **grupo** reúne uno o varios archivos. En cada archivo definís su **llave** (y cómo normalizarla); "
               "el cruce se hace sobre esa llave entre todos los grupos."))
    grupos_ui = []
    hojas_sel: dict = {}
    # sugerencia conjunta de llaves con todos los archivos ya subidos (se recalcula solo si cambian los archivos)
    subidos = [(gi, f) for gi in range(ss.n_grupos) for f in (ss.get(f"gfiles_{gi}_{ss.carga_n}") or [])]
    # tiempo de preparación (métrica): desde que se sube el primer archivo del conjunto hasta «Cruzar»
    if not subidos:
        ss.t_prep = None
    elif ss.get("t_prep") is None:
        ss.t_prep = time.time()
    leidas = [(gi, f, tabla_silenciosa(f)) for gi, f in subidos]
    leidas = [x for x in leidas if x[2] is not None]
    sugeridas = {}
    if leidas:
        firma = tuple((gi, f.name, f.size) for gi, f, _ in leidas)
        m_llave = modelo_llave()
        lista_sug = llaves_sugeridas(firma, usuario_actual(), m_llave.n_usuario if m_llave is not None else -1,
                                     [d for _, _, d in leidas], m_llave)
        for (gi, f, _tabla), s in zip(leidas, lista_sug):
            if s is not None:
                sugeridas[(gi, f.name)] = ([s[0]], s[1], s[2])
        # respaldo con IA (validado): solo si el código no encontró una llave clara entre archivos de 2 o más grupos
        from regspec.grupos import llaves_claras, sugerir_llaves_ia
        ia_res = ss.setdefault("llaves_ia", {}).get(firma)
        if ia_res is not None:
            for (gi, f, _tabla), s in zip(leidas, ia_res["llaves"]):
                if s is not None:
                    sugeridas[(gi, f.name)] = (s[0], s[1], s[2], "ia")
        elif cfg is None and len({gi for gi, _f, _d in leidas}) >= 2:
            clara = llaves_claras(lista_sug)
            caja = st.expander(_("🤖 Comparar la llave del código con la de la IA"), expanded=False) if clara else st.container(border=True)
            with caja:
                if clara:
                    st.caption(_("El código ya encontró una llave clara. Si querés, podés pedirle igual una propuesta a la IA para "
                                 "compararlas: se valida con el mismo criterio y, si la acepta, reemplaza a la del código."))
                else:
                    st.markdown(_("🔎 **El código no encontró una llave clara** que vincule todos los archivos."))
                st.caption(_("Podés elegirla a mano o pedirle una propuesta a la IA: se le envían solo los nombres de las columnas "
                             "y 5 valores de ejemplo de cada una, y la propuesta se valida con código (las columnas tienen que existir "
                             "y sus valores tienen que coincidir entre archivos)."))
                if st.button(_("🤖 Pedir la llave a la IA"), key=f"ia_llave_{len(leidas)}", disabled=proveedor is None):
                    with st.spinner(_("Consultando a la IA…")):
                        try:
                            r_ia = sugerir_llaves_ia([d for _, _, d in leidas], proveedor, [f.name for _, f, _ in leidas],
                                                     grupos=[gi for gi, _f, _d in leidas])
                        except Exception as e:  # noqa: BLE001
                            r_ia = None
                            st.error(_("La IA no pudo proponer una llave: {0}").format(e))
                        ss.ia_llamadas = ss.get("ia_llamadas", 0) + 1
                    if r_ia is not None:
                        ss.ia_tokens = ss.get("ia_tokens", 0) + r_ia["tokens"]
                        ss.llaves_ia[firma] = r_ia
                        st.rerun()
        if ia_res is not None:
            prop = ia_res.get("propuesta") or []
            nombres_l = [f.name for _g, f, _d in leidas]
            final_txt = " · ".join(f"{n}: {' + '.join(x[0])} ({x[2]:.0%})" for n, x in zip(nombres_l, ia_res["llaves"]) if x is not None)
            prop_txt = " + ".join(prop[0]) if prop and prop[0] else _("(ninguna columna válida)")
            intentos_txt = _(" Necesitó {0} intentos: el primero no pasó la validación y se le devolvió el resultado.").format(
                ia_res["intentos"]) if ia_res.get("intentos", 1) > 1 else ""
            if ia_res["validada"] and ia_res.get("reducida"):
                st.success(_("🤖 La IA propuso {0}. El código la validó con los datos y la simplificó a la menor combinación de "
                             "esas columnas que coincide entre los grupos: {1}.").format(prop_txt, final_txt)
                           + intentos_txt + " " + _("Revisala abajo. Motivo de la IA: {0}").format(ia_res["motivo"]))
            elif ia_res["validada"]:
                st.success(_("🤖 La IA propuso la llave y la validación la confirmó: {0} (coincidencia × unicidad).").format(final_txt)
                           + intentos_txt + " " + _("Revisala abajo. Motivo de la IA: {0}").format(ia_res["motivo"]))
            else:
                det = ia_res.get("detalle") or []
                det_txt = " · ".join(f"{n}: {(x or 0):.0%}" for n, x in list(zip(nombres_l, det))[1:])
                st.warning(_("🤖 La IA propuso {0} y no pasó la validación ({1}), ni completa ni con menos columnas. "
                             "Se mantiene la sugerencia del modelo de llave.").format(prop_txt, det_txt or _("sin llave por archivo"))
                           + intentos_txt)

    # describir el cruce con texto: el LLM propone la configuración, la validación la corrige y se carga en el formulario
    if leidas and len({gi for gi, _f, _d in leidas}) >= 2:
        from regspec.instrucciones import configurar_desde_texto
        res_txt = ss.get("texto_res")
        with st.expander(_("✍️ Describir el cruce con texto (asistido por IA)"), expanded=res_txt is not None):
            st.caption(_("Escribí qué querés cruzar con tus palabras, por ejemplo: «cruzá por CUIT, quedate solo con las leyendas "
                         "SIRTAC y 680/2020, compará el monto retenido con tolerancia de 1 centavo». La IA propone la "
                         "configuración, el código la valida contra los datos y se carga abajo para que la revises antes de cruzar. "
                         "A la IA se le envían los nombres de las columnas y unos pocos valores de ejemplo, no los archivos."))
            texto_u = st.text_area(_("Instrucción"), key=f"texto_cruce_{ss.carga_n}", height=90,
                                   placeholder=_("¿Qué querés cruzar y cómo?"))
            if st.button(_("Proponer la configuración"), key=f"texto_btn_{ss.carga_n}", disabled=proveedor is None or not texto_u.strip(),
                         help=_("Requiere la clave de la IA (Configuración avanzada).") if proveedor is None else None):
                cont = {}
                archivos_txt = []
                for gi, f, d in leidas:
                    cont[gi] = cont.get(gi, 0) + 1
                    archivos_txt.append((f"G{gi + 1}A{cont[gi]}", gi, d))
                with st.spinner(_("Consultando a la IA…")):
                    try:
                        res_txt = configurar_desde_texto(texto_u, archivos_txt, proveedor)
                    except Exception as e:  # noqa: BLE001
                        res_txt = None
                        st.error(_("La IA no pudo proponer una configuración: {0}").format(e))
                    ss.ia_llamadas = ss.get("ia_llamadas", 0) + 1
                if res_txt is not None:
                    ss.ia_tokens = ss.get("ia_tokens", 0) + int(res_txt.get("tokens", 0))
                    ss.texto_res = {k: v for k, v in res_txt.items() if k in ("avisos", "valida", "inventadas", "corregidas")}
                    if res_txt["config"] is not None:
                        from types import SimpleNamespace as _NS
                        for g_ in res_txt["config"].grupos:  # el nombre visible de cada archivo
                            for a_ in g_.archivos:
                                gi_, n_ = int(a_.nombre_original[1:a_.nombre_original.index("A")]) - 1, int(a_.nombre_original.split("A")[-1])
                                fs = [f for g2, f, _d in leidas if g2 == gi_]
                                a_.nombre_original = fs[n_ - 1].name if n_ - 1 < len(fs) else a_.nombre_original
                        aplicar_config(_NS(config=res_txt["config"], nombre=_("configuración desde texto")), "texto")
                    st.rerun()
            if res_txt is not None:
                if res_txt["valida"]:
                    st.success(_("Configuración cargada abajo: revisala antes de cruzar (llaves, filtros, importes y tolerancia)."))
                else:
                    st.warning(_("La IA no propuso una llave válida para todos los archivos: no se cargó la configuración."))
                for a_ in res_txt["avisos"]:
                    st.caption("• " + a_)
                if res_txt.get("inventadas") or res_txt.get("corregidas"):
                    st.caption(_("Validación: {0} referencias inexistentes descartadas, {1} correcciones aplicadas.").format(
                        res_txt.get("inventadas", 0), res_txt.get("corregidas", 0)))

    # memoria de configuración: si ya se cruzaron archivos con esta misma estructura, se ofrece lo aprendido.
    # Se aplica sola solo si la confianza supera el 95 % y este usuario lo aceptó explícitamente.
    from regspec.almacen import UMBRAL_AUTO, modo_memoria
    from types import SimpleNamespace
    grupos_tablas = [[list(d.columns) for g_, _f, d in leidas if g_ == gi] for gi in range(ss.n_grupos)]
    if ss.get("origen") == "memoria_auto" and cfg is not None:
        with st.container(border=True):
            st.markdown(_("🧠 **Se aplicó automáticamente la configuración aprendida** (confianza {0}%, mayor al {1}%, "
                          "y aceptaste que se aplique sola). Revisala antes de cruzar.").format(ss.get("memoria_confianza"), UMBRAL_AUTO))
            b_man, b_off, _h = st.columns([1, 1.6, 1.4])
            if b_man.button(_("Configurar a mano"), key="auto_manual"):
                ss.memoria_descartada = ss.get("auto_firma")
                aplicar_config(None)
                st.rerun()
            if b_off.button(_("No aplicarla más automáticamente"), key="auto_off"):
                try:
                    almacen.fijar_auto(ss.memoria_obj, False)
                except Exception as e:  # noqa: BLE001
                    st.warning(str(e))
                ss.get("mem_cache", {}).pop(ss.get("auto_firma"), None)
                ss.origen = "memoria"  # sigue aplicada, pero ya no se aplicará sola
                st.rerun()
    if cfg is None and all(grupos_tablas):
        firma_mem = firma_archivos(grupos_tablas)
        mc = ss.setdefault("mem_cache", {})
        if firma_mem not in mc:
            try:
                m_ = almacen.memoria(firma_mem)
                mc[firma_mem] = (m_, almacen.acepta_auto(m_) if m_ is not None else False)
            except Exception:  # noqa: BLE001
                mc[firma_mem] = (None, False)
        mem, acepto = mc[firma_mem]
        recien = {f for f, _c in ss.get("recordados", set())}  # lo que se acaba de cruzar en esta sesión no se ofrece
        modo_mem = modo_memoria(mem, acepto)
        if modo_mem != "nada" and ss.get("memoria_descartada") != firma_mem and firma_mem not in recien:
            def _usar_memoria(origen_m):
                aplicar_config(SimpleNamespace(config=mem.config, nombre=_("configuración aprendida")), origen_m, mem.id or None)
                ss.memoria_confianza, ss.memoria_obj, ss.auto_firma = mem.confianza, mem, firma_mem
            if modo_mem == "automatica" and ss.get("auto_aplicada") != firma_mem:
                ss.auto_aplicada = firma_mem  # una vez por sesión: si la descarta, no se vuelve a aplicar sola
                _usar_memoria("memoria_auto")
                st.rerun()
            with st.container(border=True):
                _veces = (_("1 vez") if mem.veces_usado == 1 else _("{0} veces").format(mem.veces_usado))
                st.markdown(_("🧠 **Ya cruzaste archivos con esta misma estructura** ({0}, confianza {1}%).").format(
                    _veces, mem.confianza))
                if mem.descripcion:
                    st.caption(mem.descripcion)
                auto_ok = False
                if modo_mem == "ofrecer_auto":
                    auto_ok = st.checkbox(_("Aplicarla automáticamente de ahora en más (la confianza supera el {0}%)").format(UMBRAL_AUTO),
                                          key=f"mem_auto_{firma_mem}",
                                          help=_("Solo para tu usuario. Si la corregís, la confianza baja y vuelve a preguntarte."))
                else:
                    st.caption(_("Con más usos sin cambios, cuando la confianza supere el {0}%, vas a poder aceptar que se aplique sola.").format(UMBRAL_AUTO))
                b_ap, b_no, _h = st.columns([1.3, 1, 2])
                if b_ap.button(_("Usar la configuración aprendida"), type="primary", key="mem_aplicar"):
                    if auto_ok:
                        try:
                            almacen.fijar_auto(mem, True)
                            mc[firma_mem] = (mem, True)
                        except Exception as e:  # noqa: BLE001
                            st.warning(str(e))
                    _usar_memoria("memoria")
                    st.rerun()
                if b_no.button(_("Configurar a mano"), key="mem_no"):
                    ss.memoria_descartada = firma_mem
                    st.rerun()

    controles_g: dict = {}
    for gi in range(ss.n_grupos):
        with st.container(border=True):
            c_nom, c_modo = st.columns([1, 2])
            gcfg = cfg.grupos[gi] if cfg is not None and gi < len(cfg.grupos) else None
            nombre_g = c_nom.text_input(_("Nombre del grupo"), gcfg.nombre if gcfg else _("Grupo {0}").format(gi + 1), key=f"gnom_{gi}_{V}_{ss.idioma}")
            archivos = st.file_uploader(_('Archivos de «{0}» (uno o varios)').format(nombre_g), type=["csv", "xlsx", "xls", "txt", "xml", "dat", "zip"],
                                        accept_multiple_files=True, key=f"gfiles_{gi}_{ss.carga_n}")
            # por defecto: si los archivos del grupo tienen columnas distintas (p. ej. retenciones + padrón), base + referencia
            tablas_g = [d for g_, _f, d in leidas if g_ == gi]
            modo_def = "concatenar"
            if len(tablas_g) > 1 and any(columnas_distintas(tablas_g[0], d) for d in tablas_g[1:]):
                modo_def = "base_referencia"
            if gcfg and gcfg.modo in MODOS:
                modo_def = gcfg.modo
            modo_g = c_modo.selectbox(_("Cómo combinar los archivos del grupo"), list(MODOS), format_func=lambda k: _(MODOS[k]),
                                      index=list(MODOS).index(modo_def), key=f"gmodo_{gi}_{V}_{modo_def}",
                                      disabled=not archivos or len(archivos) < 2)
            if not archivos:
                grupos_ui.append(None)
                continue
            arch_objs, completo = [], True
            ag_primera = None  # agrupación del primer archivo (los demás pueden usar la misma)
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
                        faltan = [c for c in acfg.llave + acfg.traer + [x.columna for x in acfg.filtros] + acfg.buscar_por +
                                  (acfg.agregacion.a_agregacion().columnas() if acfg.agregacion else []) if c not in cols]
                        if faltan:
                            st.warning(_("Columnas de la configuración que no están en este archivo: {0}").format(", ".join(faltan)))
                        sug = [c for c in acfg.llave if c in cols]
                        tr_def = acfg.transformacion if acfg.transformacion in TRANSFORMACIONES else "exacta"
                        k_sug = "cfg"
                    else:
                        sug = [c for c in s[0] if c in cols] if s else []
                        tr_def = s[1] if s else "exacta"
                        k_sug = f"{'_'.join(map(str, sug))}_{len(leidas)}{'_ia' if s and len(s) > 3 else ''}"
                    c1, c2 = st.columns([2, 1])
                    llave = c1.multiselect(_("Llave (una o varias columnas)"), cols, default=sug, placeholder=_("Elegí una o varias columnas"), key=f"llave_{gi}_{fi}_{V}_{k_sug}",
                                           help=_("Sugerida por la IA y validada con código.") if s and len(s) > 3 else
                                           _("Sugerida por el modelo de llave, que aprende de los cruces que confirmás.")
                                           if modelo_llave() is not None else
                                           _("Sugerida por código: la columna cuyos valores coinciden con los de los otros archivos."))
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
                            distintos, n_distintos = valores_distintos(firma_de(f), fcol, df[fcol])
                            previos_v = (f0.lista() if hasattr(f0, "lista") else (f0.valores or ([f0.valor] if f0.valor else []))) if f0 else []
                            if fop != "contiene" and distintos:
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
                        if filtros[-1].sin_valores():
                            st.caption(_("Esta condición no tiene valores: no se aplica."))
                    if st.button(_("➕ Otra condición"), key=f"masfil_{gi}_{fi}_{V}"):
                        ss[kn] += 1
                        st.rerun()
                    if fi > 0 and modo_g == "concatenar" and ag_primera is not None and st.checkbox(
                            _("Usar la misma agrupación que «{0}»").format(archivos[0].name), value=True, key=f"ag_igual_{gi}_{fi}_{V}",
                            help=_("Los archivos se suman fila a fila: lo normal es que lleven la misma fórmula, nombre y umbrales. "
                                   "Destildá para configurar este archivo aparte.")):
                        agregacion = copy.deepcopy(ag_primera)
                        st.caption(_("Agrupación: {0}").format(agregacion.formula()) +
                                   (f" · «{agregacion.nombre}»" if agregacion.nombre else ""))
                        faltan_ag = [c for c in agregacion.columnas() if c not in cols]
                        if faltan_ag:
                            st.error(_("Este archivo no tiene las columnas de la agrupación: {0}").format(", ".join(faltan_ag)))
                        else:
                            aviso_sin_umbral(agregacion, df)
                    else:
                        agregacion = editor_agregacion(f, df, cols, llave, acfg.agregacion if acfg is not None else None,
                                                       f"{gi}_{fi}_{V}")
                    if fi == 0:
                        ag_primera = agregacion
                    traer, buscar_por = [], []
                    if modo_g == "base_referencia" and fi > 0:
                        opciones_t = [c for c in cols if c not in llave]
                        traer = st.multiselect(_("Columnas a traer a la base"), opciones_t, placeholder=_("Elegí una o varias columnas"),
                                               default=[c for c in (acfg.traer if acfg else []) if c in opciones_t], key=f"traer_{gi}_{fi}_{V}")
                        if arch_objs:  # columnas de la base y las que traen las demás referencias (en cualquier orden)
                            disp = [c for c in arch_objs[0].df.columns if not str(c).startswith("_")]
                            for kk in range(1, len(archivos)):
                                if kk != fi:
                                    disp += [c for c in ss.get(f"traer_{gi}_{kk}_{V}", []) if c not in disp]
                            buscar_por = st.multiselect(
                                _("Buscar por (columnas de la base)"), disp, placeholder=_("La llave de la base"),
                                default=[c for c in (acfg.buscar_por if acfg else []) if c in disp], key=f"buscar_{gi}_{fi}_{V}",
                                help=_("Vacío: se busca por la llave de la base. Elegí otra columna para encadenar búsquedas, "
                                       "por ejemplo la letra que trajo el padrón contra una tabla de equivalencias."))
                            if buscar_por and len(buscar_por) != len(llave):
                                st.warning(_("«Buscar por» tiene que tener tantas columnas como la llave de este archivo ({0}).").format(len(llave)))
                    if llave:
                        arch_objs.append(ArchivoGrupo(f.name, df, llave, tr, filtros, traer, agregacion, buscar_por))
                        hojas_sel[(gi, len(arch_objs) - 1)] = ss.get(f"hoja_g{gi}{fi}_{V}")
                    else:
                        completo = False
            if not completo or not arch_objs:
                grupos_ui.append(None)
                continue
            cols_g = sorted({c for a in arch_objs for c in a.columnas_resultado()} |
                            {c for a in arch_objs for c in a.traer})
            if modo_g == "concatenar" and len(arch_objs) > 1:
                ags = [a.agregacion for a in arch_objs]
                if any(x is not None for x in ags):
                    nombres_ag = {(x.etiqueta("total del grupo") if x is not None else None) for x in ags}
                    if len(nombres_ag) > 1:
                        st.error(_("Los archivos de «{0}» no tienen la misma agrupación (o la columna calculada tiene nombres "
                                   "distintos: {1}). Al concatenar, las filas de cada archivo quedan en columnas distintas y al "
                                   "comparar aparecen vacías. Usá la misma agrupación en todos.").format(
                            nombre_g, ", ".join(f"«{n}»" if n else _("sin agrupación") for n in nombres_ag)))
                if any(columnas_distintas(arch_objs[0].df, a.df) for a in arch_objs[1:]):
                    st.warning(_("Los archivos de «{0}» tienen columnas distintas y se van a concatenar (sumar sus filas). "
                                 "Si uno es un padrón o tabla de referencia, elegí «Base + referencia» en «Cómo combinar los archivos del grupo».").format(nombre_g))
            controles_g[gi] = editor_controles(cols_g, gcfg.controles if gcfg else [], f"{gi}_{V}")
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
        comparaciones, funciones_cmp = [], []
        # totales de una agregación (con su nombre, si se le puso uno): se comparan como valor único por llave
        totales_ag = {a.agregacion.etiqueta("total del grupo") for _n, arch, _m, _c in listos for a in arch
                      if getattr(a, "agregacion", None) is not None}
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
            fn_prev = (cfg.funciones_comparacion[k] if cfg is not None and k < len(cfg.funciones_comparacion) else
                       "unico" if (re.search(r"al[ií]cuota|tarifa|tasa|rate|porcentaje|%|total del grupo|umbral aplicado",
                                             " ".join(fila), re.I) or any(c in totales_ag for c in fila))
                       else "suma")
            funciones_cmp.append(st.radio(_("Cómo se agrupa por llave"), list(FUNCIONES_COMPARACION), horizontal=True,
                                          index=list(FUNCIONES_COMPARACION).index(fn_prev) if fn_prev in FUNCIONES_COMPARACION else 0,
                                          format_func=lambda x: _(FUNCIONES_COMPARACION[x]), key=f"cmpfn_{k}_{V}_{'|'.join(fila)}",
                                          help=_("Los importes se suman por llave. Una alícuota o una tasa no se suma: se compara su valor, "
                                                 "y si una llave tiene varios valores distintos se marca como diferencia.")))
        tol = st.number_input(_("Tolerancia en importes"), value=float(cfg.tolerancia) if cfg is not None else 0.01, min_value=0.0,
                              step=0.01, format="%.2f", key=f"tol_g_{V}")

        hojas_def = [h for h in (cfg.hojas_excel if cfg is not None and cfg.hojas_excel else HOJAS_EXCEL) if h in HOJAS_EXCEL]
        hojas_excel = st.multiselect(_("Hojas del Excel"), list(HOJAS_EXCEL), default=hojas_def, format_func=lambda h: _(HOJAS_EXCEL[h]),
                                     key=f"hojas_xl_{V}", help=_("Se guardan con el cruce. Si no elegís ninguna, el Excel trae solo el resumen."))

        st.subheader(_("3 · Resultado"))
        if st.button(_("Cruzar"), type="primary"):
            grupos = [Grupo(nom, arch, modo, [], controles_g.get(gi, [])) for gi, (nom, arch, modo, _) in enumerate(listos)]
            seg_prep = round(time.time() - ss.t_prep, 1) if ss.get("t_prep") else None
            t0 = time.time()
            with st.spinner(_("Cruzando…")):
                ss.res_grupos = cruzar_grupos(grupos, comparaciones, Decimal(str(tol)), funciones_cmp)
                seg_cruce = round(time.time() - t0, 2)
                rg = ss.res_grupos
                grande = max([len(rg.matriz), *[len(d) for d in rg.detalles.values()]]) > GRANDE
                ss.res_excel = None if grande else rg.a_excel(hojas_sel=hojas_excel)  # los resultados grandes se exportan a pedido
                ss.res_excel_hojas = list(hojas_excel)
            ss.cfg_actual = ConfigCruce(
                grupos=[GrupoCfg(nombre=nom, modo=modo, archivos=[
                    ArchivoCfg(nombre_original=a.nombre, llave=a.llave, transformacion=a.transformacion,
                               filtros=[FiltroCfg(columna=x.columna, operador=x.operador, valor=x.valor, accion=x.accion,
                                                  valores=x.valores) for x in a.filtros],
                               traer=a.traer, hoja=hojas_sel.get((gi, ai)),
                               agregacion=AgregacionCfg.desde(a.agregacion) if a.agregacion is not None else None,
                               buscar_por=a.buscar_por)
                    for ai, a in enumerate(arch)], controles=[ControlCfg(**vars(c)) for c in controles_g.get(gi, [])])
                    for gi, (nom, arch, modo, _) in enumerate(listos)],
                comparaciones=[list(c) for c in comparaciones], funciones_comparacion=list(funciones_cmp),
                tolerancia=str(tol), hojas_excel=hojas_excel)
            ss.archivos_actual = [a.nombre for _, arch, _, _ in listos for a in arch]
            # aprendizaje por uso: se recuerda la configuración para esta estructura de archivos (una vez por sesión)
            try:
                firma_real = firma_archivos([[list(a.df.columns) for a in arch] for _n, arch, _m, _c in listos])
                clave_mem = (firma_real, ss.cfg_actual.model_dump_json())
                if clave_mem not in ss.setdefault("recordados", set()):
                    de_memoria = str(ss.get("origen") or "").startswith("memoria")
                    corregida = (de_memoria and ss.get("memoria_cfg") is not None
                                 and not config_equivalente(ss.cfg_actual, ss.memoria_cfg))
                    # llave de la IA: ¿quedó la que propuso (validada)?
                    ia_res = ss.get("llaves_ia", {}).get(tuple((gi, f.name, f.size) for gi, f, _ in leidas)) if leidas else None
                    ia_ok = None
                    if ia_res is not None:
                        prop = {(gi, f.name): s for (gi, f, _d), s in zip(leidas, ia_res["llaves"]) if s is not None}
                        usadas = [(gi, a) for gi, (_n, arch, _m, _c) in enumerate(listos) for a in arch]
                        ia_ok = bool(prop) and ia_res["validada"] and all(
                            (gi, a.nombre) in prop and prop[(gi, a.nombre)][0] == a.llave for gi, a in usadas)
                        if ia_ok and not de_memoria and ss.get("origen") != "guardado":
                            ss.origen = "ia"
                    mem = almacen.recordar(firma_real, ss.cfg_actual, corregida, " vs ".join(n for n, *_ in listos))
                    ss.recordados.add(clave_mem)
                    # aprendizaje por uso del modelo de llave: las llaves confirmadas son ejemplos etiquetados
                    try:
                        arch_u = [a for _n, arch, _m, _c in listos for a in arch]
                        ej = ejemplos_de_cruce([a.df for a in arch_u],
                                               [a.llave[0] if len(a.llave) == 1 else None for a in arch_u],
                                               arch_u[0].transformacion)
                        if ej["X"]:
                            almacen.registrar_uso_llave(ej)
                            ss.pop("modelo_llave", None)  # se reentrena en la próxima sugerencia
                    except Exception as e:  # noqa: BLE001
                        st.caption(f"{_('No se pudo actualizar el modelo de llave')}: {e}")
                    ss.memoria_id = mem.id if (mem is not None and almacen.remoto) else None
                    ss.aceptada = (not corregida) if de_memoria else (ia_ok if ia_res is not None else None)
                    if ss.get("origen") == "texto" and ss.get("memoria_cfg") is not None:  # ¿la usó tal cual la propuso la IA?
                        ss.aceptada = config_equivalente(ss.cfg_actual, ss.memoria_cfg)
                    ss.get("mem_cache", {}).pop(firma_real, None)
                    rg = ss.res_grupos
                    try:
                        almacen.registrar_evento({
                            "firma_archivos": firma_real, "origen": ss.get("origen") or "heuristica",
                            "memoria_confianza": ss.get("memoria_confianza") if de_memoria else None,
                            "aceptada": ss.aceptada, "corregida": corregida if de_memoria else None,
                            "segundos_preparacion": seg_prep, "segundos_cruce": seg_cruce, "n_grupos": len(listos),
                            "n_archivos": sum(len(arch) for _n, arch, _m, _c in listos),
                            "filas": int(sum(len(a.df) for _n, arch, _m, _c in listos for a in arch)),
                            "llaves": int(len(rg.matriz)),
                            "con_diferencias": int(str(rg.resumen.get("en todos, con diferencias", 0)) or 0),
                            "llamadas_ia": int(ss.get("ia_llamadas", 0)), "tokens_ia": int(ss.get("ia_tokens", 0)),
                            "ia_llave_validada": ia_ok, "idioma": ss.idioma})
                        ss.ia_ult = (int(ss.get("ia_llamadas", 0)), int(ss.get("ia_tokens", 0)))
                        ss.ia_llamadas = ss.ia_tokens = 0  # cada evento cuenta solo sus propias llamadas
                    except Exception as e:  # noqa: BLE001
                        st.caption(f"{_('No se pudo registrar la métrica')}: {e}")
                    ss.t_prep = time.time()  # el próximo cruce mide su propia preparación
            except Exception as e:  # noqa: BLE001
                st.warning(f"{_('No se pudo actualizar la memoria de configuración')}: {e}")
        r = ss.get("res_grupos")
        if r is not None:
            nombres = [n for n, *_ in listos]
            st.markdown(ui_estilo.cuadre(
                int(r.resumen.get("en todos, iguales", 0)), int(r.resumen["en todos, con diferencias"]),
                [(n, int(r.resumen.get(f"solo en {n}", 0))) for n in nombres], int(r.resumen["llaves en el universo"]),
                {"ok": _("Coinciden en todos los grupos"), "dif": _("En todos, con diferencias de importe"),
                 "solo": _("Solo en {0}"), "total": _("{0} llaves en total")}), unsafe_allow_html=True)
            for g_, st_ in r.estadisticas.items():  # referencias que casi no encuentran nada: suele ser la llave o «Buscar por»
                for k_, v_ in st_.items():
                    mm = re.match(r"encontradas en (.+)$", str(k_))
                    pc = re.search(r"\(([\d.]+)%\)", str(v_))
                    if mm and pc and float(pc.group(1)) < 10:
                        st.warning(_("La referencia «{0}» de «{1}» encontró solo {2}% de las filas. Revisá su llave, la normalización "
                                     "y «Buscar por» (por ejemplo, una tabla de equivalencias se busca por la letra, no por el identificador).")
                                   .format(mm.group(1), g_, pc.group(1)))
            n_llaves = [int(str(st_.get("llaves únicas", 0)) or 0) for st_ in r.estadisticas.values()]
            if n_llaves and min(n_llaves) > 0 and int(r.resumen["en todos los grupos"]) < 0.1 * min(n_llaves):
                st.warning(_("Las llaves de los grupos casi no coinciden ({0} en común). Revisá que todos usen la misma columna "
                             "(por ejemplo, el documento y no la cuenta) y la misma normalización.").format(r.resumen["en todos los grupos"]))
            with st.expander(_("Estadísticas por grupo (filas, llaves, duplicados, exclusiones, lookups)")):
                est = pd.DataFrame({g: {k: str(v) for k, v in s.items()} for g, s in r.estadisticas.items()}).fillna("–")
                st.dataframe(_df(est), width="stretch")
            excl = getattr(r, "excluidas", None)
            if excl is not None and len(excl):
                with st.expander(_("Filas excluidas: {0} (filtros y agrupaciones, con el motivo)").format(f"{len(excl):,}".replace(",", "."))):
                    st.dataframe(_df(excl.head(MOSTRAR).astype(str).replace({"None": "", "nan": "", "<NA>": ""}), columnas_valor=("motivo",)),
                                 hide_index=True, width="stretch")
            tot = getattr(r, "totales", None)
            if tot is not None and len(tot):
                with st.expander(_("Totales por grupo: {0} grupos, {1} cumplen la condición").format(
                        f"{len(tot):,}".replace(",", "."), f"{int((tot['cumple'] == 'sí').sum()):,}".replace(",", "."))):
                    st.dataframe(_df(tot.head(MOSTRAR)), hide_index=True, width="stretch")
            if len(r.diferencias):
                st.markdown(_("**Diferencias de importe**"))
                st.dataframe(ui_estilo.tabla_continua(_df(r.diferencias)), hide_index=True, width="stretch")
            estados = ["(todos)"] + sorted(r.matriz["estado"].unique())
            ver = st.selectbox(_("Ver llaves"), estados, key="ver_estado", format_func=_)
            m = r.matriz if ver == "(todos)" else r.matriz[r.matriz["estado"] == ver]
            if len(m) > MOSTRAR:
                st.caption(_("Se muestran las primeras {0} de {1} llaves; el Excel tiene el detalle.").format(
                    f"{MOSTRAR:,}".replace(",", "."), f"{len(m):,}".replace(",", ".")))
            vista = m.head(MOSTRAR).map(lambda v: "✔" if v is True else ("" if v is False else v)).astype(str).replace({"None": "", "nan": ""})
            marcas = ["dif" if d else ("ok" if t else "solo")
                      for t, d in zip(m.head(MOSTRAR)["en todos"], m.head(MOSTRAR)["con diferencias"])]
            st.dataframe(ui_estilo.tabla_continua(_df(vista), marcas, list(vista.columns).index("estado")),
                         hide_index=True, width="stretch")
            if ss.get("res_excel") is not None and ss.get("res_excel_hojas") != list(hojas_excel):
                grande_r = max([len(r.matriz), *[len(d) for d in r.detalles.values()]]) > GRANDE
                ss.res_excel = None if grande_r else r.a_excel(hojas_sel=hojas_excel)
                ss.res_excel_hojas = list(hojas_excel)
            excel = ss.get("res_excel")
            if excel is None:
                st.caption(_("El resultado es grande: el Excel se arma a pedido (puede tardar un minuto) y cada hoja se limita a 200.000 filas."))
                if st.button(_("Preparar Excel"), key="preparar_excel"):
                    with st.spinner(_("Armando el Excel…")):
                        ss.res_excel = excel = r.a_excel(hojas_sel=hojas_excel)
                        ss.res_excel_hojas = list(hojas_excel)
            if excel is not None:
                st.download_button(_("Descargar resultado (Excel)"), excel, "conciliacion.xlsx")
            if st.button(_("🧹 Nueva conciliación"), key="limpiar_abajo",
                         help=_("Quita los archivos, el resultado y la configuración aplicada para empezar otra conciliación.")):
                limpiar_conciliacion()
                st.rerun()
            if ss.get("cfg_actual") is not None:
                with st.expander(_("💾 Guardar este cruce"), expanded=False):
                    st.caption(_("Se guardan la configuración (para repetirla con los archivos del próximo período) y el resultado."))
                    nom_def = ss.cfg_nombre or " vs ".join(nombres)
                    nombre_c = st.text_input(_("Nombre del cruce"), nom_def, key=f"nom_guardar_{V}")
                    notas = st.text_area(_("Notas (opcional)"), key=f"notas_guardar_{V}")
                    if st.button(_("Guardar"), type="primary", key="btn_guardar"):
                        if excel is None:
                            with st.spinner(_("Armando el Excel…")):
                                ss.res_excel = excel = r.a_excel(hojas_sel=hojas_excel)
                                ss.res_excel_hojas = list(hojas_excel)
                        g = almacen.guardar(nombre_c, ss.cfg_actual, r.resumen, ss.get("archivos_actual", []), excel,
                                            usuario_actual(), notas, ss.get("origen") or "heuristica", ss.get("aceptada"),
                                            ss.get("memoria_id"), *ss.get("ia_ult", (0, 0)))
                        st.success(_('Guardado «{0}». Lo encontrás en la pestaña «Mis cruces» y arriba, en «Partir de un cruce guardado».').format(g.nombre))
    elif any(g is None for g in grupos_ui):
        st.caption(_("Para probar: Grupo 1 = ejemplos/grupos/g1_retenciones_sistema.csv + g1_padron_referencia.csv "
                   "(modo base + referencia) · Grupo 2 = ejemplos/grupos/g2_reporte_agente.csv"))
    _paso = 3 if ss.get("res_grupos") is not None else (2 if len(listos) >= 2 and len(listos) == len(grupos_ui) else 1)
    paso_ph.markdown(ui_estilo.casillas(_paso, [_("Archivos"), _("Comparación"), _("Resultado")]), unsafe_allow_html=True)


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
                aplicar_config(g, "guardado")
                st.rerun()
            if x3.checkbox(_("Borrar"), key=f"conf_{g.id}") and x3.button(_("Confirmar borrado"), key=f"del_{g.id}"):
                almacen.borrar(g.id)
                st.rerun()


# ---------------------------------------------------------------- métricas (solo administradoras)
if _t_met:
    with _t_met[0]:
        from regspec import metricas as met
        st.caption(_("Uso real de la herramienta para el capítulo de resultados: cada «Cruzar» deja un registro con el origen de la "
                     "configuración, si se aceptó o se corrigió, el tiempo de preparación y el uso de IA."))
        if st.button(_("Actualizar"), key="met_actualizar"):
            ss.pop("met_datos", None)
        if "met_datos" not in ss:
            try:
                ss.met_datos = (almacen.eventos(), almacen.memorias())
            except Exception as e:  # noqa: BLE001
                st.error(f"{_('No se pudieron leer las métricas')}: {e}")
                ss.met_datos = ([], [])
        eventos, memorias = ss.met_datos
        if almacen.remoto and not almacen.es_admin():
            st.info(_("Solo ves los cruces de tu espacio de trabajo."))
        if not eventos:
            st.info(_("Todavía no hay cruces registrados."))
        else:
            r_met = met.resumen(eventos)
            k = st.columns(4)
            k[0].metric(_("Cruces"), r_met["cruces"])
            k[1].metric(_("Con configuración aprendida"), r_met["cruces con configuración aprendida"])
            k[2].metric(_("Aceptación de lo aprendido"), "–" if r_met["tasa de aceptación de lo aprendido (%)"] is None
                        else f"{r_met['tasa de aceptación de lo aprendido (%)']}%")
            k[3].metric(_("Ahorro de preparación"), "–" if r_met["ahorro de tiempo de preparación (%)"] is None
                        else f"{r_met['ahorro de tiempo de preparación (%)']}%")
            st.dataframe(_df(pd.DataFrame([(_(a.strip()), "–" if v is None else str(v)) for a, v in r_met.items()],
                                          columns=["indicador", "valor"])), hide_index=True, width="stretch")
            st.markdown(_("**Por origen de la configuración**"))
            st.dataframe(_df(met.por_origen(eventos)), hide_index=True, width="stretch")
            evo = met.evolucion_confianza(eventos)
            if len(evo):
                st.markdown(_("**Evolución de la confianza de la memoria**"))
                st.line_chart(evo.pivot_table(index="uso", columns="firma_archivos", values="memoria_confianza"))
            st.download_button(_("Descargar métricas (Excel)"), met.a_excel(eventos, memorias), "metricas_uso.xlsx")
