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
from .proveedores import CupoAgotado, ErrorProveedor, Proveedor, Respuesta
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
    # manual que describe varios archivos: una extracción (y su verificación) por archivo
    partes: list = field(default_factory=list)
    notas: list = field(default_factory=list)
    # se cortó porque el proveedor agotó el cupo diario: la especificación quedó a medio corregir (no es un fallo del método)
    cupo_agotado: bool = False

    @property
    def tokens(self) -> int:
        return sum(i.tokens_entrada + i.tokens_salida for i in self.iteraciones)

    @property
    def latencia_s(self) -> float:
        return sum(i.latencia_s for i in self.iteraciones)

    def traza(self) -> dict:
        return {"valida": self.valida, "modelo": self.modelo, "error": self.error, "tokens": self.tokens,
                "cupo_agotado": self.cupo_agotado,
                "reglas_restauradas": self.reglas_restauradas,
                "latencia_s": round(self.latencia_s, 2), "iteraciones": [asdict(i) for i in self.iteraciones],
                "notas": self.notas, "partes": [{"nombre": p.spec.nombre if p.spec else None, "valida": p.valida}
                                                for p in self.partes]}


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
GLOBALES = {"nombre", "version", "formato", "longitud_registro", "separador_lineas", "separador_campos", "codificacion",
            "etiqueta_raiz_xml"}


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
    """Aplica una corrección localizada. Si el modelo devolvió una especificación completa, la usa tal cual.
    Una lista de registros vacía no es una especificación completa: los modelos chicos la devuelven para decir
    «sin cambios en los registros», y tomarla al pie de la letra borraba todos los registros."""
    trs_parche = parche.get("tipos_registro")
    if isinstance(trs_parche, list) and any(isinstance(t, dict) for t in trs_parche):
        return parche
    d = copy.deepcopy(d)
    for k, v in (parche.get("globales") or {}).items():
        if k in GLOBALES:
            d[k] = v
    trs = d.setdefault("tipos_registro", []) if isinstance(d.get("tipos_registro", []), list) else []
    for i, v in (trs_parche if isinstance(trs_parche, dict) else {}).items():
        if not (str(i).isdigit() and isinstance(v, dict)):
            continue
        if int(i) < len(trs):
            trs[int(i)] = _fusionar_registro(trs[int(i)], v)
        elif int(i) == len(trs) and v.get("codigo") is not None:  # registro que faltaba: se agrega al final
            trs.append(v)
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


def proteger_reglas(anterior: Optional[dict], nuevo: dict, manual: str,
                    descartables: frozenset = frozenset()) -> tuple[dict, list[str]]:
    """Impide que el modelo "apruebe" el verificador borrando reglas: toda regla anterior con evidencia literal
    en el manual que desaparece se restaura, y se informa para que la corrija en lugar de eliminarla.
    `descartables`: reglas que el lenguaje no pudo expresar en las dos últimas iteraciones; esas sí se pueden quitar
    (quedan anotadas para revisión manual) porque restaurarlas solo repite el mismo error."""
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
        if not presente and str(r.get("id")) in descartables:
            continue
        if not presente and evidencia_en_manual(str(r.get("evidencia", "")), mn, lineas_norm=lineas):
            nuevas.append(r)
            restauradas.append(str(r.get("id")))
    if restauradas:
        nuevo = {**nuevo, "reglas": nuevas}
    return nuevo, restauradas


from ..verificador import DECL_ARCHIVO as _DECL_ARCHIVO  # noqa: E402


