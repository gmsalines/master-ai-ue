"""Extractor neuro-simbólico: LLM + verificador formal + bucle de autocorrección.

    manual ──► LLM ──► JSON ──► verificador ──┬─► válida ──► especificación
                ▲                             │
                └── problemas accionables ◄───┘  (hasta max_iteraciones)
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..dsl import Campo, Especificacion, Regla, TipoRegistro
from ..verificador import Problema, ResultadoVerificacion, verificar
from .prompts import mensaje_correccion, mensaje_json_invalido, mensaje_parche, mensajes_iniciales
from .proveedores import ErrorProveedor, Proveedor, Respuesta
from .recuperacion import dividir
from .secciones import borrador_por_secciones, contexto_esqueleto, contexto_registro, contexto_reglas, mensajes_con_contexto


@dataclass
class ConfigExtraccion:
    max_iteraciones: int = 4
    usar_verificador: bool = True  # False = una sola llamada (sin retroalimentación)
    chequear_evidencia: bool = True  # anclaje literal al manual en la retroalimentación
    chequear_ejecucion: bool = True  # prueba de ida y vuelta en la retroalimentación
    con_esquema: bool = False  # el esquema completo agrega ~1.500 tokens; la doc + ejemplo alcanzan
    con_ejemplo: bool = True
    temperatura: float = 0.0
    correccion: str = "localizada"  # "localizada" (solo fragmentos con problemas) o "completa"
    modo: str = "secciones"  # "secciones" (esqueleto -> campos por registro -> reglas) o "completo" (una sola respuesta)
    max_tokens: Optional[int] = None  # por llamada; por defecto 3000 en secciones y 8000 en completo
    usar_codigo: bool = True  # secciones: el parser sin IA lee las tablas y la IA solo corrige o completa

    @property
    def tokens_por_llamada(self) -> int:
        return self.max_tokens or (3000 if self.modo == "secciones" else 8000)


@dataclass
class Iteracion:
    numero: int
    json_valido: bool
    errores: int
    errores_por_etapa: dict
    problemas: list[str]
    tokens_entrada: int
    tokens_salida: int
    latencia_s: float


@dataclass
class ResultadoExtraccion:
    spec: Optional[Especificacion]
    spec_dict: Optional[dict]
    valida: bool
    iteraciones: list[Iteracion] = field(default_factory=list)
    error: Optional[str] = None
    modelo: str = ""
    reglas_restauradas: list = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return sum(i.tokens_entrada + i.tokens_salida for i in self.iteraciones)

    @property
    def latencia_s(self) -> float:
        return sum(i.latencia_s for i in self.iteraciones)

    def traza(self) -> dict:
        return {"valida": self.valida, "modelo": self.modelo, "error": self.error, "tokens": self.tokens,
                "reglas_restauradas": self.reglas_restauradas,
                "latencia_s": round(self.latencia_s, 2), "iteraciones": [asdict(i) for i in self.iteraciones]}


def extraer_json(texto: str) -> dict:
    t = texto.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    ini, fin = t.find("{"), t.rfind("}")
    if ini < 0 or fin < 0:
        raise ValueError("no se encontró un objeto JSON")
    return json.loads(t[ini : fin + 1])


_DEFECTOS = {"descripcion": "", "fin": None, "decimales": 0, "formato_fecha": None, "obligatorio": True,
             "valores_permitidos": None, "valor_constante": None, "patron": None, "etiqueta_xml": None, "inicio": None}


def _compactar(d: dict) -> str:
    def limpiar(x):
        if isinstance(x, dict):
            return {k: limpiar(v) for k, v in x.items() if not (k in _DEFECTOS and v == _DEFECTOS[k])}
        if isinstance(x, list):
            return [limpiar(v) for v in x]
        return x
    return json.dumps(limpiar(d), ensure_ascii=False, separators=(",", ":"))


_RX_TR = re.compile(r"^tipos_registro\[(\d+)\]")
_RX_RG = re.compile(r"^reglas\[(\d+)\]")
_RX_RIDS = re.compile(r"^reglas ([\w, ]+)$")
_RX_REG = re.compile(r"^registro (\S+?)\.")
GLOBALES = {"nombre", "version", "formato", "longitud_registro", "separador_lineas", "etiqueta_raiz_xml"}


def _localizar_uno(p: Problema, codigos: list, ids: list):
    """("tr", {i}) | ("rg", {k}) | ("glob", None) | None."""
    ruta = p.ruta
    if m := _RX_TR.match(ruta):
        return "tr", {int(m.group(1))}
    if m := _RX_RG.match(ruta):
        return "rg", {int(m.group(1))}
    if m := _RX_RIDS.match(ruta):
        return "rg", {ids.index(x.strip()) for x in m.group(1).split(",") if x.strip() in ids}
    if (m := _RX_REG.match(ruta)) and m.group(1) in codigos:
        return "tr", {codigos.index(m.group(1))}
    if ruta == "tipos_registro":
        return "tr", set(range(len(codigos)))
    if ruta.split(".")[0].split("[")[0] in GLOBALES:
        return "glob", None
    return None


def _indices(d: dict):
    codigos = [t.get("codigo") for t in d.get("tipos_registro", []) if isinstance(t, dict)]
    ids = [r.get("id") for r in d.get("reglas", []) if isinstance(r, dict)]
    return codigos, ids


def localizar(d: dict, problemas: list[Problema]) -> Optional[tuple[set, set, bool]]:
    """Qué fragmentos de la especificación tocan los problemas: (registros, reglas, globales).

    Devuelve None si algún problema no se puede localizar (se reenvía la especificación completa).
    """
    trs, rgs, glob = set(), set(), False
    codigos, ids = _indices(d)
    for p in problemas:
        loc = _localizar_uno(p, codigos, ids)
        if loc is None:
            return None
        tipo, idx = loc
        if tipo == "tr":
            trs |= idx
        elif tipo == "rg":
            rgs |= idx
        else:
            glob = True
    return trs, rgs, glob


def _id_en(texto: str, d: dict, k: int) -> bool:
    try:
        rid = d["reglas"][k].get("id")
    except (KeyError, IndexError, AttributeError):
        return False
    return bool(rid) and re.search(rf"\b{re.escape(str(rid))}\b", texto) is not None


def lotes_de_correccion(d: dict, problemas: list[Problema], tamanio_lote_reglas: int = 4) -> list[tuple[set, set, bool, list[str]]]:
    """Divide la corrección en pedidos chicos: uno por tipo de registro, uno para reglas y uno para globales."""
    codigos, ids = _indices(d)
    por_tr: dict[int, list[str]] = {}
    reglas: tuple[set, list[str]] = (set(), [])
    globales: list[str] = []
    for p in problemas:
        loc = _localizar_uno(p, codigos, ids)
        if loc is None or loc[0] == "glob":
            globales.append(p.a_texto())
        elif loc[0] == "tr":
            for i in loc[1]:
                por_tr.setdefault(i, []).append(p.a_texto())
        else:
            reglas[0].update(loc[1])
            reglas[1].append(p.a_texto())
    lotes = [({i}, set(), False, ps) for i, ps in sorted(por_tr.items())]
    if reglas[1]:
        # las reglas se corrigen de a pocas para que cada respuesta sea corta
        ordenadas = sorted(reglas[0])
        for k in range(0, len(ordenadas), tamanio_lote_reglas):
            grupo = set(ordenadas[k : k + tamanio_lote_reglas])
            probs = [p for p in reglas[1] if any(f"reglas[{g}]" in p or _id_en(p, d, g) for g in grupo)]
            lotes.append((set(), grupo, False, probs or reglas[1]))
    if globales:
        lotes.append((set(), set(), True, globales))
    return lotes


def _validas(d: dict, modelo) -> dict:
    return {k: v for k, v in d.items() if k in modelo.model_fields}


def _fusionar_registro(viejo: dict, nuevo: dict) -> dict:
    """Fusiona un registro corregido sobre el anterior, tolerando respuestas parciales."""
    if not isinstance(viejo, dict):
        return nuevo
    # del registro anterior solo se conservan claves válidas: así una clave mal escrita se elimina al corregirla
    validas = set(TipoRegistro.model_fields)
    out = {**{k: v for k, v in viejo.items() if k in validas},
           **{k: v for k, v in nuevo.items() if k in validas and k != "campos"}}
    borrar = set(nuevo.get("eliminar_campos") or [])
    if borrar:
        out["campos"] = [c for c in (viejo.get("campos") or []) if not (isinstance(c, dict) and c.get("nombre") in borrar)]
        viejo = {**viejo, "campos": out["campos"]}
    campos_nuevos = nuevo.get("campos")
    if isinstance(campos_nuevos, dict):  # {"<posición>": campo} o {"<nombre>": campo}
        campos = list(viejo.get("campos") or [])
        nombres = [c.get("nombre") if isinstance(c, dict) else None for c in campos]
        for k, c in campos_nuevos.items():
            if not isinstance(c, dict):
                continue
            if str(k).isdigit() and int(k) < len(campos):
                j = int(k)
            elif k in nombres:
                j = nombres.index(k)
            else:
                continue
            campos[j] = {**_validas(campos[j], Campo), **c}
        out["campos"] = campos
    elif isinstance(campos_nuevos, list):
        viejos = [c for c in viejo.get("campos") or [] if isinstance(c, dict)]
        if len(campos_nuevos) == len(viejos):
            # misma cantidad: se fusiona posición a posición (el modelo puede devolver solo los atributos corregidos)
            out["campos"] = [{**_validas(v, Campo), **n} if isinstance(n, dict) else v for v, n in zip(viejos, campos_nuevos)]
        elif len(campos_nuevos) > len(viejos):
            out["campos"] = campos_nuevos
        else:  # lista parcial: se fusiona por nombre
            por_nombre = {c.get("nombre"): i for i, c in enumerate(viejos)}
            campos = list(viejos)
            for c in campos_nuevos:
                if isinstance(c, dict) and c.get("nombre") in por_nombre:
                    j = por_nombre[c["nombre"]]
                    campos[j] = {**_validas(campos[j], Campo), **c}
                elif isinstance(c, dict):
                    campos.append(c)
            def pos(c):
                try:
                    return int(c.get("inicio"))
                except (TypeError, ValueError):
                    return 10**9
            out["campos"] = sorted(campos, key=pos) if all(c.get("inicio") for c in campos) else campos
    return out


def aplicar_parche(d: dict, parche: dict) -> dict:
    """Aplica una corrección localizada. Si el modelo devolvió una especificación completa, la usa tal cual."""
    if isinstance(parche.get("tipos_registro"), list):
        return parche
    d = copy.deepcopy(d)
    for k, v in (parche.get("globales") or {}).items():
        if k in GLOBALES:
            d[k] = v
    for i, v in (parche.get("tipos_registro") or {}).items():
        if str(i).isdigit() and int(i) < len(d.get("tipos_registro", [])) and isinstance(v, dict):
            d["tipos_registro"][int(i)] = _fusionar_registro(d["tipos_registro"][int(i)], v)
    reglas = list(d.get("reglas") or [])
    if isinstance(parche.get("reglas"), list):  # el modelo devolvió la lista completa de reglas
        d["reglas"] = [r for r in parche["reglas"] if isinstance(r, dict)]
        return d
    eliminar = set()
    for k, v in (parche.get("reglas") or {}).items():
        if not str(k).isdigit() or int(k) >= len(reglas):
            continue
        if v is None:
            eliminar.add(int(k))
        elif isinstance(v, dict):
            # los modelos a veces devuelven solo los atributos que cambian: se fusiona sobre la regla anterior
            reglas[int(k)] = {**_validas(reglas[int(k)], Regla), **v} if isinstance(reglas[int(k)], dict) else v
    d["reglas"] = [r for i, r in enumerate(reglas) if i not in eliminar] + [r for r in parche.get("reglas_nuevas") or [] if isinstance(r, dict)]
    return d


def proteger_reglas(anterior: Optional[dict], nuevo: dict, manual: str) -> tuple[dict, list[str]]:
    """Impide que el modelo "apruebe" el verificador borrando reglas: toda regla anterior con evidencia literal
    en el manual que desaparece se restaura, y se informa para que la corrija en lugar de eliminarla."""
    from ..verificador import evidencia_en_manual, normalizar

    if not anterior or not isinstance(nuevo, dict):
        return nuevo, []
    mn = normalizar(manual)
    lineas = [normalizar(l) for l in manual.splitlines() if l.strip()]
    nuevas = [r for r in nuevo.get("reglas") or [] if isinstance(r, dict)]
    claves = {normalizar(str(r.get("evidencia", ""))) for r in nuevas} | {str(r.get("id")) for r in nuevas}
    restauradas = []
    for r in anterior.get("reglas") or []:
        if not isinstance(r, dict):
            continue
        presente = normalizar(str(r.get("evidencia", ""))) in claves or str(r.get("id")) in claves
        if not presente and evidencia_en_manual(str(r.get("evidencia", "")), mn, lineas_norm=lineas):
            nuevas.append(r)
            restauradas.append(str(r.get("id")))
    if restauradas:
        nuevo = {**nuevo, "reglas": nuevas}
    return nuevo, restauradas


def extraer(manual: str, proveedor: Proveedor, config: Optional[ConfigExtraccion] = None,
            al_iterar=None, al_paso=None) -> ResultadoExtraccion:
    """Convierte un manual en una especificación verificada.

    `al_iterar(iteracion)` y `al_paso(paso)` son callbacks opcionales para mostrar el progreso.
    """
    cfg = config or ConfigExtraccion()
    base = mensajes_iniciales(manual, cfg.con_esquema, cfg.con_ejemplo)
    mensajes = list(base)
    res = ResultadoExtraccion(None, None, False, modelo=getattr(proveedor, "modelo", ""))
    n_iter = cfg.max_iteraciones if cfg.usar_verificador else 1
    actual: Optional[dict] = None  # última especificación completa
    esperando_parche = False
    corregida: Optional[tuple[dict, Respuesta]] = None  # corrección por lotes ya aplicada (modo secciones)
    ultima_huella = None
    rest_pendientes: list[str] = []

    for n in range(1, n_iter + 1):
        if corregida is not None:
            recibido, r = corregida
            corregida = None
            esperando_parche = False
        elif n == 1 and cfg.modo == "secciones":
            b = borrador_por_secciones(manual, proveedor, extraer_json, cfg.tokens_por_llamada, cfg.temperatura, al_paso,
                                       usar_codigo=cfg.usar_codigo)
            r = Respuesta("", sum(p.tokens_entrada for p in b.pasos), sum(p.tokens_salida for p in b.pasos),
                          sum(p.latencia_s for p in b.pasos), getattr(proveedor, "modelo", ""))
            if b.spec is None:
                res.error = b.error
                res.iteraciones.append(Iteracion(n, False, 1, {"secciones": 1}, [b.error or "falló la extracción por secciones"],
                                                 r.tokens_entrada, r.tokens_salida, r.latencia_s))
                break
            if b.error:
                res.error = b.error
            recibido = b.spec
        else:
            try:
                r = proveedor.completar(mensajes, modo_json=True, temperatura=cfg.temperatura, max_tokens=cfg.tokens_por_llamada)
            except ErrorProveedor as e:
                res.error = str(e)
                break
            recibido = None
        try:
            recibido = recibido if recibido is not None else extraer_json(r.texto)
        except (ValueError, json.JSONDecodeError) as e:
            it = Iteracion(n, False, 1, {"json": 1}, [f"JSON inválido: {e}"], r.tokens_entrada, r.tokens_salida, r.latencia_s)
            res.iteraciones.append(it)
            if al_iterar:
                al_iterar(it)
            mensajes = mensajes[:] + [{"role": "assistant", "content": r.texto[:2000]},
                                      {"role": "user", "content": mensaje_json_invalido(str(e))}]
            continue
        d = aplicar_parche(actual, recibido) if (esperando_parche and actual is not None) else recibido
        d, restauradas = proteger_reglas(actual, d, manual)
        restauradas, rest_pendientes = restauradas + rest_pendientes, []

        # Retroalimentación: se usa la configuración de la condición (para los estudios de ablación)
        ver: ResultadoVerificacion = verificar(
            d, manual, chequear_evidencia=cfg.chequear_evidencia, chequear_ejecucion=cfg.chequear_ejecucion
        )
        problemas = [p.a_texto() for p in ver.errores]
        if restauradas:
            res.reglas_restauradas += restauradas
            problemas.append(f"[REGLA_ELIMINADA] reglas {', '.join(restauradas)}: se eliminaron reglas respaldadas por el "
                             "manual; se restauraron. Corregí su expresión en lugar de eliminarlas.")
        it = Iteracion(n, True, len(ver.errores), ver.por_etapa(), problemas, r.tokens_entrada, r.tokens_salida, r.latencia_s)
        res.iteraciones.append(it)
        if al_iterar:
            al_iterar(it)
        actual = d
        res.spec_dict = d
        res.spec = ver.spec
        res.valida = ver.valida
        if ver.valida or not cfg.usar_verificador:
            break
        # sin progreso: misma especificación y mismos problemas que la iteración anterior -> no gastar más tokens
        huella = (json.dumps(d, sort_keys=True, ensure_ascii=False, default=str), tuple(problemas))
        if huella == ultima_huella:
            res.error = "la corrección no produjo cambios; se detiene para no gastar tokens"
            break
        ultima_huella = huella

        # Modo secciones: la corrección también se hace por partes (un pedido por registro / reglas / globales),
        # para que cada llamada entre en el límite de tokens por minuto.
        if cfg.modo == "secciones":
            nuevo = d
            te = ts = 0
            lat = 0.0
            try:
                frags = dividir(manual)
                for trs, rgs, glob, probs in lotes_de_correccion(d, ver.errores):
                    # cada lote recibe solo los fragmentos del manual que le corresponden
                    if trs:
                        tr0 = (nuevo.get("tipos_registro") or [{}])[min(trs)]
                        ctx = contexto_registro(frags, tr0 if isinstance(tr0, dict) else {})
                    elif rgs:
                        ctx = contexto_reglas(frags)
                    else:
                        ctx = contexto_esqueleto(frags)
                    msj = mensajes_con_contexto(ctx, mensaje_parche(nuevo, trs, rgs, glob, probs, _compactar))
                    rr = proveedor.completar(msj, modo_json=True, temperatura=cfg.temperatura, max_tokens=cfg.tokens_por_llamada)
                    te, ts, lat = te + rr.tokens_entrada, ts + rr.tokens_salida, lat + rr.latencia_s
                    try:
                        nuevo = aplicar_parche(nuevo, extraer_json(rr.texto))
                    except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
                        continue  # parche ilegible: ese lote queda sin corregir y el verificador lo volverá a señalar
            except ErrorProveedor as e:
                res.error = str(e)
                break
            nuevo, rest_lote = proteger_reglas(d, nuevo, manual)
            rest_pendientes = rest_lote
            corregida = (nuevo, Respuesta("", te, ts, lat, getattr(proveedor, "modelo", "")))
            continue

        # Corrección: localizada (solo los fragmentos con problemas) o completa si no se puede localizar.
        loc = localizar(d, ver.errores) if cfg.correccion == "localizada" else None
        if loc is not None:
            trs, rgs, glob = loc
            mensajes = base + [{"role": "user", "content": mensaje_parche(d, trs, rgs, glob, problemas, _compactar)}]
            esperando_parche = True
        else:
            mensajes = base + [{"role": "assistant", "content": _compactar(d)},
                               {"role": "user", "content": mensaje_correccion(problemas)}]
            esperando_parche = False
    return res
