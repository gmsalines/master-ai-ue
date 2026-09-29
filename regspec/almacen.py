"""Cruces guardados: configuración reutilizable + resultado.

Una `ConfigCruce` describe cómo se armó un cruce (grupos, llaves, normalización, filtros, columnas traídas,
importes comparados y tolerancia) sin depender de los nombres de los archivos: al reutilizarla, se aplica a
los archivos nuevos por posición (grupo 1, archivo 1; grupo 1, archivo 2; ...). Así, el cruce de julio se
repite en agosto subiendo los archivos del mes y eligiendo la configuración guardada.

El almacenamiento está detrás de la interfaz `Almacen`; `AlmacenLocal` guarda en una carpeta (JSON + Excel).
Para una versión publicada con usuarios, basta con otra implementación (p. ej. base de datos + storage).
"""
from __future__ import annotations

import json
import re
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


class Almacen:
    """Interfaz de almacenamiento de cruces."""

    def guardar(self, nombre: str, config: ConfigCruce, resumen: dict, archivos: list[str], excel: Optional[bytes],
                usuario: str = "local", notas: str = "") -> CruceGuardado:
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
    def __init__(self, carpeta: Path | str):
        self.carpeta = Path(carpeta)

    def _ruta(self, id_: str, ext: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", id_):
            raise ValueError("id inválido")
        return self.carpeta / f"{id_}.{ext}"

    def guardar(self, nombre, config, resumen, archivos, excel, usuario="local", notas=""):
        self.carpeta.mkdir(parents=True, exist_ok=True)
        c = CruceGuardado(id=uuid.uuid4().hex, nombre=nombre.strip() or "Cruce sin nombre", usuario=usuario,
                          fecha=datetime.now().isoformat(timespec="seconds"), config=config,
                          resumen={k: str(v) for k, v in resumen.items()}, archivos=archivos, notas=notas)
        self._ruta(c.id, "json").write_text(c.model_dump_json(indent=2), encoding="utf-8")
        if excel is not None:
            self._ruta(c.id, "xlsx").write_bytes(excel)
        return c

    def listar(self, usuario=None):
        if not self.carpeta.exists():
            return []
        out = []
        for p in self.carpeta.glob("*.json"):
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
