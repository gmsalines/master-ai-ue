"""Banco de evaluación de la conciliación (flujo 2).

Genera casos sintéticos con resultado de referencia conocido y mide, sin gastar tokens:

  E2  motor de conciliación: precisión y exhaustividad por estado de cada llave (iguales, con diferencias,
      solo en A, solo en B) con la configuración correcta, y ablación de cada tipo de coincidencia
      (transformación, tolerancia, exclusión) para mostrar qué errores aparecen si falta.
  E3  sugerencia de llave por código: acierto de la columna y de la normalización propuestas.
  E3b sugerencia de llave con LLM (opcional, requiere proveedor): tasa de columnas inventadas antes del
      filtro, llaves válidas y acierto, comparando «solo nombres de columna» con «nombres + 5 ejemplos».
  E4  memoria de configuración: intervenciones manuales y confianza a lo largo de ejecuciones sucesivas,
      incluida una corrección y un cambio de estructura.
  E5  rendimiento: tiempo de sugerencia de llave y de cruce según el volumen.

Diseño de los casos: 4 escenarios de conciliación × 3 variantes de formato (idioma de las columnas y formato
de números y llaves de distintos países) × 2 niveles de dificultad (nombres informativos / nombres genéricos y
una columna señuelo) = 24 casos. Cada caso incluye coincidencias exactas, diferencias de redondeo dentro de la
tolerancia, pagos parciales (diferencias reales), pagos en dos cuotas que suman el total, registros sin
contrapartida en cada lado y registros anulados que deben excluirse.

Uso:
    python -m regspec.evaluacion.conciliacion -o resultados/eval_conciliacion
    python -m regspec.evaluacion.conciliacion -o resultados/eval_conciliacion --llm groq --modelo openai/gpt-oss-20b
"""
from __future__ import annotations

import argparse
import json
import random
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ..almacen import AlmacenLocal, ArchivoCfg, ConfigCruce, FiltroCfg, GrupoCfg, config_equivalente, firma_archivos
from ..grupos import SOLIDEZ_MINIMA, ArchivoGrupo, Filtro, Grupo, cruzar_grupos, sugerir_llaves, transformar_serie

# ------------------------------------------------------------------ definición de escenarios y variantes

# nombres de columna por escenario y variante: (A: llave, importe, fecha, texto, id señuelo) (B: ídem + estado)
ESCENARIOS = {
    "cobros": {
        "es": (["Nro_Factura", "Importe", "Fecha_Emision", "Cliente", "Id_Cliente"],
               ["Comprobante", "Monto_Cobrado", "Fecha_Cobro", "Pagador", "Id_Pagador", "Estado"]),
        "pt": (["Numero_Nota", "Valor", "Data_Emissao", "Cliente", "Cod_Cliente"],
               ["Documento", "Valor_Recebido", "Data_Recebimento", "Pagador", "Cod_Pagador", "Situacao"]),
        "en": (["Invoice_No", "Amount", "Issue_Date", "Customer", "Customer_Id"],
               ["Reference", "Amount_Received", "Receipt_Date", "Payer", "Payer_Id", "Status"]),
    },
    "pagos": {
        "es": (["N_Documento", "Total", "Vencimiento", "Proveedor", "Cod_Proveedor"],
               ["Ref_Pago", "Importe_Pagado", "Fecha_Pago", "Beneficiario", "Cod_Benef", "Estado"]),
        "pt": (["Num_Titulo", "Valor_Total", "Vencimento", "Fornecedor", "Cod_Fornecedor"],
               ["Ref_Pagamento", "Valor_Pago", "Data_Pagamento", "Favorecido", "Cod_Favorecido", "Situacao"]),
        "en": (["Bill_Number", "Total", "Due_Date", "Vendor", "Vendor_Id"],
               ["Payment_Ref", "Paid_Amount", "Payment_Date", "Beneficiary", "Beneficiary_Id", "Status"]),
    },
    "banco": {
        "es": (["Id_Transaccion", "Monto", "Fecha", "Concepto", "Nro_Cuenta"],
               ["Referencia", "Importe", "Fecha_Valor", "Descripcion", "Sucursal", "Estado"]),
        "pt": (["Id_Transacao", "Valor", "Data", "Historico", "Num_Conta"],
               ["Referencia", "Valor_Lancamento", "Data_Valor", "Descricao", "Agencia", "Situacao"]),
        "en": (["Transaction_Id", "Amount", "Date", "Memo", "Account_No"],
               ["Bank_Reference", "Value", "Value_Date", "Description", "Branch", "Status"]),
    },
    "proveedor": {
        "es": (["Numero_Doc", "Importe_Total", "Fecha", "Razon_Social", "Id_Tercero"],
               ["Factura_Proveedor", "Saldo", "Fecha_Doc", "Emisor", "Id_Emisor", "Estado"]),
        "pt": (["Numero_Doc", "Valor_Total", "Data", "Razao_Social", "Id_Terceiro"],
               ["Nota_Fornecedor", "Saldo", "Data_Doc", "Emitente", "Id_Emitente", "Situacao"]),
        "en": (["Document_No", "Total_Amount", "Date", "Company", "Party_Id"],
               ["Supplier_Invoice", "Balance", "Doc_Date", "Issuer", "Issuer_Id", "Status"]),
    },
}
ANULADO = {"es": "ANULADO", "pt": "CANCELADO", "en": "VOID"}
# formato de la llave en B respecto de A y transformación correcta
FORMATO_LLAVE = {"es": ("igual", "exacta"), "pt": ("con_guiones", "solo_numeros"), "en": ("ceros_izquierda", "solo_numeros")}
N_BASE = 400


