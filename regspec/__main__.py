"""Interfaz de línea de comandos.

  python -m regspec extraer manuales/m2_cuentas.md --proveedor groq -o spec.json
  python -m regspec verificar spec.json --manual manuales/m2_cuentas.md
  python -m regspec muestra spec.json -o ejemplo.txt
  python -m regspec validar spec.json archivo.txt
  python -m regspec xsd spec.json -o esquema.xsd
  python -m regspec evaluar --proveedor groq --repeticiones 3
  python -m regspec evaluar --solo-sin-ia
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .archivos import escribir, generar_xsd
from .dsl import Especificacion
from .sintetico import generar_registros
from .validador import validar
from .verificador import verificar


def _leer_texto(ruta: str) -> str:
    p = Path(ruta)
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages)
    return p.read_text(encoding="utf-8")


def _spec(ruta: str) -> Especificacion:
    d = json.loads(Path(ruta).read_text(encoding="utf-8"))
    return Especificacion.model_validate(d.get("spec", d))


def _salida(texto: str, destino) -> None:
    if destino:
        Path(destino).write_text(texto, encoding="utf-8")
        print(f"escrito: {destino}", file=sys.stderr)
    else:
        print(texto)


def main(argv=None) -> int:
    from .llm.proveedores import ErrorProveedor

    try:
        return _main(argv)
    except ErrorProveedor as e:
        print(f"error del proveedor de LLM: {e}", file=sys.stderr)
        return 2


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="regspec", description="Compilador de especificaciones regulatorias con IA")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extraer", help="manual (md/txt/pdf) -> especificación verificada")
    e.add_argument("manual")
    e.add_argument("--proveedor", default="groq")
    e.add_argument("--modelo")
    e.add_argument("--max-iteraciones", type=int, default=4)
    e.add_argument("--sin-cache", action="store_true", help="no reutilizar respuestas guardadas del LLM")
    e.add_argument("--solo-ia", action="store_true", help="no usar el parser sin IA como primer paso (ablación)")
    e.add_argument("--modo", choices=["secciones", "completo"], default="secciones",
                   help="secciones: una llamada por parte (entra en planes gratuitos); completo: una sola respuesta")
    e.add_argument("--sin-ia", action="store_true", help="usar la línea base por reglas")
    e.add_argument("-o", "--salida")

    v = sub.add_parser("verificar", help="verifica una especificación")
    v.add_argument("spec")
    v.add_argument("--manual")

    m = sub.add_parser("muestra", help="genera un archivo de ejemplo válido")
    m.add_argument("spec")
    m.add_argument("--semilla", type=int, default=0)
    m.add_argument("-o", "--salida")

    va = sub.add_parser("validar", help="valida un archivo de datos contra la especificación")
    va.add_argument("spec")
    va.add_argument("archivo")

    x = sub.add_parser("xsd", help="genera el XSD de una especificación")
    x.add_argument("spec")
    x.add_argument("-o", "--salida")

    cr = sub.add_parser("cruzar", help="cruza dos archivos (CSV/Excel o TXT/XML con especificación), sin IA")
    cr.add_argument("archivo_a")
    cr.add_argument("archivo_b")
    cr.add_argument("--spec-a", help="especificación para leer A (si es TXT/XML)")
    cr.add_argument("--spec-b", help="especificación para leer B (si es TXT/XML)")
    cr.add_argument("--llave-a", help="columnas llave en A, separadas por coma (por defecto: sugerida)")
    cr.add_argument("--llave-b", help="columnas llave en B, separadas por coma")
    cr.add_argument("--comparar", help="pares a comparar 'colA=colB,colA2=colB2' (por defecto: sugeridos)")
    cr.add_argument("--tolerancia", default="0.01")
    cr.add_argument("-o", "--salida", default="cruce.xlsx")

    ev = sub.add_parser("evaluar", help="corre el banco de evaluación")
    ev.add_argument("--proveedor", default="groq")
    ev.add_argument("--modelo")
    ev.add_argument("--repeticiones", type=int, default=3)
    ev.add_argument("--condiciones", default="referencia,base_sin_ia,llm_1_intento,llm_bucle,llm_bucle_sin_evidencia,llm_bucle_sin_ejecucion")
    ev.add_argument("--manuales", help="ids separados por coma (por defecto todos)")
    ev.add_argument("--solo-sin-ia", action="store_true")
    ev.add_argument("--sin-cache", action="store_true")

    a = ap.parse_args(argv)

    if a.cmd == "extraer":
        manual = _leer_texto(a.manual)
        if a.sin_ia:
            from .base_sin_ia import extraer_base

            d = extraer_base(manual)
            res = verificar(d, manual)
            for p in res.problemas:
                print("  ", p.a_texto(), file=sys.stderr)
            _salida(json.dumps(d, ensure_ascii=False, indent=2), a.salida)
            return 0 if res.valida else 1
        from .llm.extractor import ConfigExtraccion, extraer
        from .llm.proveedores import crear_proveedor

        from .llm.cache import ProveedorConCache

        prov = ProveedorConCache(crear_proveedor(a.proveedor, a.modelo), usar=not a.sin_cache)

        def progreso(it):
            estado = "OK" if it.errores == 0 else f"{it.errores} errores {it.errores_por_etapa}"
            print(f"iteración {it.numero}: {estado} ({it.tokens_entrada}+{it.tokens_salida} tokens, {it.latencia_s:.1f}s)", file=sys.stderr)
            for p in it.problemas[:8]:
                print("    ", p, file=sys.stderr)

        def paso(p):
            estado = "ok" if p.ok else p.detalle
            costo = "sin IA" if "por código" in p.nombre else (
                "desde caché, 0 tokens gastados" if p.latencia_s == 0 else f"{p.tokens_entrada}+{p.tokens_salida} tokens")
            print(f"  paso {p.nombre}: {estado} ({costo}, {p.latencia_s:.1f}s)", file=sys.stderr)

        r = extraer(manual, prov, ConfigExtraccion(max_iteraciones=a.max_iteraciones, modo=a.modo, usar_codigo=not a.solo_ia),
                    al_iterar=progreso, al_paso=paso)
        if prov.aciertos:
            print(f"caché: {prov.aciertos} respuestas reutilizadas ({prov.tokens_ahorrados} tokens ahorrados)", file=sys.stderr)
        if r.error:
            print("error:", r.error, file=sys.stderr)
        if r.spec_dict is not None:
            _salida(json.dumps(r.spec_dict, ensure_ascii=False, indent=2), a.salida)
        print(f"especificación {'VÁLIDA' if r.valida else 'CON ERRORES'} en {len(r.iteraciones)} iteraciones", file=sys.stderr)
        return 0 if r.valida else 1

    if a.cmd == "verificar":
        d = json.loads(Path(a.spec).read_text(encoding="utf-8"))
        res = verificar(d.get("spec", d), _leer_texto(a.manual) if a.manual else None)
        for p in res.problemas:
            print(f"{p.severidad:<11} {p.a_texto()}")
        print("VÁLIDA" if res.valida else f"INVÁLIDA ({len(res.errores)} errores)")
        return 0 if res.valida else 1

    if a.cmd == "muestra":
        s = _spec(a.spec)
        regs, _ = generar_registros(s, a.semilla)
        _salida(escribir(s, regs), a.salida)
        return 0

    if a.cmd == "validar":
        s = _spec(a.spec)
        inf = validar(s, Path(a.archivo).read_text(encoding="utf-8"))
        print(inf.resumen(100))
        return 0 if inf.ok else 1

    if a.cmd == "xsd":
        _salida(generar_xsd(_spec(a.spec)), a.salida)
        return 0

    if a.cmd == "cruzar":
        from decimal import Decimal

        from .cruce import cruzar, sugerir_comparaciones, sugerir_llave, tabla_desde_archivo

        A = tabla_desde_archivo(Path(a.archivo_a).read_bytes(), a.archivo_a, _spec(a.spec_a) if a.spec_a else None)
        B = tabla_desde_archivo(Path(a.archivo_b).read_bytes(), a.archivo_b, _spec(a.spec_b) if a.spec_b else None)
        if a.llave_a and a.llave_b:
            la, lb = a.llave_a.split(","), a.llave_b.split(",")
        else:
            sug = sugerir_llave(A, B)
            if not sug:
                print("no se encontró una llave común; indicala con --llave-a y --llave-b", file=sys.stderr)
                return 1
            la, lb = [sug[0].col_a], [sug[0].col_b]
            print(f"llave sugerida: {la[0]} <-> {lb[0]} ({sug[0].motivo})", file=sys.stderr)
        comp = [tuple(x.split("=", 1)) for x in a.comparar.split(",")] if a.comparar else sugerir_comparaciones(A, B, la, lb)
        r = cruzar(A, B, la, lb, comp, Decimal(a.tolerancia))
        for k, v in r.resumen.items():
            print(f"{k:28s} {v}")
        Path(a.salida).write_bytes(r.a_excel())
        print(f"detalle en {a.salida}", file=sys.stderr)
        return 0

    if a.cmd == "evaluar":
        from .evaluacion.ejecutar import ejecutar

        conds = [c.strip() for c in a.condiciones.split(",") if c.strip()]
        prov = None
        if a.solo_sin_ia:
            conds = ["referencia", "base_sin_ia"]
        else:
            from .llm.proveedores import crear_proveedor

            prov = crear_proveedor(a.proveedor, a.modelo)
        out = ejecutar(conds, prov, a.repeticiones, manuales=a.manuales.split(",") if a.manuales else None,
                       usar_cache=not a.sin_cache)
        print((out / "resumen.md").read_text(encoding="utf-8"))
        print(f"resultados en {out}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
