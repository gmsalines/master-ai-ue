"""Caché de respuestas del LLM en disco.

La misma pregunta (mismo modelo, mismos mensajes, mismos parámetros) nunca se
paga dos veces: la respuesta se guarda y se reutiliza. Hace gratis repetir
pruebas y demos. Para medir variabilidad (repeticiones del banco de
evaluación) se desactiva con `usar=False` o `--sin-cache`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .proveedores import Proveedor, Respuesta

RUTA_DEFECTO = Path(".cache_llm")


@dataclass
class ProveedorConCache:
    interno: Proveedor
    ruta: Path = RUTA_DEFECTO
    usar: bool = True
    sal: str = ""  # distingue repeticiones de un experimento (misma pregunta, respuestas independientes)
    aciertos: int = 0
    fallos: int = 0
    tokens_ahorrados: int = field(default=0)

    @property
    def nombre(self) -> str:
        return getattr(self.interno, "nombre", "")

    @property
    def modelo(self) -> str:
        return getattr(self.interno, "modelo", "")

    def _clave(self, mensajes, modo_json, temperatura, max_tokens) -> str:
        crudo = json.dumps({"m": self.modelo, "x": getattr(self.interno, "extra", {}), "msg": mensajes,
                            "j": modo_json, "t": temperatura, "mt": max_tokens, "s": self.sal}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(crudo.encode()).hexdigest()[:32]

    def completar(self, mensajes, modo_json=True, temperatura=0.0, max_tokens=8000) -> Respuesta:
        archivo = self.ruta / f"{self._clave(mensajes, modo_json, temperatura, max_tokens)}.json"
        if self.usar and archivo.exists():
            d = json.loads(archivo.read_text(encoding="utf-8"))
            self.aciertos += 1
            self.tokens_ahorrados += d.get("tokens_entrada", 0) + d.get("tokens_salida", 0)
            # se informan los tokens originales (costo del método) con latencia 0: no se consumió cupo
            return Respuesta(d["texto"], d.get("tokens_entrada", 0), d.get("tokens_salida", 0), 0.0, d.get("modelo", self.modelo))
        r = self.interno.completar(mensajes, modo_json=modo_json, temperatura=temperatura, max_tokens=max_tokens)
        self.fallos += 1
        if self.usar and r.texto and _es_json(r.texto):  # no se guardan respuestas ilegibles
            self.ruta.mkdir(parents=True, exist_ok=True)
            archivo.write_text(json.dumps(r.__dict__, ensure_ascii=False), encoding="utf-8")
        return r


def _es_json(texto: str) -> bool:
    ini, fin = texto.find("{"), texto.rfind("}")
    if ini < 0 or fin < 0:
        return False
    try:
        json.loads(texto[ini : fin + 1])
        return True
    except ValueError:
        return False