def _fmt_importe(x: float, variante: str) -> str:
    s = f"{x:,.2f}"  # 1,234.56
    if variante in ("es", "pt"):
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")  # 1.234,56
    return ("R$ " + s) if variante == "pt" else s


def _fmt_fecha(d: pd.Timestamp, variante: str) -> str:
    return d.strftime("%m/%d/%Y") if variante == "en" else d.strftime("%d/%m/%Y")


def _fmt_llave_b(k: int, formato: str) -> str:
    if formato == "con_guiones":
        s = f"{k:012d}"
        return f"{s[:4]}-{s[4:]}"
    if formato == "ceros_izquierda":
        return f"{k:014d}"
    return str(k)


@dataclass
class Caso:
    id: str
    escenario: str
    variante: str
    dificultad: str
    a: pd.DataFrame
    b: pd.DataFrame
    llave_a: str
    llave_b: str
    importe_a: str
    importe_b: str
    estado_b: str
    anulado: str
    transformacion: str
    verdad: dict  # llave normalizada -> estado esperado


def generar_caso(escenario: str, variante: str, dificultad: str, semilla: int, n: int = N_BASE) -> Caso:
    rng = random.Random(semilla)
    cols_a, cols_b = ESCENARIOS[escenario][variante]
    formato, tr = FORMATO_LLAVE[variante]
    base = rng.randint(10_000_000, 90_000_000)
    llaves = rng.sample(range(base, base + n * 50), int(n * 1.2))  # llaves únicas
    n_iguales, n_redondeo, n_parcial, n_cuotas, n_solo_a, n_solo_b, n_anulados = (
        int(n * .55), int(n * .10), int(n * .10), int(n * .05), int(n * .10), int(n * .10), int(n * .05))
    idx = 0

    def tomar(k):
        nonlocal idx
        out = llaves[idx: idx + k]
        idx += k
        return out

    grupos = {"iguales": tomar(n_iguales), "redondeo": tomar(n_redondeo), "parcial": tomar(n_parcial),
              "cuotas": tomar(n_cuotas), "solo_a": tomar(n_solo_a), "solo_b": tomar(n_solo_b)}
    anulados = grupos["solo_a"][:n_anulados]  # en B aparecen, pero anulados: deben excluirse y quedar «solo en A»
    fecha0 = pd.Timestamp("2026-07-01")
    filas_a, filas_b, verdad = [], [], {}
    ids_cliente = [rng.randint(1, 999_999) for _ in range(60)]

    def fila_a(k, imp):
        return [str(k), imp, fecha0 + pd.Timedelta(days=rng.randint(0, 29)), f"Tercero {rng.randint(1, 60)}",
                rng.choice(ids_cliente)]

    for g, ks in grupos.items():
        for k in ks:
            imp = round(rng.uniform(50, 25_000), 2)
            fecha_b = fecha0 + pd.Timedelta(days=rng.randint(0, 40))
            if g != "solo_b":
                filas_a.append(fila_a(k, imp))
            if g == "iguales":
                filas_b.append([k, imp, fecha_b, "OK"])
                verdad[k] = "iguales"
            elif g == "redondeo":
                filas_b.append([k, round(imp + rng.choice([-0.01, 0.01]), 2), fecha_b, "OK"])
                verdad[k] = "iguales"
            elif g == "parcial":
                filas_b.append([k, round(imp * rng.uniform(0.3, 0.8), 2), fecha_b, "OK"])
                verdad[k] = "diferencias"
            elif g == "cuotas":
                p1 = round(imp * 0.5, 2)
                filas_b.append([k, p1, fecha_b, "OK"])
                filas_b.append([k, round(imp - p1, 2), fecha_b + pd.Timedelta(days=15), "OK"])
                verdad[k] = "iguales"
            elif g == "solo_a":
                verdad[k] = "solo_a"
                if k in anulados:
                    filas_b.append([k, imp, fecha_b, ANULADO[variante]])
            elif g == "solo_b":
                filas_b.append([k, imp, fecha_b, "OK"])
                verdad[k] = "solo_b"
    rng.shuffle(filas_a)
    rng.shuffle(filas_b)
    a = pd.DataFrame({
        cols_a[0]: [f[0] for f in filas_a],
        cols_a[1]: [_fmt_importe(f[1], variante) for f in filas_a],
        cols_a[2]: [_fmt_fecha(f[2], variante) for f in filas_a],
        cols_a[3]: [f[3] for f in filas_a],
        cols_a[4]: [str(f[4]) for f in filas_a],
    })
    b = pd.DataFrame({
        cols_b[0]: [_fmt_llave_b(f[0], formato) for f in filas_b],
        cols_b[1]: [_fmt_importe(f[1], variante) for f in filas_b],
        cols_b[2]: [_fmt_fecha(f[2], variante) for f in filas_b],
        cols_b[3]: [f"Tercero {rng.randint(1, 60)}" for _ in filas_b],
        cols_b[4]: [str(rng.choice(ids_cliente)) for _ in filas_b],
        cols_b[5]: [f[3] for f in filas_b],
    })
    if dificultad == "dificil":
        # nombres genéricos (sin pistas semánticas) y columnas en otro orden; en B la llave no es la primera
        a.columns = [f"Col{i + 1}" for i in range(a.shape[1])]
        b = b[[b.columns[i] for i in (4, 1, 0, 2, 3, 5)]]
        b.columns = [f"Campo_{c}" for c in "ABCDEF"]
        llave_a, imp_a = "Col1", "Col2"
        llave_b, imp_b, estado_b = "Campo_C", "Campo_B", "Campo_F"
    else:
        llave_a, imp_a = cols_a[0], cols_a[1]
        llave_b, imp_b, estado_b = cols_b[0], cols_b[1], cols_b[5]
    ks = list(verdad)
    verdad_norm = dict(zip(transformar_serie(pd.Series([str(k) for k in ks]), tr), (verdad[k] for k in ks)))
    return Caso(f"{escenario}-{variante}-{dificultad}", escenario, variante, dificultad, a, b, llave_a, llave_b,
                imp_a, imp_b, estado_b, ANULADO[variante], tr, verdad_norm)


