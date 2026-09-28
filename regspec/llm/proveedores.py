"""Proveedores de LLM desacoplados del resto del sistema.

Cualquier modelo se usa a través de la interfaz `Proveedor.completar()`. Esto
permite cambiar de proveedor o de modelo (o correr uno local) sin tocar el
extractor, y mitiga la dependencia de un servicio externo.

- OpenAICompatible: Groq, Gemini, OpenAI, OpenRouter, Together, Ollama/LM Studio locales.
- Anthropic: API de mensajes de Anthropic.
- Guionado: devuelve respuestas predefinidas (tests y demos reproducibles).
"""
from __future__ import annotations

import json
import re
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class Respuesta:
    texto: str
    tokens_entrada: int = 0
    tokens_salida: int = 0
    latencia_s: float = 0.0
    modelo: str = ""


class Proveedor(Protocol):
    nombre: str
    modelo: str

    def completar(self, mensajes: list[dict], modo_json: bool = True, temperatura: float = 0.0,
                  max_tokens: int = 8000) -> Respuesta: ...


class ErrorProveedor(RuntimeError):
    def __init__(self, mensaje: str, cuerpo: Optional[dict] = None):
        super().__init__(mensaje)
        self.cuerpo = cuerpo


def _post(url: str, cuerpo: dict, headers: dict, reintentos: int = 8, timeout: int = 240) -> dict:
    datos = json.dumps(cuerpo).encode()
    espera = 5.0
    for intento in range(reintentos):
        req = urllib.request.Request(url, data=datos, headers={"Content-Type": "application/json", "User-Agent": "regspec/1.0", "Accept": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            crudo = e.read().decode(errors="replace")
            detalle = crudo[:500]
            try:
                cuerpo_err = json.loads(crudo)
            except ValueError:
                cuerpo_err = None
            # "Request too large" no es transitorio: reintentar no sirve
            demasiado_grande = "too large" in crudo.lower()
            ra = e.headers.get("retry-after")
            try:
                espera_srv = float(ra) if ra else None
            except ValueError:
                espera_srv = None
            # límite diario agotado (o espera muy larga): se informa en lugar de quedar esperando
            m = re.search(r"try again in ((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)", crudo)
            if m and m.group(1):
                partes = re.findall(r"([\d.]+)([hms])", m.group(1))
                espera_msg = sum(float(v) * {"h": 3600, "m": 60, "s": 1}[u] for v, u in partes)
                espera_srv = max(espera_srv or 0, espera_msg)
            if e.code == 429 and espera_srv and espera_srv > 120:
                cuando = f" Se libera en {m.group(1).rstrip('.')}." if m else ""
                raise ErrorProveedor(f"se agotó el límite diario de tokens del plan para este modelo.{cuando} "
                                     "Podés esperar o usar otro modelo con --modelo (cada modelo tiene su propio cupo).", cuerpo_err)
            if e.code in (429, 500, 502, 503, 529) and not demasiado_grande and intento < reintentos - 1:
                pausa = espera_srv if espera_srv else espera
                time.sleep(min(max(pausa, 1.0), 90.0))
                espera = min(espera * 2, 90.0)
                continue
            if demasiado_grande:
                raise ErrorProveedor(f"HTTP {e.code}: la solicitud supera el límite de tokens por minuto del plan ({detalle[:300]})", cuerpo_err)
            if e.code == 413:
                raise ErrorProveedor(f"HTTP 413: la solicitud supera el límite de tokens por minuto del plan ({detalle[:200]})", cuerpo_err)
            raise ErrorProveedor(f"HTTP {e.code}: {detalle}", cuerpo_err)
        except urllib.error.URLError as e:
            if intento < reintentos - 1:
                time.sleep(espera)
                espera *= 2
                continue
            raise ErrorProveedor(f"sin conexión: {e.reason}")
    raise ErrorProveedor("reintentos agotados")


@dataclass
class OpenAICompatible:
    modelo: str
    base_url: str = "https://api.groq.com/openai/v1"
    api_key: Optional[str] = None
    nombre: str = "openai-compatible"
    extra: dict = field(default_factory=dict)  # parámetros propios del proveedor (p. ej. reasoning_effort)

    def completar(self, mensajes, modo_json=True, temperatura=0.0, max_tokens=8000) -> Respuesta:
        cuerpo = {"model": self.modelo, "messages": mensajes, "temperature": temperatura, "max_tokens": max_tokens, **self.extra}
        if modo_json:
            cuerpo["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        t0 = time.time()
        try:
            r = _post(self.base_url.rstrip("/") + "/chat/completions", cuerpo, headers)
        except ErrorProveedor as e:
            # Groq rechaza con 400 las respuestas que no son JSON válido; se devuelve el texto
            # generado para que el bucle lo corrija como cualquier otro error.
            if "json_validate_failed" in str(e) and e.cuerpo:
                fallido = (e.cuerpo.get("error") or {}).get("failed_generation", "")
                return Respuesta(fallido, 0, len(fallido) // 4, time.time() - t0, self.modelo)
            raise
        uso = r.get("usage", {})
        return Respuesta(
            texto=r["choices"][0]["message"]["content"] or "",
            tokens_entrada=uso.get("prompt_tokens", 0),
            tokens_salida=uso.get("completion_tokens", 0),
            latencia_s=time.time() - t0,
            modelo=r.get("model", self.modelo),
        )


@dataclass
class Anthropic:
    modelo: str = "claude-haiku-4-5-20251001"
    api_key: Optional[str] = None
    nombre: str = "anthropic"

    def completar(self, mensajes, modo_json=True, temperatura=0.0, max_tokens=8000) -> Respuesta:
        sistema = "\n\n".join(m["content"] for m in mensajes if m["role"] == "system")
        conv = [m for m in mensajes if m["role"] != "system"]
        cuerpo = {"model": self.modelo, "system": sistema, "messages": conv, "temperature": temperatura, "max_tokens": max_tokens}
        headers = {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"}
        t0 = time.time()
        r = _post("https://api.anthropic.com/v1/messages", cuerpo, headers)
        texto = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
        uso = r.get("usage", {})
        return Respuesta(texto, uso.get("input_tokens", 0), uso.get("output_tokens", 0), time.time() - t0, r.get("model", self.modelo))


@dataclass
class Guionado:
    """Devuelve, en orden, respuestas predefinidas. Útil para tests y para demos sin API."""

    respuestas: list[str]
    modelo: str = "guionado"
    nombre: str = "guionado"
    recibidos: list[list[dict]] = field(default_factory=list)

    def completar(self, mensajes, modo_json=True, temperatura=0.0, max_tokens=8000) -> Respuesta:
        self.recibidos.append(mensajes)
        i = min(len(self.recibidos) - 1, len(self.respuestas) - 1)
        return Respuesta(self.respuestas[i], sum(len(m["content"]) for m in mensajes) // 4, len(self.respuestas[i]) // 4, 0.0, self.modelo)


PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "qwen/qwen3.8-27b:free"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-2.5-flash"),
    "ollama": ("http://localhost:11434/v1", None, "llama3.1:8b"),
}


def crear_proveedor(nombre: str, modelo: Optional[str] = None) -> Proveedor:
    """Crea un proveedor a partir de un nombre corto: groq, openai, openrouter, ollama o anthropic."""
    if nombre == "anthropic":
        return Anthropic(modelo=modelo or "claude-haiku-4-5-20251001", api_key=os.environ.get("ANTHROPIC_API_KEY"))
    if nombre not in PRESETS:
        raise ValueError(f"proveedor desconocido '{nombre}'. Opciones: {', '.join(list(PRESETS) + ['anthropic'])}")
    url, env, defecto = PRESETS[nombre]
    clave = os.environ.get(env) if env else None
    if env and not clave:
        raise ErrorProveedor(f"falta la variable de entorno {env}")
    modelo = modelo or defecto
    extra: dict = {}
    if "gpt-oss" in modelo and nombre in ("groq", "openrouter"):
        extra = {"reasoning_effort": "low"}  # menos razonamiento interno = menos tokens de salida
    if nombre == "openrouter":
        extra.setdefault("reasoning", {"effort": "low"})
        extra.pop("reasoning_effort", None)
    return OpenAICompatible(modelo=modelo, base_url=url, api_key=clave, nombre=nombre, extra=extra)
