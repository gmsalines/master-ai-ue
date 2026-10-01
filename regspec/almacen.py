"""Cruces guardados: configuración reutilizable + resultado.

Una `ConfigCruce` describe cómo se armó un cruce (grupos, llaves, normalización, filtros, columnas traídas,
importes comparados y tolerancia) sin depender de los nombres de los archivos: al reutilizarla, se aplica a
los archivos nuevos por posición (grupo 1, archivo 1; grupo 1, archivo 2; ...). Así, el cruce de julio se
repite en agosto subiendo los archivos del mes y eligiendo la configuración guardada.

El almacenamiento está detrás de la interfaz `Almacen`: `AlmacenLocal` guarda en una carpeta (JSON + Excel) y
`AlmacenSupabase` en la base de datos (tablas `conciliacion`, `memoria_conciliacion`, `especificacion`) y el storage
(`resultados/<usuario>/<id>.xlsx`), con permisos por workspace.

Memoria de configuración (aprendizaje por uso): cada vez que se cruza, la configuración se recuerda asociada a la
**firma** de los archivos (su estructura: columnas por grupo, no sus nombres ni su contenido). Cuando llegan archivos
con la misma firma, se sugiere la configuración aprendida. La confianza sube cuando se usa sin cambios y baja cuando
el usuario la corrige.

Aplicación automática: una configuración aprendida se aplica sola solo si su confianza supera el 95 % **y** el usuario
aceptó explícitamente que se aplique sola (la aceptación es por usuario y se puede retirar). En los demás casos se
ofrece y el usuario decide.

Métricas: cada «Cruzar» deja un evento (origen de la configuración, si se aceptó o corrigió, tiempos, uso de IA), que
alimenta el capítulo de resultados.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class FiltroCfg(BaseModel):
    columna: str
    operador: str
    valor: str = ""
    accion: str = "excluir"
    valores: list[str] = Field(default_factory=list)


class ArchivoCfg(BaseModel):
    nombre_original: str = ""                 # solo informativo
    llave: list[str]
    transformacion: str = "exacta"
    filtros: list[FiltroCfg] = Field(default_factory=list)
    traer: list[str] = Field(default_factory=list)
    hoja: Optional[str] = None


class GrupoCfg(BaseModel):
    nombre: str
    modo: str = "concatenar"
    archivos: list[ArchivoCfg]


class ConfigCruce(BaseModel):
    grupos: list[GrupoCfg]
    comparaciones: list[list[str]] = Field(default_factory=list)  # una columna por grupo
    tolerancia: str = "0.01"

    def archivo(self, gi: int, fi: int) -> Optional[ArchivoCfg]:
        if gi < len(self.grupos) and fi < len(self.grupos[gi].archivos):
            return self.grupos[gi].archivos[fi]
        return None


class CruceGuardado(BaseModel):
    id: str
    nombre: str
    usuario: str = "local"
    fecha: str
    config: ConfigCruce
    resumen: dict = Field(default_factory=dict)
    archivos: list[str] = Field(default_factory=list)  # nombres de los archivos usados
    notas: str = ""


class Memoria(BaseModel):
    id: str = ""
    firma: str
    config: ConfigCruce
    confianza: int = 50
    veces_usado: int = 0
    veces_corregido: int = 0
    descripcion: str = ""
    auto_usuarios: list[str] = Field(default_factory=list)  # solo en el almacén local


UMBRAL_AUTO = 95  # confianza (en %) que hay que SUPERAR para poder aplicar sola una configuración aprendida


def modo_memoria(mem: Optional["Memoria"], acepto_auto: bool) -> str:
    """Qué hacer con una configuración aprendida: 'nada', 'ofrecer' (el usuario decide), 'ofrecer_auto' (supera el
    umbral: además de usarla, se puede aceptar que se aplique sola) o 'automatica' (supera el umbral y el usuario lo aceptó)."""
    if mem is None:
        return "nada"
    if mem.confianza > UMBRAL_AUTO:
        return "automatica" if acepto_auto else "ofrecer_auto"
    return "ofrecer"


def _norm_col(c) -> str:
    t = unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", t)


def firma_archivos(grupos_columnas: list[list[list]]) -> str:
    """Firma de un conjunto de archivos: columnas (normalizadas) de cada archivo, en el orden de grupos y archivos.

    No depende del nombre de los archivos ni de sus datos: julio y agosto del mismo reporte tienen la misma firma.
    """
    estructura = [[sorted({_norm_col(c) for c in cols if not str(c).startswith("_")}) for cols in grupo] for grupo in grupos_columnas]
    return hashlib.sha1(json.dumps(estructura, sort_keys=True).encode()).hexdigest()[:20]


def confianza(usado: int, corregido: int) -> int:
    """Proporción de usos sin corrección, suavizada (con pocos usos no llega a 100)."""
    return max(0, min(100, round(100 * (usado - corregido + 1) / (usado + 2))))


def config_equivalente(a: ConfigCruce, b: ConfigCruce) -> bool:
    """Igualdad de configuración ignorando nombres de archivo y de grupo (lo que el usuario puede renombrar)."""
    def limpia(c: ConfigCruce):
        d = c.model_dump()
        for g in d["grupos"]:
            g.pop("nombre", None)
            for a_ in g["archivos"]:
                a_.pop("nombre_original", None)
                a_.pop("hoja", None)
        return d
    return limpia(a) == limpia(b)


class Almacen:
    """Interfaz de almacenamiento de cruces, memoria de configuración y formatos aprendidos."""

    remoto = False

    # memoria de configuración
    def memoria(self, firma: str) -> Optional[Memoria]:
        return None

    def recordar(self, firma: str, config: ConfigCruce, corregida: bool, descripcion: str = "") -> Optional[Memoria]:
        return None

    def acepta_auto(self, mem: Memoria) -> bool:
        """¿El usuario actual aceptó que esta configuración aprendida se aplique sola?"""
        return False

    def fijar_auto(self, mem: Memoria, aceptar: bool) -> None:
        return None

    # métricas
    def registrar_evento(self, evento: dict) -> None:
        return None

    def eventos(self) -> list[dict]:
        return []

    def memorias(self) -> list[dict]:
        return []

    # formatos aprendidos (especificaciones)
    def formatos(self) -> dict[str, dict]:
        return {}

    # aprendizaje por uso de la llave: ejemplos etiquetados de cada cruce confirmado (ver regspec.llave_ml)
    def uso_llave(self) -> list[dict]:
        return []

    def registrar_uso_llave(self, ejemplo: dict) -> None:
        return None

    def guardar_formato(self, nombre: str, spec: dict, origen: str = "ia", tokens: int = 0, iteraciones: int = 0) -> None:
        return None

    def guardar(self, nombre: str, config: ConfigCruce, resumen: dict, archivos: list[str], excel: Optional[bytes],
                usuario: str = "local", notas: str = "", origen: str = "heuristica", aceptada: Optional[bool] = None,
                memoria_id: Optional[str] = None, llamadas_ia: int = 0, tokens_ia: int = 0) -> CruceGuardado:
        raise NotImplementedError

    def listar(self, usuario: Optional[str] = None) -> list[CruceGuardado]:
        raise NotImplementedError

    def obtener(self, id_: str) -> Optional[CruceGuardado]:
        raise NotImplementedError

    def excel(self, id_: str) -> Optional[bytes]:
        raise NotImplementedError

    def borrar(self, id_: str) -> bool:
        raise NotImplementedError


class AlmacenLocal(Almacen):
    def __init__(self, carpeta: Path | str, usuario: str = "local"):
        self.carpeta = Path(carpeta)
        self.usuario = usuario

    def _ruta(self, id_: str, ext: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", id_):
            raise ValueError("id inválido")
        return self.carpeta / f"{id_}.{ext}"

    def guardar(self, nombre, config, resumen, archivos, excel, usuario="local", notas="", origen="heuristica",
                aceptada=None, memoria_id=None):
        self.carpeta.mkdir(parents=True, exist_ok=True)
        c = CruceGuardado(id=uuid.uuid4().hex, nombre=nombre.strip() or "Cruce sin nombre", usuario=usuario,
                          fecha=datetime.now().isoformat(timespec="seconds"), config=config,
                          resumen={k: str(v) for k, v in resumen.items()}, archivos=archivos, notas=notas)
        self._ruta(c.id, "json").write_text(c.model_dump_json(indent=2), encoding="utf-8")
        if excel is not None:
            self._ruta(c.id, "xlsx").write_bytes(excel)
        return c

    # memoria local: un JSON con {firma: Memoria}
    def _memorias(self) -> dict:
        p = self.carpeta / "memoria.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception:  # noqa: BLE001
            return {}

    def memoria(self, firma):
        d = self._memorias().get(firma)
        return Memoria.model_validate(d) if d else None

    def recordar(self, firma, config, corregida, descripcion=""):
        todas = self._memorias()
        m = Memoria.model_validate(todas[firma]) if firma in todas else Memoria(id=firma, firma=firma, config=config)
        m.veces_usado += 1
        m.veces_corregido += int(bool(corregida))
        m.config, m.confianza = config, confianza(m.veces_usado, m.veces_corregido)
        m.descripcion = descripcion or m.descripcion
        todas[firma] = m.model_dump()
        self.carpeta.mkdir(parents=True, exist_ok=True)
        (self.carpeta / "memoria.json").write_text(json.dumps(todas, ensure_ascii=False, indent=1), encoding="utf-8")
        return m

    def _usuario(self):
        return getattr(self, "usuario", "local")

    def uso_llave(self):
        p = self.carpeta / "uso_llave.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except Exception:  # noqa: BLE001
            return []

    def registrar_uso_llave(self, ejemplo):
        from .llave_ml import MAX_CRUCES_USUARIO
        if not ejemplo.get("X"):
            return
        todos = (self.uso_llave() + [ejemplo])[-MAX_CRUCES_USUARIO:]
        self.carpeta.mkdir(parents=True, exist_ok=True)
        (self.carpeta / "uso_llave.json").write_text(json.dumps(todos), encoding="utf-8")

    def acepta_auto(self, mem):
        return self._usuario() in (mem.auto_usuarios or [])

    def fijar_auto(self, mem, aceptar):
        todas = self._memorias()
        if mem.firma not in todas:
            return
        m = Memoria.model_validate(todas[mem.firma])
        u = self._usuario()
        m.auto_usuarios = sorted(set(m.auto_usuarios) | {u}) if aceptar else [x for x in m.auto_usuarios if x != u]
        todas[mem.firma] = m.model_dump()
        (self.carpeta / "memoria.json").write_text(json.dumps(todas, ensure_ascii=False, indent=1), encoding="utf-8")

    def registrar_evento(self, evento):
        self.carpeta.mkdir(parents=True, exist_ok=True)
        e = {"created_at": datetime.now().isoformat(timespec="seconds"), "usuario": self._usuario(), **evento}
        with (self.carpeta / "eventos.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")

    def eventos(self):
        p = self.carpeta / "eventos.jsonl"
        if not p.exists():
            return []
        out = []
        for linea in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(linea))
            except Exception:  # noqa: BLE001
                continue
        return out

    def memorias(self):
        return [{"firma_archivos": m.get("firma"), "descripcion": m.get("descripcion", ""), "confianza": m.get("confianza"),
                 "veces_usado": m.get("veces_usado"), "veces_corregido": m.get("veces_corregido"),
                 "auto_aceptada": bool(m.get("auto_usuarios"))} for m in self._memorias().values()]

    def listar(self, usuario=None):
        if not self.carpeta.exists():
            return []
        out = []
        for p in self.carpeta.glob("*.json"):
            if p.name == "memoria.json":
                continue
            try:
                c = CruceGuardado.model_validate(json.loads(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                continue
            if usuario is None or c.usuario == usuario:
                out.append(c)
        return sorted(out, key=lambda c: c.fecha, reverse=True)

    def obtener(self, id_):
        p = self._ruta(id_, "json")
        return CruceGuardado.model_validate(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None

    def excel(self, id_):
        p = self._ruta(id_, "xlsx")
        return p.read_bytes() if p.exists() else None

    def borrar(self, id_):
        ok = False
        for ext in ("json", "xlsx"):
            p = self._ruta(id_, ext)
            if p.exists():
                p.unlink()
                ok = True
        return ok


class AlmacenSupabase(Almacen):
    """Cruces, memoria y formatos en Supabase. `cliente` es un cliente ya autenticado (los permisos por workspace
    los aplica la base con RLS). El Excel va al bucket `resultados`, en la carpeta del usuario."""

    remoto = True
    BUCKET = "resultados"

    def __init__(self, cliente, usuario_id: str, workspace_id: str, email: str = ""):
        self.c, self.uid, self.ws, self.email = cliente, usuario_id, workspace_id, email

    # cruces
    def _a_cruce(self, f: dict) -> CruceGuardado:
        return CruceGuardado(id=f["id"], nombre=f.get("nombre") or "", usuario=self.email if f.get("usuario_id") == self.uid else "",
                             fecha=(f.get("created_at") or "")[:19], config=ConfigCruce.model_validate(f["configuracion_cruce"]),
                             resumen=f.get("resumen_resultado") or {}, archivos=f.get("archivos") or [], notas=f.get("notas") or "")

    def guardar(self, nombre, config, resumen, archivos, excel, usuario="", notas="", origen="heuristica", aceptada=None,
                memoria_id=None, llamadas_ia=0, tokens_ia=0):
        fila = {"workspace_id": self.ws, "usuario_id": self.uid, "nombre": nombre.strip() or "Cruce sin nombre",
                "archivos": archivos, "configuracion_cruce": config.model_dump(), "notas": notas,
                "resumen_resultado": {k: str(v) for k, v in resumen.items()}, "origen_sugerencia": origen,
                "sugerencia_aceptada": aceptada, "memoria_id": memoria_id or None,
                "llamadas_ia": int(llamadas_ia or 0), "tokens_ia": int(tokens_ia or 0)}
        f = self.c.table("conciliacion").insert(fila).execute().data[0]
        if excel is not None:
            ruta = f"{self.uid}/{f['id']}.xlsx"
            self.c.storage.from_(self.BUCKET).upload(ruta, excel, {"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "upsert": "true"})
            self.c.table("conciliacion").update({"archivo_resultado_url": ruta}).eq("id", f["id"]).execute()
        return self._a_cruce(f)

    def listar(self, usuario=None):
        filas = self.c.table("conciliacion").select("*").eq("workspace_id", self.ws).order("created_at", desc=True).execute().data
        out = []
        for f in filas:
            try:
                out.append(self._a_cruce(f))
            except Exception:  # noqa: BLE001 (filas de otras versiones del esquema)
                continue
        return out

    def obtener(self, id_):
        filas = self.c.table("conciliacion").select("*").eq("id", id_).execute().data
        return self._a_cruce(filas[0]) if filas else None

    def excel(self, id_):
        filas = self.c.table("conciliacion").select("archivo_resultado_url").eq("id", id_).execute().data
        if not filas or not filas[0].get("archivo_resultado_url"):
            return None
        try:
            return self.c.storage.from_(self.BUCKET).download(filas[0]["archivo_resultado_url"])
        except Exception:  # noqa: BLE001
            return None

    def borrar(self, id_):
        filas = self.c.table("conciliacion").select("archivo_resultado_url").eq("id", id_).execute().data
        if filas and filas[0].get("archivo_resultado_url"):
            try:
                self.c.storage.from_(self.BUCKET).remove([filas[0]["archivo_resultado_url"]])
            except Exception:  # noqa: BLE001
                pass
        return bool(self.c.table("conciliacion").delete().eq("id", id_).execute().data)

    # memoria de configuración
    def _a_memoria(self, f: dict) -> Memoria:
        return Memoria(id=f["id"], firma=f["firma_archivos"], config=ConfigCruce.model_validate(f["configuracion_cruce"]),
                       confianza=f.get("confianza", 50), veces_usado=f.get("veces_usado", 0),
                       veces_corregido=f.get("veces_corregido", 0), descripcion=f.get("descripcion") or "")

    def memoria(self, firma):
        filas = self.c.table("memoria_conciliacion").select("*").eq("workspace_id", self.ws).eq("firma_archivos", firma).execute().data
        return self._a_memoria(filas[0]) if filas else None

    def recordar(self, firma, config, corregida, descripcion=""):
        previa = self.memoria(firma)
        usado = (previa.veces_usado if previa else 0) + 1
        corregido = (previa.veces_corregido if previa else 0) + int(bool(corregida))
        fila = {"workspace_id": self.ws, "firma_archivos": firma, "configuracion_cruce": config.model_dump(),
                "confianza": confianza(usado, corregido), "veces_usado": usado, "veces_corregido": corregido,
                "updated_at": datetime.utcnow().isoformat() + "Z"}
        if descripcion:
            fila["descripcion"] = descripcion
        f = self.c.table("memoria_conciliacion").upsert(fila, on_conflict="workspace_id,firma_archivos").execute().data[0]
        return self._a_memoria(f)

    def acepta_auto(self, mem):
        if not mem.id:
            return False
        return bool(self.c.table("memoria_autoaplicar").select("memoria_id").eq("memoria_id", mem.id)
                    .eq("usuario_id", self.uid).execute().data)

    def fijar_auto(self, mem, aceptar):
        if not mem.id:
            return
        if aceptar:
            self.c.table("memoria_autoaplicar").upsert({"memoria_id": mem.id, "usuario_id": self.uid},
                                                       on_conflict="memoria_id,usuario_id").execute()
        else:
            self.c.table("memoria_autoaplicar").delete().eq("memoria_id", mem.id).eq("usuario_id", self.uid).execute()

    # métricas (con RLS: cada una ve su workspace; las administradoras, todos)
    def registrar_evento(self, evento):
        self.c.table("evento_cruce").insert({"workspace_id": self.ws, "usuario_id": self.uid, **evento}).execute()

    def es_admin(self) -> bool:
        try:
            return bool(self.c.rpc("soy_admin").execute().data)
        except Exception:  # noqa: BLE001
            return False

    def _todo(self, tabla: str, columnas: str = "*") -> list[dict]:
        out, desde = [], 0
        while True:
            parte = self.c.table(tabla).select(columnas).order("created_at").range(desde, desde + 999).execute().data
            out += parte
            if len(parte) < 1000:
                return out
            desde += 1000

    def eventos(self):
        return self._todo("evento_cruce")

    def memorias(self):
        return self._todo("memoria_conciliacion", "workspace_id,firma_archivos,descripcion,confianza,veces_usado,veces_corregido,created_at,updated_at")

    # formatos aprendidos
    def formatos(self):
        filas = self.c.table("especificacion").select("nombre,spec").eq("workspace_id", self.ws).execute().data
        return {f["nombre"]: f["spec"] for f in filas if not str(f["nombre"]).startswith("__")}

    # ejemplos de uso de la llave: una fila reservada de «especificacion» por usuario (no requiere migración)
    def _nombre_uso(self):
        return f"__uso_llave__{self.uid}"

    def uso_llave(self):
        filas = (self.c.table("especificacion").select("spec").eq("workspace_id", self.ws)
                 .eq("nombre", self._nombre_uso()).execute().data)
        return (filas[0]["spec"] or {}).get("ejemplos", []) if filas else []

    def registrar_uso_llave(self, ejemplo):
        from .llave_ml import MAX_CRUCES_USUARIO
        if not ejemplo.get("X"):
            return
        todos = (self.uso_llave() + [ejemplo])[-MAX_CRUCES_USUARIO:]
        self.guardar_formato(self._nombre_uso(), {"ejemplos": todos}, origen="manual")

    def guardar_formato(self, nombre, spec, origen="ia", tokens=0, iteraciones=0):
        self.c.table("especificacion").upsert(
            {"workspace_id": self.ws, "nombre": nombre, "spec": spec, "origen": origen, "tokens_ia": int(tokens or 0),
             "iteraciones": int(iteraciones or 0), "updated_at": datetime.utcnow().isoformat() + "Z"},
            on_conflict="workspace_id,nombre").execute()