def normalizar_decimales(d: dict) -> tuple[dict, list[str]]:
    """Notación «Numérico(n,d)» de muchos manuales: n enteros y d decimales. Si un campo decimal quedó con tantos o más
    decimales que su longitud, se toma la longitud como n + d. Cuando el manual da Desde/Hasta, deciden las posiciones:
    si el ancho es n, la longitud ya era la total y no se toca; si es n + d, se confirma sin aviso. Solo se avisa
    cuando no hay posiciones para comprobarlo, una vez por campo aunque se repita en varios registros."""
    avisos: dict[tuple, list[str]] = {}
    for tr in d.get("tipos_registro") or []:
        for c in (tr.get("campos") or []) if isinstance(tr, dict) else []:
            if not isinstance(c, dict) or c.get("tipo") != "decimal":
                continue
            lg, dec = c.get("longitud"), c.get("decimales") or 0
            if not (isinstance(lg, int) and isinstance(dec, int) and 0 < lg <= dec):
                continue
            ini, fin = c.get("inicio"), c.get("fin")
            ancho = fin - ini + 1 if isinstance(ini, int) and isinstance(fin, int) and fin >= ini else None
            if ancho == lg:  # las posiciones dicen que la longitud ya incluía los decimales
                continue
            c["longitud"] = lg + dec
            if ancho != lg + dec:  # sin posiciones (o que no cierran): interpretación a revisar
                avisos.setdefault((str(c.get("nombre")), lg, dec), []).append(str(tr.get("codigo")))
    notas = []
    for (nombre, lg, dec), regs in avisos.items():
        donde = f"registro {regs[0]}" if len(regs) == 1 else "registros " + ", ".join(regs[:-1]) + f" y {regs[-1]}"
        notas.append(f"{nombre} ({donde}): «({lg},{dec})» se tomó como {lg} enteros + {dec} decimales "
                     f"(longitud {lg + dec}). Revisalo contra el manual.")
    return d, notas


_ENUMERADOS = re.compile(r"(?<![\w.,])(\d{1,3})\s*[-–=]\s*[A-Za-zÁÉÍÓÚáéíóúÑñ]")


def normalizar_constantes(d: dict) -> tuple[dict, list[str]]:
    """Un campo marcado constante que no identifica a su registro, pero cuya descripción enumera valores
    («1-Retención, 2-Informativo, 3-Excluido…»), es un dato con valores permitidos, no un valor fijo."""
    notas = []
    for tr in d.get("tipos_registro") or []:
        for c in (tr.get("campos") or []) if isinstance(tr, dict) else []:
            if not isinstance(c, dict) or c.get("tipo") != "constante" or str(c.get("valor_constante")) == str(tr.get("codigo")):
                continue
            valores = list(dict.fromkeys(_ENUMERADOS.findall(f"{c.get('descripcion', '')} {c.get('evidencia', '')}")))
            if len(valores) >= 2:
                c.update(tipo="numerico" if all(v.isdigit() for v in valores) else "alfanumerico",
                         valores_permitidos=valores, valor_constante=None)
                notas.append(f"{tr.get('codigo')}.{c.get('nombre')}: enumera valores ({', '.join(valores)}): "
                             "se toma como dato con valores permitidos, no como constante.")
    return d, notas


def archivos_independientes(d: dict, manual: str) -> bool:
    """El manual describe varios ARCHIVOS (no varios tipos de registro de un mismo archivo): hay más de un tipo de
    registro, ninguno tiene un campo que lo identifique dentro del archivo y el manual declara más de un archivo."""
    trs = [t for t in (d.get("tipos_registro") or []) if isinstance(t, dict)]
    if len(trs) < 2 or d.get("formato") == "xml":
        return False
    def identificado(tr):
        return any(isinstance(c, dict) and c.get("tipo") == "constante" and str(c.get("valor_constante")) == str(tr.get("codigo"))
                   for c in tr.get("campos") or [])
    if not any(identificado(t) for t in trs) and len(_DECL_ARCHIVO.findall(manual)) >= 2:
        return True
    # los registros son exactamente los archivos que declara el manual (esqueleto armado por código): aunque el
    # modelo les haya agregado un «identificador», son archivos distintos
    from .secciones import archivos_del_manual

    titulos = archivos_del_manual(manual)
    return len(titulos) >= 2 and sorted(str(t.get("nombre")) for t in trs) == sorted(titulos)