def casos(semilla: int = 7) -> list[Caso]:
    out, s = [], semilla
    for esc in ESCENARIOS:
        for var in ("es", "pt", "en"):
            for dif in ("estandar", "dificil"):
                out.append(generar_caso(esc, var, dif, s))
                s += 1
    return out


# ------------------------------------------------------------------ E2: motor de conciliación


def _cruzar(c: Caso, transformacion: Optional[str] = None, tolerancia: str = "0.01", excluir: bool = True):
    tr = transformacion or c.transformacion
    filtros = [Filtro(c.estado_b, "==", c.anulado, "excluir")] if excluir else []
    ga = Grupo("A", [ArchivoGrupo("a", c.a, [c.llave_a], tr)])
    gb = Grupo("B", [ArchivoGrupo("b", c.b, [c.llave_b], tr, filtros)])
    return cruzar_grupos([ga, gb], [(c.importe_a, c.importe_b)], Decimal(tolerancia))


def _estados_obtenidos(r) -> dict:
    m = r.matriz
    est = np.where(m["en todos"] & m["con diferencias"], "diferencias",
                   np.where(m["en todos"], "iguales", np.where(m["en A"], "solo_a", "solo_b")))
    return dict(zip(m["llave"], est))


CLASES = ["iguales", "diferencias", "solo_a", "solo_b"]


def metricas_estados(verdad: dict, obtenido: dict) -> dict:
    """Precisión y exhaustividad por clase sobre el universo de llaves verdaderas ∪ obtenidas."""
    llaves = set(verdad) | set(obtenido)
    out = {}
    aciertos = sum(1 for k in llaves if verdad.get(k) == obtenido.get(k))
    out["exactitud"] = aciertos / max(1, len(llaves))
    for cl in CLASES:
        tp = sum(1 for k in llaves if verdad.get(k) == cl and obtenido.get(k) == cl)
        fp = sum(1 for k in llaves if verdad.get(k) != cl and obtenido.get(k) == cl)
        fn = sum(1 for k in llaves if verdad.get(k) == cl and obtenido.get(k) != cl)
        out[f"prec_{cl}"] = tp / (tp + fp) if tp + fp else 1.0
        out[f"exh_{cl}"] = tp / (tp + fn) if tp + fn else 1.0
    out["llaves_verdad"], out["llaves_obtenidas"] = len(verdad), len(obtenido)
    return out


def e2_motor(cs: list[Caso]) -> pd.DataFrame:
    filas = []
    for c in cs:
        condiciones = {"completa": {}, "sin_transformacion": {"transformacion": "exacta"},
                       "sin_tolerancia": {"tolerancia": "0"}, "sin_exclusion": {"excluir": False}}
        for nombre, kw in condiciones.items():
            if nombre == "sin_transformacion" and c.transformacion == "exacta":
                continue  # en esta variante la llave ya coincide: la ablación no aplica
            m = metricas_estados(c.verdad, _estados_obtenidos(_cruzar(c, **kw)))
            filas.append({"caso": c.id, "escenario": c.escenario, "variante": c.variante, "dificultad": c.dificultad,
                          "condicion": nombre, **m})
    return pd.DataFrame(filas)


# ------------------------------------------------------------------ E3: sugerencia de llave por código


def _misma_llave(c: Caso, col_a, col_b, tr) -> bool:
    """La sugerencia es correcta si propone las columnas correctas y una normalización que hace coincidir las llaves."""
    if col_a != c.llave_a or col_b != c.llave_b:
        return False
    ka = set(transformar_serie(c.a[c.llave_a], tr))
    kb = set(transformar_serie(c.b[c.llave_b], tr))
    return len(ka & kb) / max(1, min(len(ka), len(kb))) >= 0.5


def e3_llave_codigo(cs: list[Caso]) -> pd.DataFrame:
    filas = []
    for c in cs:
        t0 = time.perf_counter()
        s = sugerir_llaves([c.a, c.b])
        ms = (time.perf_counter() - t0) * 1000
        (ca, tra, _), (cb, trb, cob) = s[0], s[1]
        filas.append({"caso": c.id, "escenario": c.escenario, "variante": c.variante, "dificultad": c.dificultad,
                      "llave_a_sugerida": ca, "llave_b_sugerida": cb, "normalizacion": trb, "cobertura": round(cob, 3),
                      "llave_clara": cob >= SOLIDEZ_MINIMA, "correcta": _misma_llave(c, ca, cb, trb),
                      "llave_a_correcta": c.llave_a, "llave_b_correcta": c.llave_b, "ms": round(ms, 1)})
    return pd.DataFrame(filas)


