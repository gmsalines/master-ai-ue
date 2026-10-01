"""Configuración del cruce a partir de una instrucción en lenguaje natural.

El usuario describe el cruce con sus palabras («cruzá Nro_Factura con Comprobante, sacá los anulados y aceptá
diferencias de hasta un peso») y un LLM lo traduce a una configuración estructurada: llave y normalización de cada
archivo, filtros, pares de importes y tolerancia. Como en el resto del sistema, el modelo solo propone:

  1. Recibe los nombres de columna de cada archivo, unos pocos valores de ejemplo y, para las columnas con pocos
     valores distintos (estados, tipos), su lista de valores, para poder traducir «los anulados» a Estado = ANULADO.
  2. La respuesta pasa por una validación determinista: columnas que existan, operadores y normalizaciones
     admitidos, valores de filtro que aparezcan en los datos, importes numéricos, tolerancia válida y llaves
     sólidas (solapamiento × unicidad, el mismo criterio que la sugerencia por código). Si otra normalización da
     una llave más sólida, se corrige. Una exclusión que elimina la mayoría de las filas (doble negación) se
     invierte, y la tolerancia se ancla al valor que figure literalmente en la instrucción. Lo que no pasa la
     validación se descarta y se informa.
  3. La configuración resultante se carga en el formulario y el usuario la revisa antes de cruzar; el cruce lo
     ejecuta el motor determinista.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

import pandas as pd

from .almacen import ArchivoCfg, ConfigCruce, FiltroCfg, GrupoCfg
from .grupos import OPERADORES, SOLIDEZ_MINIMA, TRANSFORMACIONES, Filtro, _factor_unicidad, _unicidad, serie_numerica, transformar_serie

ACCIONES_VALIDAS = ("excluir", "incluir")
MAX_CATEGORIAS = 12  # columnas con hasta esta cantidad de valores distintos se envían con su lista de valores


def _perfil_archivo(df: pd.DataFrame, muestras: int) -> dict:
    cols = {}
    for c in [c for c in df.columns if not str(c).startswith("_")]:
        s = df[c].dropna().astype(str).str.strip()
        s = s[s != ""]
        distintos = pd.unique(s.head(5000))
        if 0 < len(distintos) <= MAX_CATEGORIAS and len(s) > 2 * len(distintos):
            cols[str(c)] = {"valores_posibles": [str(v) for v in distintos]}
        else:
            cols[str(c)] = {"ejemplos": [str(v) for v in s.head(muestras)]}
    return cols


def construir_pedido(texto: str, archivos: list[tuple[str, int, pd.DataFrame]], muestras: int = 3) -> str:
    """`archivos`: (id, grupo, tabla). El id identifica el archivo en la respuesta (p. ej. «G1A1»)."""
    perfil = {aid: {"grupo": g + 1, "columnas": _perfil_archivo(df, muestras)} for aid, g, df in archivos}
    return (
        "Configurá una conciliación de archivos a partir de la instrucción del usuario.\n"
        f"Archivos (agrupados; el cruce se hace entre grupos):\n{json.dumps(perfil, ensure_ascii=False)}\n\n"
        f"Instrucción del usuario:\n«{texto.strip()}»\n\n"
        "Respondé SOLO JSON con esta forma:\n"
        '{"archivos": [{"id": "G1A1", "llave": ["columna"], "normalizacion": "exacta|solo_numeros|sin_ceros|normalizada", '
        '"filtros": [{"columna": "col", "operador": "==|!=|>|>=|<|<=|contiene|vacío|no vacío", "valores": ["v"], '
        '"accion": "excluir|incluir"}]}], "comparar": [["columna_importe_grupo1", "columna_importe_grupo2"]], '
        '"tolerancia": "0.01", "dudas": ["lo que no pudiste resolver"]}\n'
        "Reglas: usá exactamente los nombres de columna y los valores dados; incluí un elemento por cada archivo; en "
        "«comparar» poné una columna por grupo, en el orden de los grupos; si la instrucción no menciona tolerancia, "
        "usá 0.01; si las llaves tienen formatos distintos (guiones, ceros a la izquierda) usá «solo_numeros»; si algo "
        "no se puede resolver con los datos, decilo en «dudas» en vez de inventarlo."
    )


_INVERSO = {"==": "!=", "!=": "==", ">": "<=", ">=": "<", "<": ">=", "<=": ">", "vacío": "no vacío", "no vacío": "vacío"}


def tolerancia_en_texto(texto: str) -> Optional[Decimal]:
    """Tolerancia mencionada literalmente en la instrucción («tolerancia 1», «hasta 1 peso», «un centavo»), si la hay."""
    t = texto.lower()
    if re.search(r"\bun\s+centavo|\bum\s+centavo", t):
        return Decimal("0.01")
    m = re.search(r"(?:toler[aâ]nc\w*|diferen[cç]\w*\s+de\s+(?:hasta|at[eé])|hasta|at[eé])\s*(?:de\s+)?(?:hasta\s+|at[eé]\s+)?"
                  r"(\d+(?:[.,]\d+)?)", t)
    if not m:
        return None
    try:
        return Decimal(m.group(1).replace(",", "."))
    except InvalidOperation:
        return None


def _llave(df: pd.DataFrame, cols: list[str], tr: str) -> pd.Series:
    partes = [transformar_serie(df[c], tr) for c in cols]
    k = partes[0]
    for p in partes[1:]:
        k = k + "|" + p
    return k


def _solidez(a: pd.DataFrame, la: list[str], b: pd.DataFrame, lb: list[str], tr: str) -> float:
    sa, sb = _llave(a, la, tr), _llave(b, lb, tr)
    ka, kb = set(sa) - {""}, set(sb) - {""}
    solape = len(ka & kb) / max(1, min(len(ka), len(kb)))
    return solape * min(_factor_unicidad(_unicidad(sa)), _factor_unicidad(_unicidad(sb)))


def validar_configuracion(d: dict, archivos: list[tuple[str, int, pd.DataFrame]], texto: Optional[str] = None) -> dict:
    """Valida la respuesta del LLM contra los datos. Devuelve la configuración depurada, los avisos y contadores.

    {"config": ConfigCruce | None, "avisos": [...], "inventadas": int, "corregidas": int, "valida": bool}
    """
    avisos: list[str] = []
    inventadas = corregidas = 0
    por_id = {aid: (g, df) for aid, g, df in archivos}
    resp = {str(x.get("id")): x for x in (d.get("archivos") or []) if isinstance(x, dict)}
    for extra in set(resp) - set(por_id):
        avisos.append(f"El modelo mencionó un archivo inexistente ({extra}); se ignora.")
        inventadas += 1
    salida: dict[str, ArchivoCfg] = {}
    for aid, (g, df) in por_id.items():
        r = resp.get(aid, {})
        llave_bruta = r.get("llave") or []
        llave_bruta = llave_bruta if isinstance(llave_bruta, list) else [llave_bruta]
        llave = [str(c) for c in llave_bruta if str(c) in df.columns]
        inventadas += len(llave_bruta) - len(llave)
        if not llave:
            avisos.append(f"{aid}: el modelo no propuso una llave con columnas existentes.")
        tr = r.get("normalizacion") if r.get("normalizacion") in TRANSFORMACIONES else "exacta"
        filtros = []
        for f in r.get("filtros") or []:
            if not isinstance(f, dict):
                continue
            col, op = str(f.get("columna", "")), f.get("operador")
            vals = f.get("valores") if isinstance(f.get("valores"), list) else ([f.get("valor")] if f.get("valor") else [])
            vals = [str(v) for v in vals if v is not None and str(v).strip()]
            acc = f.get("accion") if f.get("accion") in ACCIONES_VALIDAS else "excluir"
            if col not in df.columns:
                inventadas += 1
                avisos.append(f"{aid}: filtro sobre una columna inexistente ({col}); se descarta.")
                continue
            if op not in OPERADORES:
                avisos.append(f"{aid}: operador no admitido ({op}); se descarta el filtro sobre {col}.")
                continue
            if op in ("==", "!=", "contiene") and vals:
                presentes = df[col].astype(str).str.strip()
                faltan = [v for v in vals if not (presentes.str.contains(v, case=False, regex=False).any() if op == "contiene"
                                                 else (presentes == v).any())]
                if faltan:
                    avisos.append(f"{aid}: los valores {faltan} no aparecen en {col}; se descartan.")
                    vals = [v for v in vals if v not in faltan]
                    if not vals:
                        continue
            n = int(Filtro(col, op, "", acc, vals).descartar(df).sum())
            if acc == "excluir" and n > 0.5 * len(df) and op in _INVERSO:
                # una exclusión que elimina la mayoría de las filas suele ser una doble negación del modelo
                # («excluir donde Estado != ANULADO» cuando quería sacar los anulados): se invierte si así excluye una minoría
                n_inv = int(Filtro(col, _INVERSO[op], "", acc, vals).descartar(df).sum())
                if n_inv < 0.5 * len(df):
                    avisos.append(f"{aid}: el filtro «excluir {col} {op} {vals}» eliminaba {n} de {len(df)} filas; se "
                                  f"invirtió a «excluir {col} {_INVERSO[op]} {vals}» ({n_inv} filas).")
                    op, n = _INVERSO[op], n_inv
                    corregidas += 1
            if n == 0:
                avisos.append(f"{aid}: el filtro sobre {col} no afecta ninguna fila (se mantiene para revisión).")
            filtros.append(FiltroCfg(columna=col, operador=op, valor=vals[0] if len(vals) == 1 else "", accion=acc,
                                     valores=vals))
        salida[aid] = ArchivoCfg(nombre_original=aid, llave=llave, transformacion=tr, filtros=filtros)

    # llaves: misma cantidad de columnas y solidez frente al primer archivo; se corrige la normalización si conviene
    ids = [aid for aid, _g, _df in archivos]
    ancla = ids[0]
    a_df = por_id[ancla][1]
    for aid in ids[1:]:
        la, lb = salida[ancla].llave, salida[aid].llave
        if not la or not lb or len(la) != len(lb):
            continue
        b_df = por_id[aid][1]
        tr_actual = salida[aid].transformacion
        mejor = max(TRANSFORMACIONES, key=lambda t: (_solidez(a_df, la, b_df, lb, t), t == tr_actual))
        sol = _solidez(a_df, la, b_df, lb, mejor)
        if mejor != tr_actual and sol > _solidez(a_df, la, b_df, lb, tr_actual) + 1e-9:
            avisos.append(f"{aid}: se cambió la normalización de «{tr_actual}» a «{mejor}» porque la llave coincide mejor.")
            corregidas += 1
            for x in (salida[ancla], salida[aid]):
                x.transformacion = mejor
        if sol < SOLIDEZ_MINIMA:
            avisos.append(f"{aid}: la llave {' + '.join(lb)} no parece identificar los mismos registros que la del primer "
                          f"archivo (solidez {sol:.0%}); revisala.")

    # importes a comparar: una columna numérica por grupo
    n_grupos = max(g for _a, g, _d in archivos) + 1
    cols_grupo = [set().union(*[set(df.columns) for _a, g, df in archivos if g == gi]) for gi in range(n_grupos)]
    comparar = []
    for par in d.get("comparar") or []:
        if not isinstance(par, (list, tuple)) or len(par) != n_grupos:
            continue
        ok = True
        for gi, c in enumerate(par):
            if str(c) not in cols_grupo[gi]:
                inventadas += 1
                ok = False
                avisos.append(f"Importe inexistente en el grupo {gi + 1} ({c}); se descarta la comparación.")
            else:
                df = next(df for _a, g, df in archivos if g == gi and str(c) in df.columns)
                if serie_numerica(df[str(c)]).notna().mean() < 0.8:
                    ok = False
                    avisos.append(f"La columna {c} del grupo {gi + 1} no es numérica; se descarta la comparación.")
        if ok:
            comparar.append([str(c) for c in par])
    try:
        tol = Decimal(str(d.get("tolerancia", "0.01")).replace(",", "."))
        if tol < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        avisos.append(f"Tolerancia no válida ({d.get('tolerancia')}); se usa 0.01.")
        tol = Decimal("0.01")
    # anclaje a la instrucción: si el texto menciona una tolerancia, manda el texto
    tol_texto = tolerancia_en_texto(texto) if texto else None
    if tol_texto is not None and tol_texto != tol:
        avisos.append(f"La instrucción indica una tolerancia de {tol_texto} y el modelo propuso {tol}; se usa la del texto.")
        tol = tol_texto
        corregidas += 1
    for duda in d.get("dudas") or []:
        if str(duda).strip():
            avisos.append(f"Duda del modelo: {duda}")

    grupos = []
    for gi in range(n_grupos):
        arch = [salida[aid] for aid, g, _d in archivos if g == gi]
        dfs = [df for _a, g, df in archivos if g == gi]
        distintos = len(dfs) > 1 and any(len(set(dfs[0].columns) & set(x.columns)) / max(1, len(set(dfs[0].columns) | set(x.columns))) < 0.5
                                         for x in dfs[1:])
        grupos.append(GrupoCfg(nombre=f"Grupo {gi + 1}", modo="base_referencia" if distintos else "concatenar", archivos=arch))
    valida = all(a.llave for a in salida.values())
    cfg = ConfigCruce(grupos=grupos, comparaciones=comparar, tolerancia=str(tol)) if valida else None
    return {"config": cfg, "avisos": avisos, "inventadas": inventadas, "corregidas": corregidas, "valida": valida}


def configurar_desde_texto(texto: str, archivos: list[tuple[str, int, pd.DataFrame]], proveedor, muestras: int = 3) -> dict:
    """Pide la configuración al LLM y la valida. Devuelve lo mismo que `validar_configuracion` más la respuesta y tokens."""
    r = proveedor.completar([{"role": "user", "content": construir_pedido(texto, archivos, muestras)}], modo_json=True,
                            temperatura=0.0, max_tokens=1500)
    t = r.texto or ""
    try:
        d = json.loads(t[t.find("{"): t.rfind("}") + 1])
    except ValueError:
        d = {}
    v = validar_configuracion(d if isinstance(d, dict) else {}, archivos, texto)
    return {**v, "respuesta": d, "tokens": r.tokens_entrada + r.tokens_salida}