def separar_archivos(d: dict, manual: str = "") -> list[dict]:
    """Una especificación por tipo de registro, con los atributos de archivo comunes y las reglas de ese registro.
    Si el modelo agregó un campo «identificador» con un código que no figura en el manual, se quita: en un archivo
    con un solo tipo de registro no hay nada que identificar."""
    partes = []
    for tr in d.get("tipos_registro") or []:
        cod = str(tr.get("codigo"))
        if manual and not re.search(rf"(?<!\w){re.escape(cod)}(?!\w)", manual):
            tr = {**tr, "campos": [c for c in tr.get("campos") or [] if not (
                isinstance(c, dict) and c.get("tipo") == "constante" and str(c.get("valor_constante")) == cod)]}
        reglas = [r for r in d.get("reglas") or [] if isinstance(r, dict) and r.get("ambito") == "registro"
                  and str(r.get("tipo_registro")) == str(tr.get("codigo"))]
        nombre = f"{d.get('nombre') or 'Especificación'} · {tr.get('nombre') or tr.get('codigo')}"
        partes.append({**d, "nombre": nombre[:120], "tipos_registro": [tr], "reglas": reglas})
    return partes


def solo_un_archivo(d: dict, ref: dict) -> tuple[dict, bool]:
    """Compilando un archivo de un manual con varios: el modelo, que ve el manual entero, a veces devuelve también los
    registros de los otros archivos. Se conserva solo el registro de este archivo (por código, nombre o campos) y sus
    reglas. Devuelve (especificación, si se descartó algo)."""
    trs = [t for t in d.get("tipos_registro") or [] if isinstance(t, dict)]
    if len(trs) <= 1 and len(trs) == len(d.get("tipos_registro") or []):
        return d, False
    nombres_ref = {c.get("nombre") for c in ref.get("campos") or [] if isinstance(c, dict)}

    def afinidad(t):
        nombres = {c.get("nombre") for c in t.get("campos") or [] if isinstance(c, dict)}
        return (str(t.get("codigo")) == str(ref.get("codigo")), t.get("nombre") == ref.get("nombre"),
                len(nombres & nombres_ref))
    if not trs:
        return d, False
    elegido = max(trs, key=afinidad)
    if afinidad(elegido) == (False, False, 0):
        return d, False
    otros = {str(t.get("codigo")) for t in trs if t is not elegido} - {str(elegido.get("codigo"))}

    def propia(r):
        if not isinstance(r, dict):
            return False
        if r.get("ambito") == "registro":
            return str(r.get("tipo_registro")) == str(elegido.get("codigo"))
        return not any(f'"{c}"' in str(r.get("expresion", "")) for c in otros)
    reglas = [r for r in d.get("reglas") or [] if propia(r)]
    return {**d, "tipos_registro": [elegido], "reglas": reglas}, True


def registros_de_respaldo(manual: str) -> list[dict]:
    """Registros leídos sin IA, para cuando el modelo no listó ninguno: primero los del parser por código (con sus
    campos); si no, uno por cada archivo que declara el manual (sin campos: la corrección por registro los completa)."""
    from ..base_sin_ia import extraer_base
    from .secciones import archivos_del_manual

    base = [t for t in extraer_base(manual).get("tipos_registro") or [] if isinstance(t, dict) and t.get("codigo")]
    if base:
        return copy.deepcopy(base)
    titulos = archivos_del_manual(manual)
    if len(titulos) >= 2 and len(set(titulos)) == len(titulos):
        return [{"codigo": f"A{i}", "nombre": t, "min_ocurrencias": 1, "max_ocurrencias": None, "posicion": "cualquiera",
                 "campos": []} for i, t in enumerate(titulos, 1)]
    return []


def _anotar_descartadas(anterior, nuevo, descartables, res) -> None:
    """Deja constancia de las reglas que se quitaron porque el lenguaje no pudo expresarlas."""
    if not descartables or not isinstance(anterior, dict) or not isinstance(nuevo, dict):
        return
    quedan = {str(r.get("id")) for r in nuevo.get("reglas") or [] if isinstance(r, dict)}
    for r in anterior.get("reglas") or []:
        if isinstance(r, dict) and str(r.get("id")) in descartables and str(r.get("id")) not in quedan:
            nota = (f"La regla {r.get('id')} no se pudo expresar en el lenguaje de reglas después de dos intentos y se "
                    f"descartó: revisala a mano. Manual: «{str(r.get('evidencia', ''))[:160]}»")
            if nota not in res.notas:
                res.notas.append(nota)