# ------------------------------------------------------------------ E3b: sugerencia de llave con LLM


def e3_llave_llm(cs: list[Caso], proveedor, muestras_opciones=(0, 5)) -> pd.DataFrame:
    from ..cruce import consultar_llave_ia, validar_llave_ia
    filas = []
    for c in cs:
        for m in muestras_opciones:
            t0 = time.perf_counter()
            try:
                q = consultar_llave_ia(c.a, c.b, proveedor, muestras=m)
                error = ""
            except Exception as e:  # noqa: BLE001 (cupo agotado, red, etc.)
                q, error = {"respuesta": {}, "tokens": 0}, str(e)[:200]
            seg = time.perf_counter() - t0
            d = q["respuesta"]
            v = validar_llave_ia(c.a, c.b, d)
            propuestas = len(d.get("llave_a") or []) + len(d.get("llave_b") or []) + 2 * len(d.get("comparar") or [])
            correcta = v["valida"] and v["llave_a"] == [c.llave_a] and v["llave_b"] == [c.llave_b]
            filas.append({"caso": c.id, "escenario": c.escenario, "variante": c.variante, "dificultad": c.dificultad,
                          "muestras": m, "respondio_json": bool(d), "columnas_propuestas": propuestas,
                          "columnas_inventadas": v["inventadas"], "llave_valida": v["valida"], "correcta": correcta,
                          "plausible": v["plausible"], "solape": round(v["solape"], 3), "solidez": round(v["solidez"], 3),
                          "tokens": q["tokens"], "segundos": round(seg, 2), "fallo_cupo": "límite" in error or "429" in error,
                          "llave_a_ia": "|".join(v["llave_a"]), "llave_b_ia": "|".join(v["llave_b"]), "error": error})
    return pd.DataFrame(filas)


# ------------------------------------------------------------------ E4: memoria de configuración


def _config_correcta(c: Caso, tolerancia: str = "0.01", anulado: Optional[str] = None) -> ConfigCruce:
    return ConfigCruce(grupos=[
        GrupoCfg(nombre="A", archivos=[ArchivoCfg(llave=[c.llave_a], transformacion=c.transformacion)]),
        GrupoCfg(nombre="B", archivos=[ArchivoCfg(llave=[c.llave_b], transformacion=c.transformacion,
                                                  filtros=[FiltroCfg(columna=c.estado_b, operador="==",
                                                                     valor=anulado or c.anulado)])])],
        comparaciones=[[c.importe_a, c.importe_b]], tolerancia=tolerancia)


def _decisiones(cfg: ConfigCruce) -> int:
    """Decisiones de configuración que el usuario toma a mano cuando no hay memoria: llave y normalización de cada
    archivo, cada filtro, cada par de importes y la tolerancia."""
    n = 0
    for g in cfg.grupos:
        for a in g.archivos:
            n += 2 + len(a.filtros)
    return n + len(cfg.comparaciones) + 1


def e4_memoria(ejecuciones: int = 25, umbral: int = 95, correccion_en: Optional[int] = 8,
               estructura_en: Optional[int] = 16, semilla: int = 100) -> pd.DataFrame:
    """Simula períodos sucesivos del mismo proceso (archivos nuevos cada mes, misma estructura) con el almacén real.

    Reglas de la app: sin memoria, el usuario configura todo (se descuentan las decisiones que el código sugiere
    bien: llave y normalización); con memoria y confianza <= umbral, se ofrece la configuración y aceptarla es 1
    intervención; con confianza > umbral, se aplica sola (0 intervenciones). En `correccion_en` el proceso cambia
    (se agrega una exclusión) y el usuario corrige; en `estructura_en` cambia el nombre de una columna del archivo B
    (nueva estructura: la memoria no se reutiliza).
    """
    filas = []
    with tempfile.TemporaryDirectory() as tmp:
        alm = AlmacenLocal(Path(tmp))
        for i in range(1, ejecuciones + 1):
            c = generar_caso("cobros", "es", "estandar", semilla + i)
            if estructura_en and i >= estructura_en:
                c.b = c.b.rename(columns={"Fecha_Cobro": "Fecha_Acreditacion"})
            firma = firma_archivos([[list(c.a.columns)], [list(c.b.columns)]])
            cfg_ok = _config_correcta(c)
            if correccion_en and i >= correccion_en:  # desde este período también se excluyen los cobros «RECHAZADO»
                cfg_ok.grupos[1].archivos[0].filtros.append(FiltroCfg(columna=c.estado_b, operador="==", valor="RECHAZADO"))
            mem = alm.memoria(firma)
            conf_antes = mem.confianza if mem else None
            if mem is None:
                s = sugerir_llaves([c.a, c.b])
                bien = sum(2 for (col, tr, _), arch in zip(s, (c.llave_a, c.llave_b)) if col == arch and tr == c.transformacion)
                intervenciones, modo, corregida = _decisiones(cfg_ok) - bien, "sin memoria (configuración manual)", False
            else:
                corregida = not config_equivalente(mem.config, cfg_ok)
                automatica = mem.confianza > umbral
                modo = "automática" if automatica else "ofrecida"
                intervenciones = (0 if automatica else 1)
                if corregida:  # el usuario ajusta lo que cambió
                    intervenciones += 1
                    modo += " + corrección"
            m = alm.recordar(firma, cfg_ok, corregida)
            filas.append({"ejecucion": i, "modo": modo, "intervenciones": intervenciones,
                          "decisiones_sin_memoria": _decisiones(cfg_ok), "confianza_antes": conf_antes,
                          "confianza_despues": m.confianza, "veces_usado": m.veces_usado,
                          "veces_corregido": m.veces_corregido, "firma": firma[:8]})
    return pd.DataFrame(filas)


