"""Validación de un archivo de datos contra una especificación (control previo al envío)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from .archivos import generar_xsd, leer, validar_xsd
from .dsl import Especificacion
from .expr import ContextoArchivo, ErrorExpresion, Evaluador, Nulo


@dataclass
class Hallazgo:
    severidad: str  # "error" | "advertencia"
    origen: str  # "estructura" | "campo" | "ocurrencias" | "regla" | "xsd"
    mensaje: str
    linea: Optional[int] = None
    tipo_registro: Optional[str] = None
    campo: Optional[str] = None
    regla: Optional[str] = None


@dataclass
class Informe:
    hallazgos: list[Hallazgo] = field(default_factory=list)
    registros_leidos: int = 0

    @property
    def errores(self) -> list[Hallazgo]:
        return [h for h in self.hallazgos if h.severidad == "error"]

    @property
    def ok(self) -> bool:
        return not self.errores

    def reglas_violadas(self) -> set[str]:
        return {h.regla for h in self.errores if h.regla}

    def a_dicts(self) -> list[dict]:
        return [asdict(h) for h in self.hallazgos]

    def resumen(self, max_items: int = 20) -> str:
        if self.ok:
            return f"OK: {self.registros_leidos} registros, sin errores."
        out = [f"{len(self.errores)} errores en {self.registros_leidos} registros:"]
        for h in self.errores[:max_items]:
            loc = f"línea {h.linea}" if h.linea else "archivo"
            extra = " ".join(x for x in [h.tipo_registro and f"[{h.tipo_registro}]", h.campo and f"campo {h.campo}", h.regla and f"regla {h.regla}"] if x)
            out.append(f"  - {loc} {extra}: {h.mensaje}")
        if len(self.errores) > max_items:
            out.append(f"  ... y {len(self.errores) - max_items} más")
        return "\n".join(out)


def validar(spec: Especificacion, contenido: str, usar_xsd: bool = True) -> Informe:
    inf = Informe()
    if spec.formato == "xml" and usar_xsd:
        for linea, msg in validar_xsd(generar_xsd(spec), contenido):
            inf.hallazgos.append(Hallazgo("error", "xsd", msg, linea=linea))

    registros, estructura = leer(spec, contenido)
    inf.registros_leidos = len(registros) + len(estructura)
    for linea, msg in estructura:
        inf.hallazgos.append(Hallazgo("error", "estructura", msg, linea=linea))
    for r in registros:
        for campo, msg in r.errores:
            inf.hallazgos.append(Hallazgo("error", "campo", msg, linea=r.linea, tipo_registro=r.codigo, campo=campo))

    # ocurrencias y posición
    por_tipo: dict[str, list] = {}
    for r in registros:
        por_tipo.setdefault(r.codigo, []).append(r)
    for tr in spec.tipos_registro:
        n = len(por_tipo.get(tr.codigo, []))
        if n < tr.min_ocurrencias:
            inf.hallazgos.append(Hallazgo("error", "ocurrencias", f"se esperaban al menos {tr.min_ocurrencias} registros '{tr.codigo}' y hay {n}", tipo_registro=tr.codigo))
        if tr.max_ocurrencias is not None and n > tr.max_ocurrencias:
            inf.hallazgos.append(Hallazgo("error", "ocurrencias", f"se esperaban como máximo {tr.max_ocurrencias} registros '{tr.codigo}' y hay {n}", tipo_registro=tr.codigo))
        if registros and n:
            if tr.posicion == "primero" and registros[0].codigo != tr.codigo:
                inf.hallazgos.append(Hallazgo("error", "ocurrencias", f"el registro '{tr.codigo}' debe ser el primero del archivo", tipo_registro=tr.codigo))
            if tr.posicion == "ultimo" and registros[-1].codigo != tr.codigo:
                inf.hallazgos.append(Hallazgo("error", "ocurrencias", f"el registro '{tr.codigo}' debe ser el último del archivo", tipo_registro=tr.codigo))

    # reglas
    ctx = ContextoArchivo({k: [r.valores for r in v] for k, v in por_tipo.items()})
    for regla in spec.reglas:
        try:
            if regla.ambito == "registro":
                for r in por_tipo.get(regla.tipo_registro, []):
                    campos_con_error = {c for c, _ in r.errores}
                    try:
                        ok = Evaluador(ctx, r.valores).evaluar(regla.expresion)
                    except Nulo as nulo:
                        if str(nulo) in campos_con_error:
                            continue  # ya informado como error de campo
                        continue  # dato opcional no informado: la regla no aplica
                    if not ok:
                        inf.hallazgos.append(Hallazgo("error", "regla", regla.descripcion, linea=r.linea, tipo_registro=r.codigo, regla=regla.id))
            else:
                try:
                    ok = Evaluador(ctx).evaluar(regla.expresion)
                except Nulo:
                    continue
                if not ok:
                    inf.hallazgos.append(Hallazgo("error", "regla", regla.descripcion, regla=regla.id))
        except ErrorExpresion as e:
            inf.hallazgos.append(Hallazgo("advertencia", "regla", f"regla no evaluable: {e.mensaje}", regla=regla.id))
        except (TypeError, KeyError) as e:
            inf.hallazgos.append(Hallazgo("advertencia", "regla", f"regla no evaluable: {e}", regla=regla.id))
    return inf