SIN_REGISTROS_MSG = ("no se encontró ningún tipo de registro en el manual: revisá que sea el manual técnico del archivo "
                     "(el que describe sus campos, formatos y posiciones)")


def extraer(manual: str, proveedor: Proveedor, config: Optional[ConfigExtraccion] = None,
            al_iterar=None, al_paso=None, inicial: Optional[dict] = None, _separar: bool = True) -> ResultadoExtraccion:
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
    invalidas: dict[str, int] = {}  # id de regla -> iteraciones seguidas en que el lenguaje no pudo expresarla

    for n in range(1, n_iter + 1):
        if n == 1 and inicial is not None:  # se continúa desde una especificación ya armada (p. ej. un archivo de varios)
            recibido, r = inicial, Respuesta("", 0, 0, 0.0, getattr(proveedor, "modelo", ""))
        elif corregida is not None:
            recibido, r = corregida
            corregida = None
            esperando_parche = False
        elif n == 1 and cfg.modo == "secciones":
            b = borrador_por_secciones(manual, proveedor, extraer_json, cfg.tokens_por_llamada, cfg.temperatura, al_paso,
                                       usar_codigo=cfg.usar_codigo)
            r = Respuesta("", sum(p.tokens_entrada for p in b.pasos), sum(p.tokens_salida for p in b.pasos),
                          sum(p.latencia_s for p in b.pasos), getattr(proveedor, "modelo", ""))
            res.cupo_agotado = b.cupo_agotado
            if b.spec is None:
                res.error = b.error
                res.iteraciones.append(Iteracion(n, False, 1, {"secciones": 1}, [b.error or "falló la extracción por secciones"],
                                                 r.tokens_entrada, r.tokens_salida, r.latencia_s))
                break
            if b.error:
                res.error = b.error
            if not (b.spec.get("tipos_registro") if isinstance(b.spec, dict) else None):
                # sin registros no hay nada que corregir: seguir solo gastaría iteraciones
                res.error = b.error or SIN_REGISTROS_MSG
                res.iteraciones.append(Iteracion(n, False, 1, {"estructura": 1}, [res.error],
                                                 r.tokens_entrada, r.tokens_salida, r.latencia_s))
                if al_iterar:
                    al_iterar(res.iteraciones[-1])
                break
            recibido = b.spec
        else:
            try:
                r = proveedor.completar(mensajes, modo_json=True, temperatura=cfg.temperatura, max_tokens=cfg.tokens_por_llamada)
            except ErrorProveedor as e:
                res.error = str(e)
                res.cupo_agotado = isinstance(e, CupoAgotado)
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
        if (isinstance(d, dict) and isinstance(actual, dict) and actual.get("tipos_registro")
                and not d.get("tipos_registro")):
            # una corrección no puede dejar el archivo sin registros: se conservan los de la iteración anterior
            d = {**d, "tipos_registro": copy.deepcopy(actual["tipos_registro"])}
        descartables = frozenset(k for k, v in invalidas.items() if v >= 2)
        d, restauradas = proteger_reglas(actual, d, manual, descartables)
        _anotar_descartadas(actual, d, descartables, res)
        restauradas, rest_pendientes = restauradas + rest_pendientes, []
        if isinstance(d, dict) and not _separar and inicial is not None and (inicial.get("tipos_registro") or [None])[0]:
            d, recortada = solo_un_archivo(d, inicial["tipos_registro"][0])
            if recortada and n > 1:
                nota = "El modelo agregó registros de los otros archivos del manual: se descartaron."
                if nota not in res.notas:
                    res.notas.append(nota)
        if isinstance(d, dict) and not d.get("tipos_registro"):
            # el modelo no listó ningún registro y la corrección no puede inventarlos: se parte de lo leído por
            # código o, si el manual no describe ninguno, se corta sin gastar más iteraciones
            respaldo = registros_de_respaldo(manual)
            if respaldo:
                d = {**d, "tipos_registro": respaldo}
                nota = "El modelo no listó los registros del archivo: se tomaron los que se leyeron por código."
                if nota not in res.notas:
                    res.notas.append(nota)
            else:
                res.error = SIN_REGISTROS_MSG
                res.spec_dict = d
                it = Iteracion(n, False, 1, {"estructura": 1}, [SIN_REGISTROS_MSG], r.tokens_entrada, r.tokens_salida,
                               r.latencia_s)
                res.iteraciones.append(it)
                if al_iterar:
                    al_iterar(it)
                break
        if isinstance(d, dict):
            d, notas_dec = normalizar_decimales(d)
            d, notas_const = normalizar_constantes(d)
            notas_dec += notas_const
            res.notas += [x for x in notas_dec if x not in res.notas]
            if _separar and archivos_independientes(d, manual):
                # varios archivos en un manual: cada uno se verifica y se corrige por separado, con las iteraciones restantes
                res.notas.append(f"El manual describe {len(d['tipos_registro'])} archivos distintos: se compiló cada uno por separado.")
                if r.tokens_entrada or r.tokens_salida:
                    res.iteraciones.append(Iteracion(n, True, 0, {}, ["borrador con varios archivos: se separa"],
                                                     r.tokens_entrada, r.tokens_salida, r.latencia_s))
                restantes = ConfigExtraccion(**{**cfg.__dict__, "max_iteraciones": max(1, n_iter - n + 1)})
                for parte in separar_archivos(d, manual):
                    desde = len(res.iteraciones)

                    def numerar(it, desde=desde):  # numeración continua entre archivos
                        it.numero += desde
                        if al_iterar:
                            al_iterar(it)
                    sub = extraer(manual, proveedor, restantes, numerar, al_paso, inicial=parte, _separar=False)
                    res.partes.append(sub)
                    res.iteraciones += sub.iteraciones
                    res.notas += [x for x in sub.notas if x not in res.notas]
                validas = [x for x in res.partes if x.valida]
                res.valida = len(validas) == len(res.partes)
                elegida = (validas or res.partes)[0]
                res.spec, res.spec_dict = elegida.spec, elegida.spec_dict
                errores = list(dict.fromkeys(x.error for x in res.partes if x.error))
                res.error = "; ".join(errores) if errores and not res.valida else None
                res.cupo_agotado = res.cupo_agotado or any(x.cupo_agotado for x in res.partes)
                return res

        # Retroalimentación: se usa la configuración de la condición (para los estudios de ablación)
        ver: ResultadoVerificacion = verificar(
            d, manual, chequear_evidencia=cfg.chequear_evidencia, chequear_ejecucion=cfg.chequear_ejecucion
        )
        problemas = [p.a_texto() for p in ver.errores]
        ids_reglas = [str(x.get("id")) for x in (d.get("reglas") or []) if isinstance(x, dict)] if isinstance(d, dict) else []
        ahora = set()
        for p in ver.errores:
            m = _RX_RG.match(p.ruta)
            if p.codigo == "REGLA_INVALIDA" and m and int(m.group(1)) < len(ids_reglas):
                ahora.add(ids_reglas[int(m.group(1))])
        invalidas = {k: invalidas.get(k, 0) + 1 for k in ahora}
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
            trabadas = frozenset(k for k, v in invalidas.items() if v >= 2)
            if trabadas and isinstance(d, dict):
                # el modelo no logra expresar estas reglas en el lenguaje: se quitan (quedan anotadas para revisión
                # manual) y se vuelve a verificar sin otra llamada al modelo
                sin = {**d, "reglas": [x for x in d.get("reglas") or [] if not (isinstance(x, dict) and str(x.get("id")) in trabadas)]}
                _anotar_descartadas(d, sin, trabadas, res)
                invalidas = {k: v for k, v in invalidas.items() if k not in trabadas}
                corregida = (sin, Respuesta("", 0, 0, 0.0, getattr(proveedor, "modelo", "")))
                actual, ultima_huella = sin, None
                continue
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
                res.cupo_agotado = isinstance(e, CupoAgotado)
                break
            nuevo, rest_lote = proteger_reglas(d, nuevo, manual, frozenset(k for k, v in invalidas.items() if v >= 2))
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