# ------------------------------------------------------------------ E5: rendimiento


def e5_rendimiento(tamanos=(1_000, 10_000, 100_000, 1_000_000)) -> pd.DataFrame:
    filas = []
    for n in tamanos:
        c = generar_caso("banco", "pt", "estandar", 999, n=n)
        t0 = time.perf_counter()
        sugerir_llaves([c.a, c.b])
        t1 = time.perf_counter()
        r = _cruzar(c)
        t2 = time.perf_counter()
        m = metricas_estados(c.verdad, _estados_obtenidos(r))
        filas.append({"registros_a": len(c.a), "registros_b": len(c.b), "seg_sugerir_llave": round(t1 - t0, 2),
                      "seg_cruce": round(t2 - t1, 2), "exactitud": m["exactitud"]})
    return pd.DataFrame(filas)


# ------------------------------------------------------------------ E6: configuración desde una instrucción en texto

_TEXTO_SEMANTICO = {
    "cobros": {"es": "Quiero conciliar las facturas con los cobros por número de comprobante. Sacá los cobros anulados y compará los importes{tol}.",
               "pt": "Quero conciliar as notas com os recebimentos pelo número do documento. Retire os recebimentos cancelados e compare os valores{tol}."},
    "pagos": {"es": "Cruzá las cuentas por pagar con los pagos realizados por número de documento, sin los pagos anulados, comparando los totales{tol}.",
              "pt": "Cruze os títulos a pagar com os pagamentos pelo número do título, sem os pagamentos cancelados, comparando os valores{tol}."},
    "banco": {"es": "Conciliá el sistema con el extracto bancario por el identificador de la transacción; ignorá los movimientos anulados y compará los montos{tol}.",
              "pt": "Concilie o sistema com o extrato bancário pelo identificador da transação; ignore os lançamentos cancelados e compare os valores{tol}."},
    "proveedor": {"es": "Compará nuestros documentos con las facturas del proveedor por número de documento, descartando las anuladas y comparando importes{tol}.",
                  "pt": "Compare nossos documentos com as notas do fornecedor pelo número do documento, descartando as canceladas e comparando os valores{tol}."},
}
_TEXTO_EXPLICITO = {
    "es": "Cruzá {la} del archivo 1 con {lb} del archivo 2, excluí las filas con {est} igual a {anu} y compará {ia} con {ib} con tolerancia {tol}.",
    "pt": "Cruze {la} do arquivo 1 com {lb} do arquivo 2, exclua as linhas com {est} igual a {anu} e compare {ia} com {ib} com tolerância {tol}.",
}
_TOL_TXT = {("es", "0.01"): " (se aceptan diferencias de un centavo)", ("es", "1"): " con una tolerancia de 1 peso",
            ("pt", "0.01"): " (aceitando diferenças de um centavo)", ("pt", "1"): " com tolerância de 1 real"}


def casos_texto(semilla: int = 2000) -> list[tuple[Caso, str, str, str]]:
    """12 casos (4 escenarios × 3 variantes, dificultad alterna) × 2 instrucciones (explícita y semántica)."""
    out, k = [], 0
    for esc in ESCENARIOS:
        for var in ("es", "pt", "en"):
            dif = "estandar" if k % 2 == 0 else "dificil"
            c = generar_caso(esc, var, dif, semilla + k)
            idioma = "pt" if var == "pt" else "es"
            tol = "1" if k % 2 == 0 else "0.01"
            out.append((c, "explicita", _TEXTO_EXPLICITO[idioma].format(
                la=c.llave_a, lb=c.llave_b, est=c.estado_b, anu=c.anulado, ia=c.importe_a, ib=c.importe_b,
                tol=tol.replace(".", ",")), tol))
            out.append((c, "semantica", _TEXTO_SEMANTICO[esc][idioma].format(tol=_TOL_TXT[(idioma, tol)]), tol))
            k += 1
    return out


def _cruzar_config(c: Caso, cfg) -> dict:
    grupos = []
    for g, df in zip(cfg.grupos, (c.a, c.b)):
        ac = g.archivos[0]
        filtros = [Filtro(f.columna, f.operador, f.valor, f.accion, f.valores) for f in ac.filtros]
        grupos.append(Grupo(g.nombre, [ArchivoGrupo(g.nombre, df, ac.llave, ac.transformacion, filtros)]))
    comp = [tuple(x) for x in cfg.comparaciones]
    r = cruzar_grupos(grupos, comp, Decimal(cfg.tolerancia))
    m = r.matriz
    en_a, en_b = f"en {cfg.grupos[0].nombre}", f"en {cfg.grupos[1].nombre}"
    est = np.where(m["en todos"] & m["con diferencias"], "diferencias",
                   np.where(m["en todos"], "iguales", np.where(m[en_a], "solo_a", "solo_b")))
    return metricas_estados(c.verdad, dict(zip(m["llave"], est)))


def _partes_correctas(c: Caso, archivos_cfg: list, comparar, tol, solidez_ok: bool) -> dict:
    """archivos_cfg: [(llave, normalizacion, filtros)] para A y B; filtros como dicts (columna, operador, valores, accion)."""
    (la, _ta, fa), (lb, _tb, fb) = archivos_cfg
    llave = la == [c.llave_a] and lb == [c.llave_b]
    def es_anulado(f):
        vals = [str(v).upper() for v in (f.get("valores") or ([f.get("valor")] if f.get("valor") else []))]
        return (f.get("columna") == c.estado_b and f.get("operador") in ("==", "contiene") and c.anulado in vals
                and f.get("accion", "excluir") == "excluir")
    filtro = len(fa) == 0 and len(fb) == 1 and es_anulado(fb[0])
    comp = [list(map(str, x)) for x in (comparar or [])] == [[c.importe_a, c.importe_b]]
    try:
        tol_ok = Decimal(str(tol).replace(",", ".")) == Decimal(c.tol_esperada)
    except Exception:  # noqa: BLE001
        tol_ok = False
    return {"llave": llave, "normalizacion": llave and solidez_ok, "filtro": filtro, "comparar": comp, "tolerancia": tol_ok,
            "todo": llave and solidez_ok and filtro and comp and tol_ok}


def e6_texto(proveedor, semilla: int = 2000) -> pd.DataFrame:
    from ..instrucciones import _solidez, configurar_desde_texto
    filas = []
    for c, nivel, texto, tol in casos_texto(semilla):
        c.tol_esperada = tol
        archivos = [("G1A1", 0, c.a), ("G2A1", 1, c.b)]
        t0 = time.perf_counter()
        try:
            r, error = configurar_desde_texto(texto, archivos, proveedor), ""
        except Exception as e:  # noqa: BLE001
            r, error = {"respuesta": {}, "config": None, "avisos": [], "inventadas": 0, "corregidas": 0, "valida": False,
                        "tokens": 0}, str(e)[:200]
        seg = time.perf_counter() - t0
        d = r["respuesta"] if isinstance(r["respuesta"], dict) else {}
        # sin validación: la respuesta cruda tal como la propuso el modelo
        crudo = {str(x.get("id")): x for x in (d.get("archivos") or []) if isinstance(x, dict)}
        def ll(x):
            v = x.get("llave") or []
            return [str(y) for y in (v if isinstance(v, list) else [v])]
        ca, cb = crudo.get("G1A1", {}), crudo.get("G2A1", {})
        try:
            sol_crudo = (ll(ca) == [c.llave_a] and ll(cb) == [c.llave_b] and
                         _solidez(c.a, ll(ca), c.b, ll(cb), cb.get("normalizacion") or "exacta") >= SOLIDEZ_MINIMA)
        except Exception:  # noqa: BLE001 (normalización o columna inválida)
            sol_crudo = False
        p_crudo = _partes_correctas(c, [(ll(ca), ca.get("normalizacion"), ca.get("filtros") or []),
                                        (ll(cb), cb.get("normalizacion"), cb.get("filtros") or [])],
                                    d.get("comparar"), d.get("tolerancia", "0.01"), sol_crudo)
        # con validación: la configuración depurada que se carga en el formulario
        cfg = r["config"]
        if cfg is not None:
            A, B = cfg.grupos[0].archivos[0], cfg.grupos[1].archivos[0]
            sol_val = A.llave == [c.llave_a] and B.llave == [c.llave_b] and \
                _solidez(c.a, A.llave, c.b, B.llave, B.transformacion) >= SOLIDEZ_MINIMA
            p_val = _partes_correctas(c, [(A.llave, A.transformacion, [f.model_dump() for f in A.filtros]),
                                          (B.llave, B.transformacion, [f.model_dump() for f in B.filtros])],
                                      cfg.comparaciones, cfg.tolerancia, sol_val)
            exactitud = _cruzar_config(c, cfg)["exactitud"] if cfg.comparaciones else float("nan")
        else:
            p_val = {k: False for k in ("llave", "normalizacion", "filtro", "comparar", "tolerancia", "todo")}
            exactitud = float("nan")
        filas.append({"caso": c.id, "variante": c.variante, "dificultad": c.dificultad, "nivel": nivel, "texto": texto,
                      "tolerancia_pedida": tol, **{f"crudo_{k}": v for k, v in p_crudo.items()},
                      **{f"val_{k}": v for k, v in p_val.items()}, "exactitud_cruce": exactitud,
                      "inventadas": r["inventadas"], "corregidas": r["corregidas"], "avisos": len(r["avisos"]),
                      "tokens": r["tokens"], "segundos": round(seg, 2), "error": error,
                      "fallo_cupo": "límite" in error or "429" in error})
    return pd.DataFrame(filas)


def informe_e6(e6: pd.DataFrame) -> str:
    g0 = e6[~e6["fallo_cupo"]]
    L = ["## E6 · Configuración desde una instrucción en texto", "",
         f"{len(g0)} instrucciones válidas ({int(e6['fallo_cupo'].sum())} excluidas por cupo del proveedor).", "",
         "| instrucción | casos | llave | normalización | filtro | importes | tolerancia | configuración completa (sin validar) | "
         "configuración completa (validada) | exactitud del cruce | tokens |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for nivel, g in list(g0.groupby("nivel")) + [("total", g0)]:
        L.append(f"| {nivel} | {len(g)} | {_pct(g['val_llave'].mean())} | {_pct(g['val_normalizacion'].mean())} | "
                 f"{_pct(g['val_filtro'].mean())} | {_pct(g['val_comparar'].mean())} | {_pct(g['val_tolerancia'].mean())} | "
                 f"{_pct(g['crudo_todo'].mean())} | {_pct(g['val_todo'].mean())} | {_pct(g['exactitud_cruce'].mean())} | "
                 f"{g['tokens'].mean():.0f} |")
    L += ["", f"Columnas inventadas: {int(g0['inventadas'].sum())}; correcciones aplicadas por la validación (normalización, filtros invertidos, tolerancia): "
              f"{int(g0['corregidas'].sum())}.", ""]
    for col in ("val_filtro", "val_tolerancia", "val_todo"):
        L.append(f"- {col}: sin validar {_pct(g0[col.replace('val_', 'crudo_')].mean())} → validada {_pct(g0[col].mean())}")
    L.append("")
    for t, g in g0.groupby("tolerancia_pedida"):
        L.append(f"- tolerancia pedida {t}: configuración completa sin validar {_pct(g['crudo_todo'].mean())}, "
                 f"validada {_pct(g['val_todo'].mean())} ({len(g)} casos)")
    for d, g in g0.groupby("dificultad"):
        L.append(f"- nombres {d}: configuración completa validada {_pct(g['val_todo'].mean())} ({len(g)} casos; "
                 "nota: en este diseño la dificultad coincide con la tolerancia pedida)")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ informe


def _pct(x) -> str:
    return f"{100 * x:.1f} %"


def informe(e2: pd.DataFrame, e3: pd.DataFrame, e4: pd.DataFrame, e5: pd.DataFrame,
            e3b: Optional[pd.DataFrame] = None, e4_sin_cambios: Optional[pd.DataFrame] = None) -> str:
    L = ["# Evaluación de la conciliación", "",
         f"Generado el {time.strftime('%Y-%m-%d %H:%M')} con `python -m regspec.evaluacion.conciliacion`. "
         f"{e3['caso'].nunique()} casos sintéticos (4 escenarios × 3 variantes × 2 dificultades).", ""]
    L += ["## E2 · Motor de conciliación", ""]
    t = e2.groupby("condicion")[["exactitud", "prec_iguales", "exh_iguales", "prec_diferencias", "exh_diferencias",
                                 "prec_solo_a", "exh_solo_a", "prec_solo_b", "exh_solo_b"]].mean()
    t = t.reindex([x for x in ["completa", "sin_transformacion", "sin_tolerancia", "sin_exclusion"] if x in t.index])
    L.append("| condición | casos | exactitud | P iguales | E iguales | P difer. | E difer. | P solo A | E solo A | P solo B | E solo B |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for cond, r in t.iterrows():
        n = int((e2["condicion"] == cond).sum())
        L.append(f"| {cond} | {n} | " + " | ".join(_pct(v) for v in r.values) + " |")
    L += ["", "P = precisión, E = exhaustividad, por estado de la llave. «completa» usa la configuración correcta;",
          "cada ablación quita un tipo de coincidencia (la de transformación solo aplica a las variantes con formatos de llave distintos).", ""]
    L += ["## E3 · Sugerencia de llave por código (sin IA)", ""]
    L.append("| dificultad | variante | casos | llave clara | correcta |")
    L.append("|---|---|---|---|---|")
    for (d, v), g in e3.groupby(["dificultad", "variante"]):
        L.append(f"| {d} | {v} | {len(g)} | {_pct(g['llave_clara'].mean())} | {_pct(g['correcta'].mean())} |")
    L.append(f"| **total** | | {len(e3)} | {_pct(e3['llave_clara'].mean())} | {_pct(e3['correcta'].mean())} |")
    L += ["", f"Tiempo medio de la sugerencia: {e3['ms'].mean():.0f} ms por caso.", ""]
    if e3b is not None and len(e3b):
        L += ["## E3b · Sugerencia de llave con LLM", ""]
        cupo = int(e3b["fallo_cupo"].sum())
        g0 = e3b[~e3b["fallo_cupo"]]
        L.append(f"Se excluyen {cupo} consultas que fallaron por cupo del proveedor (no son respuestas del modelo).")
        L.append("")
        L.append("| información enviada | consultas | JSON válido | columnas inventadas / propuestas | llave correcta | "
                 "incorrecta aceptada (solo validación estructural) | incorrecta aceptada (+ solidez) | correcta rechazada por solidez | tokens medios |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for m, g in g0.groupby("muestras"):
            inv, prop = g["columnas_inventadas"].sum(), g["columnas_propuestas"].sum()
            etiqueta = "solo nombres" if m == 0 else f"nombres + {m} ejemplos"
            mal_est = (g["llave_valida"] & ~g["correcta"]).mean()
            mal_sol = (g["plausible"] & ~g["correcta"]).mean()
            bien_rech = (g["correcta"] & ~g["plausible"]).mean()
            L.append(f"| {etiqueta} | {len(g)} | {_pct(g['respondio_json'].mean())} | {inv} / {prop} ({_pct(inv / max(1, prop))}) | "
                     f"{_pct(g['correcta'].mean())} | {_pct(mal_est)} | {_pct(mal_sol)} | {_pct(bien_rech)} | {g['tokens'].mean():.0f} |")
        L.append("")
        for m, g in g0.groupby("muestras"):
            for d, gg in g.groupby("dificultad"):
                L.append(f"- {('solo nombres' if m == 0 else f'nombres + {m} ejemplos')}, {d}: correcta {_pct(gg['correcta'].mean())} ({len(gg)} casos)")
        L.append("")
    L += ["## E4 · Memoria de configuración", "",
          f"Decisiones de configuración del proceso: {e4['decisiones_sin_memoria'].iloc[0]} (llave y normalización de cada "
          "archivo, filtro de exclusión, par de importes y tolerancia). Sin memoria, el usuario toma a mano las que el código no "
          "sugiere; con memoria ofrecida, aceptarla es 1 intervención; con aplicación automática, 0. Una corrección suma 1.", ""]
    if e4_sin_cambios is not None and len(e4_sin_cambios):
        auto = e4_sin_cambios.loc[e4_sin_cambios["modo"].str.startswith("automática"), "ejecucion"]
        L += [f"**Escenario A · proceso estable ({len(e4_sin_cambios)} ejecuciones sin correcciones).** Confianza tras cada "
              "ejecución: " + ", ".join(f"{v} %" for v in e4_sin_cambios["confianza_despues"]) + ". "
              f"Primera aplicación automática (confianza > 95 %): ejecución {int(auto.iloc[0]) if len(auto) else '–'}. "
              f"Intervenciones totales: {int(e4_sin_cambios['intervenciones'].sum())} "
              f"(frente a {int(e4_sin_cambios['intervenciones'].iloc[0]) * len(e4_sin_cambios)} si cada ejecución se configurara desde cero).", ""]
    L += ["**Escenario B · proceso con cambios** (en la ejecución 8 se agrega una exclusión; en la 16 cambia el nombre de "
          "una columna del archivo B, es decir, la estructura).", ""]
    L.append("| ejecución | modo | intervenciones | confianza antes | confianza después |")
    L.append("|---|---|---|---|---|")
    for _, r in e4.iterrows():
        ca = "–" if pd.isna(r["confianza_antes"]) else f"{int(r['confianza_antes'])} %"
        L.append(f"| {r['ejecucion']} | {r['modo']} | {r['intervenciones']} | {ca} | {r['confianza_despues']} % |")
    primera_auto = e4.loc[e4["modo"].str.startswith("automática"), "ejecucion"]
    L += ["", f"Intervenciones: {e4['intervenciones'].iloc[0]} en la 1.ª ejecución; media de "
              f"{e4['intervenciones'].iloc[1:].mean():.2f} en las siguientes. Primera aplicación automática: "
              f"ejecución {int(primera_auto.iloc[0]) if len(primera_auto) else '–'}.", ""]
    L += ["## E5 · Rendimiento", "", "| registros A | registros B | sugerir llave (s) | cruce (s) | exactitud |", "|---|---|---|---|---|"]
    for _, r in e5.iterrows():
        L.append(f"| {int(r['registros_a']):,} | {int(r['registros_b']):,} | {r['seg_sugerir_llave']} | {r['seg_cruce']} | {_pct(r['exactitud'])} |".replace(",", "."))
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--salida", default="resultados/eval_conciliacion")
    ap.add_argument("--llm", help="proveedor para E3b (groq, openai, openrouter, ollama, anthropic)")
    ap.add_argument("--modelo")
    ap.add_argument("--sin-rendimiento", action="store_true")
    ap.add_argument("--semilla", type=int, default=1000, help="semilla de los casos (7 = casos de desarrollo)")
    ap.add_argument("--solo-texto", action="store_true", help="solo E6 (configuración desde texto; requiere --llm)")
    ap.add_argument("--semilla-texto", type=int, default=2000, help="semilla de E6 (2000 = casos con los que se diseñó la validación)")
    a = ap.parse_args(argv)
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    if a.solo_texto:
        from ..llm.cache import ProveedorConCache
        from ..llm.proveedores import crear_proveedor
        e6 = e6_texto(ProveedorConCache(crear_proveedor(a.llm, a.modelo)), a.semilla_texto)
        e6.to_csv(out / "e6_texto.csv", index=False)
        texto = informe_e6(e6)
        (out / "resumen_e6.md").write_text(texto, encoding="utf-8")
        print(texto)
        return
    cs = casos(a.semilla)
    e2, e3, e4 = e2_motor(cs), e3_llave_codigo(cs), e4_memoria()
    e4a = e4_memoria(30, correccion_en=None, estructura_en=None)
    e4a.to_csv(out / "e4_memoria_estable.csv", index=False)
    e5 = pd.DataFrame() if a.sin_rendimiento else e5_rendimiento()
    e3b = None
    if a.llm:
        from ..llm.cache import ProveedorConCache
        from ..llm.proveedores import crear_proveedor
        e3b = e3_llm = e3_llave_llm(cs, ProveedorConCache(crear_proveedor(a.llm, a.modelo)))
        e3_llm.to_csv(out / "e3b_llave_llm.csv", index=False)
    for nombre, df in (("e2_motor", e2), ("e3_llave_codigo", e3), ("e4_memoria", e4), ("e5_rendimiento", e5)):
        df.to_csv(out / f"{nombre}.csv", index=False)
    texto = informe(e2, e3, e4, e5, e3b, e4a)
    (out / "resumen.md").write_text(texto, encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
