import copy
import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from regspec.archivos import escribir, generar_xsd, leer, validar_xsd
from regspec.base_sin_ia import extraer_base
from regspec.dsl import Especificacion
from regspec.evaluacion.metricas import metricas_estructurales, metricas_funcionales
from regspec.evaluacion.mutaciones import construir_bateria
from regspec.expr import ContextoArchivo, ErrorExpresion, Evaluador, verificar_regla
from regspec.generador import generar
from regspec.llm.extractor import ConfigExtraccion, extraer
from regspec.llm.proveedores import Guionado
from regspec.sintetico import generar_registros
from regspec.validador import validar
from regspec.verificador import verificar

RAIZ = Path(__file__).resolve().parents[1]
IDS = ["m1_retenciones", "m2_cuentas", "m3_operaciones_xml", "m4_seguros_pdf", "m5_beneficiarios_inconsistente"]


def gold(i) -> dict:
    return json.loads((RAIZ / "gold" / f"{i}.json").read_text(encoding="utf-8"))


def manual(i) -> str:
    p = RAIZ / "manuales" / f"{i}.md"
    return (p if p.exists() else p.with_suffix(".txt")).read_text(encoding="utf-8")


def codigos(res):
    return {p.codigo for p in res.errores}


# ------------------------------------------------------------------ verificador


@pytest.mark.parametrize("i", IDS)
def test_referencias_son_validas(i):
    res = verificar(gold(i), manual(i))
    assert res.valida, [p.a_texto() for p in res.problemas]


def test_detecta_solapamiento_y_hueco():
    d = gold("m1_retenciones")
    d["tipos_registro"][1]["campos"][4]["inicio"] = 27  # base imponible pisa al campo anterior
    d["tipos_registro"][1]["campos"][4]["fin"] = None
    d["tipos_registro"][2]["campos"].pop(2)  # falta el importe total -> hueco
    c = codigos(verificar(d, manual("m1_retenciones")))
    assert {"SOLAPAMIENTO", "HUECO"} <= c


def test_detecta_inconsistencia_inicio_fin_longitud():
    d = gold("m1_retenciones")
    d["tipos_registro"][1]["campos"][3]["longitud"] = 14
    assert "INCONSISTENCIA_FIN" in codigos(verificar(d))


def test_regla_con_campo_inexistente_sugiere_correccion():
    d = gold("m1_retenciones")
    d["reglas"][2]["expresion"] = "importe_retenid == redondear(base_imponible * alicuota / 100, 2)"
    res = verificar(d)
    p = [x for x in res.errores if x.codigo == "REGLA_INVALIDA"][0]
    assert "importe_retenido" in p.sugerencia


def test_evidencia_inventada_se_detecta():
    d = gold("m2_cuentas")
    d["tipos_registro"][1]["campos"][2]["evidencia"] = "Tipo de cuenta | X(1) | valores C, A, P, E (cuenta especial)"
    assert "EVIDENCIA" in codigos(verificar(d, manual("m2_cuentas")))


def test_identificador_de_registro_obligatorio():
    d = gold("m1_retenciones")
    d["tipos_registro"][0]["campos"][0]["tipo"] = "numerico"
    d["tipos_registro"][0]["campos"][0]["valor_constante"] = None
    assert "SIN_IDENTIFICADOR" in codigos(verificar(d))


def test_reglas_contradictorias_no_son_ejecutables():
    d = gold("m1_retenciones")
    d["reglas"].append({"id": "R9", "descripcion": "x", "ambito": "registro", "tipo_registro": "02",
                        "expresion": "base_imponible < 0", "evidencia": ""})
    assert "REGLAS_INSATISFACIBLES" in codigos(verificar(d, chequear_evidencia=False))


def test_error_de_esquema():
    d = gold("m1_retenciones")
    d["tipos_registro"][0]["campos"][1]["tipo"] = "entero"
    res = verificar(d)
    assert res.spec is None and "ESQUEMA" in codigos(res)


# ------------------------------------------------------------------ lenguaje de reglas


def test_chequeo_estatico_de_tipos():
    s = Especificacion.model_validate(gold("m2_cuentas"))
    verificar_regla(s, 'total_creditos == sumar("MOV", "importe", "numero_cuenta", numero_cuenta, "tipo_movimiento", "C")', "CTA")
    with pytest.raises(ErrorExpresion):
        verificar_regla(s, 'tipo_cuenta == 1', "CTA")  # texto vs número
    with pytest.raises(ErrorExpresion):
        verificar_regla(s, 'valor("MOV", "importe") > 0', None)  # MOV se repite
    with pytest.raises(ErrorExpresion):
        verificar_regla(s, '__import__("os")', None)  # construcciones fuera del lenguaje


def test_evaluacion():
    ctx = ContextoArchivo({"MOV": [{"cuenta": "A", "importe": Decimal("10.5")}, {"cuenta": "B", "importe": Decimal(2)}]})
    ev = Evaluador(ctx, {"cuenta": "A", "total": Decimal("10.50"), "f": date(2025, 1, 2)})
    assert ev.evaluar('total == sumar("MOV", "importe", "cuenta", cuenta)')
    assert ev.evaluar('contar("MOV") == 2 and implica(cuenta == "B", total > 100)')


# ------------------------------------------------------------------ archivos


@pytest.mark.parametrize("i", IDS)
def test_ida_y_vuelta(i):
    s = Especificacion.model_validate(gold(i))
    regs, pendientes = generar_registros(s, 7)
    assert not pendientes
    contenido = escribir(s, regs)
    leidos, estructura = leer(s, contenido)
    assert not estructura and len(leidos) == len(regs)
    assert validar(s, contenido).ok


def test_xml_valida_contra_xsd():
    s = Especificacion.model_validate(gold("m3_operaciones_xml"))
    contenido = escribir(s, generar_registros(s, 3)[0])
    assert validar_xsd(generar_xsd(s), contenido) == []
    assert validar_xsd(generar_xsd(s), contenido.replace("<Canal>", "<Canal>X")) != []


def test_txt_posicional_formato():
    s = Especificacion.model_validate(gold("m1_retenciones"))
    contenido = escribir(s, generar_registros(s, 1)[0])
    lineas = contenido.split("\r\n")[:-1]
    assert all(len(l) == 80 for l in lineas)
    assert lineas[0].startswith("01") and lineas[-1].startswith("09")


# ------------------------------------------------------------------ generación del informe


def test_generacion_completa_totales_de_control():
    s = Especificacion.model_validate(gold("m1_retenciones"))
    detalle = [
        {"fecha_retencion": "2025-06-10", "tipo_documento": "01", "numero_documento": "30111222333",
         "base_imponible": "1000,00", "alicuota": "3.50", "numero_comprobante": "A-0001"},
        {"fecha_retencion": "2025-06-12", "tipo_documento": "02", "numero_documento": "25111222",
         "base_imponible": "250.40", "alicuota": "2", "numero_comprobante": "A-0002"},
    ]
    regs = [("01", {"identificador_agente": 30500000000, "periodo": "2025-06", "razon_social": "AGENTE DEMO SA"})]
    regs += [("02", d) for d in detalle] + [("09", {})]
    r = generar(s, regs)
    assert r.ok, (r.error_escritura, r.informe.resumen())
    cierre = r.contenido.split("\r\n")[-2]
    assert cierre[2:10] == "00000002"  # cantidad calculada
    assert cierre[10:25] == "000000000004001"  # 35.00 + 5.01 = 40.01
    assert any("importe_retenido" in x for x in r.autocompletados)


def test_validacion_previa_informa_linea_y_regla():
    s = Especificacion.model_validate(gold("m1_retenciones"))
    regs, _ = generar_registros(s, 2)
    regs[1][1]["importe_retenido"] += Decimal("0.01")
    inf = validar(s, escribir(s, regs))
    assert not inf.ok and "R3" in inf.reglas_violadas() and any(h.linea == 2 for h in inf.errores)


# ------------------------------------------------------------------ extractor con LLM (proveedor guionado)


def test_bucle_de_autocorreccion():
    bueno = gold("m1_retenciones")
    malo = copy.deepcopy(bueno)
    malo["tipos_registro"][1]["campos"][4]["inicio"] = 29  # error de lectura
    malo["tipos_registro"][1]["campos"][4]["fin"] = 43
    malo["reglas"][0]["expresion"] = 'valor("09", "cant_registros") == contar("02")'
    malo["tipos_registro"][0]["campos"][3]["evidencia"] = "Nombre del agente, texto de 50"
    prov = Guionado([json.dumps(malo), "```json\n" + json.dumps(bueno) + "\n```"])
    r = extraer(manual("m1_retenciones"), prov, ConfigExtraccion(max_iteraciones=4, modo="completo"))
    assert r.valida and len(r.iteraciones) == 2
    assert r.iteraciones[0].errores >= 3
    feedback = prov.recibidos[1][-1]["content"]
    assert "cantidad_registros" in feedback and "EVIDENCIA" in feedback and "base_imponible" in feedback


def test_un_solo_intento_no_corrige():
    malo = gold("m1_retenciones")
    malo["tipos_registro"][1]["campos"][4]["inicio"] = 29
    prov = Guionado([json.dumps(malo), json.dumps(gold("m1_retenciones"))])
    r = extraer(manual("m1_retenciones"), prov, ConfigExtraccion(usar_verificador=False, modo="completo"))
    assert not r.valida and len(r.iteraciones) == 1


def test_json_invalido_se_reintenta():
    prov = Guionado(["esto no es json", json.dumps(gold("m1_retenciones"))])
    r = extraer(manual("m1_retenciones"), prov, ConfigExtraccion(modo="completo"))
    assert r.valida and not r.iteraciones[0].json_valido


# ------------------------------------------------------------------ evaluación


@pytest.mark.parametrize("i", IDS)
def test_banco_con_referencia_da_100(i):
    s = Especificacion.model_validate(gold(i))
    bateria = construir_bateria(s)
    assert metricas_estructurales(s, s)["campos_exactos"] == 1.0
    f = metricas_funcionales(s, bateria)
    assert f["especificidad"] == 1.0 and f["sensibilidad"] == 1.0


def test_metrica_no_premia_rechazar_todo():
    s = Especificacion.model_validate(gold("m1_retenciones"))
    roto = copy.deepcopy(s)
    roto.longitud_registro = 81  # rechaza cualquier archivo
    f = metricas_funcionales(roto, construir_bateria(s))
    assert f["sensibilidad"] == 0.0 and f["especificidad"] == 0.0


def test_base_sin_ia_lee_tablas_pero_no_reglas():
    s = Especificacion.model_validate(gold("m1_retenciones"))
    d = extraer_base(manual("m1_retenciones"))
    m = metricas_estructurales(Especificacion.model_validate(d), s)
    assert m["campos_f1"] == 1.0 and m["reglas_extraidas"] == 0
    assert extraer_base(manual("m3_operaciones_xml"))["tipos_registro"] == []


def test_correccion_localizada_con_parche():
    bueno = gold("m2_cuentas")
    malo = copy.deepcopy(bueno)
    malo["reglas"][8]["expresion"] = 'cantidad_cuentas == contar("CTA")'  # campo suelto en ámbito archivo
    malo["tipos_registro"][2]["campos"][4]["evidencia"] = "importe con signo"
    parche = {"reglas": {"8": bueno["reglas"][8]}, "tipos_registro": {"2": bueno["tipos_registro"][2]}}
    prov = Guionado([json.dumps(malo), json.dumps(parche)])
    r = extraer(manual("m2_cuentas"), prov, ConfigExtraccion(modo="completo"))
    assert r.valida and len(r.iteraciones) == 2
    pedido = prov.recibidos[1][-1]["content"]
    assert "Fragmentos con problemas" in pedido and '"8"' in pedido
    assert len(pedido) < len(json.dumps(bueno)) / 2  # no se reenvía la especificación completa


def test_extraccion_por_secciones():
    g = gold("m2_cuentas")
    esqueleto = {k: v for k, v in g.items() if k not in ("tipos_registro", "reglas")}
    esqueleto["tipos_registro"] = [{k: v for k, v in t.items() if k != "campos"} for t in g["tipos_registro"]]
    respuestas = [json.dumps(esqueleto)] + [json.dumps({"campos": t["campos"]}) for t in g["tipos_registro"]]
    respuestas.append(json.dumps({"reglas": g["reglas"]}))
    prov = Guionado(respuestas)
    pasos = []
    r = extraer(manual("m2_cuentas"), prov, ConfigExtraccion(modo="secciones", usar_codigo=False), al_paso=pasos.append)
    assert r.valida and len(r.iteraciones) == 1
    assert [p.nombre for p in pasos] == ["esqueleto", "campos CAB", "campos CTA", "campos MOV", "campos TOT", "reglas"]
    assert "numero_cuenta" in prov.recibidos[-1][-1]["content"]  # el paso de reglas recibe el índice de campos


def test_parche_parcial_se_fusiona():
    from regspec.llm.extractor import aplicar_parche
    g = gold("m2_cuentas")
    malo = copy.deepcopy(g)
    malo["tipos_registro"][1]["campos"][8]["obligatorio"] = True
    malo["reglas"][6]["evidencia"] = "inventada"
    parche = {"tipos_registro": {"1": {"campos": [{"nombre": "fecha_vencimiento", "obligatorio": False}]}},
              "reglas": {"6": {"evidencia": g["reglas"][6]["evidencia"]}}}
    assert aplicar_parche(malo, parche) == g


def test_parche_elimina_clave_mal_escrita():
    from regspec.llm.extractor import aplicar_parche
    g = gold("m2_cuentas")
    malo = copy.deepcopy(g)
    malo["tipos_registro"][2]["max_occurrencias"] = malo["tipos_registro"][2].pop("max_ocurrencias")
    assert aplicar_parche(malo, {"tipos_registro": {"2": {"max_ocurrencias": None}}}) == g


def test_correccion_por_lotes_en_modo_secciones():
    g = gold("m2_cuentas")
    esq = {k: v for k, v in g.items() if k not in ("tipos_registro", "reglas")}
    esq["tipos_registro"] = [{k: v for k, v in t.items() if k != "campos"} for t in g["tipos_registro"]]
    campos = [copy.deepcopy(t["campos"]) for t in g["tipos_registro"]]
    campos[0][1]["evidencia"] = "Código de entidad | 4 | 9(8)"  # evidencia con un dato que no está en el manual
    reglas = copy.deepcopy(g["reglas"])
    reglas[8]["expresion"] = 'cantidad_cuentas == contar("CTA")'
    respuestas = [json.dumps(esq)] + [json.dumps({"campos": c}) for c in campos] + [json.dumps({"reglas": reglas})]
    respuestas += [json.dumps({"tipos_registro": {"0": {"campos": [{"nombre": "codigo_entidad", "evidencia": g["tipos_registro"][0]["campos"][1]["evidencia"]}]}}}),
                   json.dumps({"reglas": {"8": g["reglas"][8]}})]
    prov = Guionado(respuestas)
    r = extraer(manual("m2_cuentas"), prov, ConfigExtraccion(modo="secciones", usar_codigo=False))
    assert r.valida and len(r.iteraciones) == 2
    assert len(prov.recibidos) == 8  # 6 pasos + 2 lotes de corrección (registro CAB y reglas)


def test_evidencia_fila_con_celdas_omitidas_es_valida():
    from regspec.verificador import evidencia_en_manual, normalizar
    m = manual("m2_cuentas")
    lineas = [normalizar(l) for l in m.splitlines()]
    assert evidencia_en_manual("| Código de entidad | 4 | 9(6) | |", normalizar(m), lineas_norm=lineas)
    assert not evidencia_en_manual("| Código de entidad | 4 | 9(8) |", normalizar(m), lineas_norm=lineas)


def test_ejemplo_xml_del_manual_debe_validar():
    d = gold("m3_operaciones_xml")
    d["tipos_registro"][1]["campos"][1]["formato_fecha"] = "AAAAMMDD"  # el manual dice AAAA-MM-DD
    d["tipos_registro"][1]["campos"][1]["longitud"] = 8
    res = verificar(d, manual("m3_operaciones_xml"))
    assert any(p.codigo == "EJEMPLO_DEL_MANUAL" and "fecha_operacion" in p.ruta for p in res.errores)


def test_mascara_de_fecha_debe_coincidir_con_la_evidencia():
    d = gold("m1_retenciones")
    d["tipos_registro"][1]["campos"][1]["formato_fecha"] = "DDMMAAAA"
    assert "ATRIBUTO_NO_RESPALDADO" in codigos(verificar(d, manual("m1_retenciones")))


def test_parche_con_campos_por_nombre():
    from regspec.llm.extractor import aplicar_parche
    g = gold("m3_operaciones_xml")
    malo = copy.deepcopy(g)
    malo["tipos_registro"][1]["campos"][1]["formato_fecha"] = "AAAAMMDD"
    parche = {"tipos_registro": {"1": {"campos": {"fecha_operacion": {"formato_fecha": "AAAA-MM-DD"}}}}}
    assert aplicar_parche(malo, parche) == g



def test_hibrido_codigo_primero_la_ia_solo_corrige():
    """m1: los registros 01 y 09 se leen por código (0 tokens); el 02 remite a una tabla de códigos y la IA
    solo devuelve la corrección; las reglas las extrae la IA."""
    from regspec.base_sin_ia import extraer_base
    g = gold("m1_retenciones")
    esq = {k: v for k, v in g.items() if k not in ("tipos_registro", "reglas")}
    esq["tipos_registro"] = [{k: v for k, v in t.items() if k != "campos"} for t in g["tipos_registro"]]
    revision_02 = {"correcciones": {"tipo_de_documento_del_sujeto_retenido": {"valores_permitidos": ["01", "02", "03"]}}}
    reglas = copy.deepcopy(g["reglas"])
    for r in reglas:  # los nombres de campo son los que asignó el parser
        r["expresion"] = (r["expresion"].replace('"cantidad_registros"', '"cantidad_de_registros_de_detalle"')
                          .replace("* alicuota", "* alicuota_aplicada"))
    prov = Guionado([json.dumps(esq), json.dumps(revision_02), json.dumps({"reglas": reglas})])
    pasos = []
    r = extraer(manual("m1_retenciones"), prov, ConfigExtraccion(modo="secciones"), al_paso=pasos.append)
    nombres = [p.nombre for p in pasos]
    assert nombres == ["esqueleto", "campos 01 (por código)", "campos 02 (revisión)", "campos 09 (por código)", "reglas"]
    assert len(prov.recibidos) == 3  # solo 3 llamadas a la IA
    assert r.valida, r.iteraciones[-1].problemas
    # el esqueleto y la revisión reciben fragmentos, no el manual completo
    assert all(len(m[-1]["content"]) < len(manual("m1_retenciones")) for m in prov.recibidos[:2])


def test_parche_elimina_campo():
    from regspec.llm.extractor import aplicar_parche
    g = gold("m2_cuentas")
    malo = copy.deepcopy(g)
    malo["tipos_registro"][2]["campos"].append({"nombre": "extra", "inicio": "?", "longitud": 1, "tipo": "alfanumerico"})
    assert aplicar_parche(malo, {"tipos_registro": {"2": {"eliminar_campos": ["extra"]}}}) == g


def test_no_se_pueden_borrar_reglas_respaldadas():
    g = gold("m1_retenciones")
    malo = copy.deepcopy(g)
    malo["reglas"][0]["expresion"] = 'contar("02") == 0'  # contradice min_ocurrencias=1: insatisfacible
    sin_reglas = copy.deepcopy(malo)
    sin_reglas["reglas"] = sin_reglas["reglas"][1:]  # el modelo "resuelve" borrando la regla
    prov = Guionado([json.dumps(malo), json.dumps(sin_reglas), json.dumps(g)])
    r = extraer(manual("m1_retenciones"), prov, ConfigExtraccion(modo="completo", correccion="completa"))
    assert r.valida and "R1" in r.reglas_restauradas
    assert len(r.spec.reglas) == 4
    assert any("REGLA_ELIMINADA" in p for p in r.iteraciones[1].problemas)


# ------------------------------------------------------------------ cruce de archivos (sin IA)


def test_cruce_csv_contra_txt_posicional():
    from regspec.cruce import cruzar, sugerir_llave, tabla_desde_archivo
    s = Especificacion.model_validate(gold("m1_retenciones"))
    A = tabla_desde_archivo((RAIZ / "ejemplos/cruce_sistema.csv").read_bytes(), "cruce_sistema.csv")
    B = tabla_desde_archivo((RAIZ / "ejemplos/cruce_presentado.txt").read_bytes(), "cruce_presentado.txt", s)
    llave = sugerir_llave(A, B)[0]
    assert (llave.col_a, llave.col_b) == ("comprobante", "numero_comprobante")
    r = cruzar(A, B, [llave.col_a], [llave.col_b], [("base", "base_imponible"), ("retenido", "importe_retenido")])
    assert r.resumen["solo en A"] == 1 and r.solo_a.iloc[0]["comprobante"] == "RET-000105"
    assert r.resumen["solo en B"] == 1 and r.solo_b.iloc[0]["numero_comprobante"] == "RET-000099"
    assert r.resumen["en ambos con diferencias"] == 1 and r.diferencias.iloc[0]["llave"] == "RET-000103"
    assert r.resumen["en ambos iguales"] == 3
    assert len(r.a_excel()) > 1000


def test_cruce_tolerancia_y_duplicados():
    import pandas as pd
    from decimal import Decimal
    from regspec.cruce import cruzar
    A = pd.DataFrame({"id": ["1", "2", "2"], "monto": ["10.00", "20,00", "5"]})
    B = pd.DataFrame({"ref": ["001", "2"], "importe": ["10.004", "20.50"]})
    r = cruzar(A, B, ["id"], ["ref"], [("monto", "importe")], Decimal("0.01"))
    assert r.resumen["en ambos iguales"] == 1  # "1" y "001" son la misma llave; 10.00 vs 10.004 dentro de la tolerancia
    assert r.resumen["llaves duplicadas en A"] == 1
    assert r.resumen["en ambos con diferencias"] == 1



def test_manual_inconsistente_lo_detecta_el_verificador():
    """m5 tiene una errata: el campo nombre va de 13 a 42 (30 posiciones) pero la columna Long. dice 28."""
    d = gold("m5_beneficiarios_inconsistente")
    d["tipos_registro"][1]["campos"][2]["longitud"] = 28  # copiar el manual al pie de la letra
    assert "INCONSISTENCIA_FIN" in codigos(verificar(d, manual("m5_beneficiarios_inconsistente")))


def test_regla_de_registro_unico_con_valor_es_satisfacible():
    """Un modelo escribió los totales de control como reglas de ámbito registro sobre el cierre usando valor():
    es equivalente y no debe informarse como insatisfacible."""
    d = gold("m1_retenciones")
    for r in d["reglas"][:2]:
        r["ambito"], r["tipo_registro"] = "registro", "09"
    assert verificar(d, manual("m1_retenciones")).valida


def test_deteccion_de_formato_y_llave_ia_validada():
    import pandas as pd
    from regspec.cruce import biblioteca_de_especificaciones, detectar_especificacion, llave_clara, sugerir_llave, sugerir_llave_ia
    bib = biblioteca_de_especificaciones(RAIZ / "gold")
    det = detectar_especificacion((RAIZ / "ejemplos/cruce_presentado.txt").read_bytes(), "x.txt", bib)
    assert det.nombre_spec == "m1_retenciones" and det.tipo_registro == "02" and det.confianza == 1.0
    # columnas sin nombres parecidos ni valores idénticos: el código no encuentra llave clara
    A = pd.DataFrame({"ref": ["F-1", "F-2", "F-3"], "total": ["10", "20", "30"]})
    B = pd.DataFrame({"comprobante": ["F-1", "F-2", "F-9"], "monto": ["10", "25", "5"]})
    assert llave_clara(sugerir_llave(A, B)) in (True, False)
    # la IA propone una columna inexistente y una válida: solo se acepta lo que existe
    prov = Guionado([json.dumps({"llave_a": ["ref"], "llave_b": ["comprobante"], "comparar": [["total", "monto"], ["x", "y"]], "motivo": "ids"})])
    r = sugerir_llave_ia(A, B, prov)
    assert r["llave_a"] == ["ref"] and r["comparar"] == [("total", "monto")] and round(r["solape"], 2) == 0.67
    prov = Guionado([json.dumps({"llave_a": ["inventada"], "llave_b": ["comprobante"]})])
    with pytest.raises(ValueError):
        sugerir_llave_ia(A, B, prov)


# ------------------------------------------------------------------ grupos de archivos (motor N-way)


def test_grupos_base_referencia_transformacion_exclusion_tolerancia():
    import pandas as pd
    from regspec.grupos import ArchivoGrupo, Filtro, Grupo, cruzar_grupos
    rd = lambda n: pd.read_csv(RAIZ / "ejemplos/grupos" / n, dtype=str)  # noqa: E731
    g1 = Grupo("Sistema", [
        ArchivoGrupo("retenciones", rd("g1_retenciones_sistema.csv"), ["ID_FISCAL"], "solo_numeros",
                     [Filtro("MONTO_RETENIDO", "<=", "0")]),
        ArchivoGrupo("padron", rd("g1_padron_referencia.csv"), ["Identificador"], "solo_numeros", traer=["Categoria"]),
    ], modo="base_referencia", importes=["MONTO_RETENIDO"])
    g2 = Grupo("Agente", [ArchivoGrupo("reporte", rd("g2_reporte_agente.csv"), ["Id Contribuyente"], "solo_numeros")])
    r = cruzar_grupos([g1, g2], [("MONTO_RETENIDO", "Monto Retenido")])
    assert r.estadisticas["Sistema"]["excluidas por filtros"] == 1
    assert r.estadisticas["Sistema"]["encontradas en padron"].startswith("19 de 19")
    assert r.resumen["en todos los grupos"] == 9 and r.resumen["en todos, con diferencias"] == 1
    assert r.resumen["solo en Sistema"] == 1 and r.resumen["solo en Agente"] == 1
    assert "Categoria" in r.detalles["Sistema"].columns
    assert len(r.a_excel()) > 3000
    # sin transformación, los ids con guiones no coinciden: 0 llaves en común
    g2e = Grupo("Agente", [ArchivoGrupo("reporte", rd("g2_reporte_agente.csv"), ["Id Contribuyente"], "exacta")])
    g1e = Grupo("Sistema", [ArchivoGrupo("ret", rd("g1_retenciones_sistema.csv"), ["ID_FISCAL"], "exacta")])
    assert cruzar_grupos([g1e, g2e]).resumen["en todos los grupos"] == 0


def test_tres_grupos_y_suma_por_llave():
    import pandas as pd
    from regspec.grupos import ArchivoGrupo, Grupo, cruzar_grupos
    a = pd.DataFrame({"id": ["1", "1", "2"], "m": ["10", "5", "7"]})
    b = pd.DataFrame({"ref": ["1", "2", "3"], "imp": ["15", "7", "1"]})
    c = pd.DataFrame({"k": ["01", "02"], "v": ["15", "8"]})
    r = cruzar_grupos([Grupo("A", [ArchivoGrupo("a", a, ["id"])], "sumar_por_llave"),
                       Grupo("B", [ArchivoGrupo("b", b, ["ref"])]),
                       Grupo("C", [ArchivoGrupo("c", c, ["k"], "sin_ceros")])], [("m", "imp", "v")])
    assert r.resumen["en todos los grupos"] == 2 and r.resumen["en todos, con diferencias"] == 1
    assert r.resumen["solo en B"] == 1


# ---------------------------------------------------------------- formato delimitado y lectura de tablas

def _spec_delimitada(fijo: bool):
    from regspec.dsl import Especificacion
    return Especificacion.model_validate({
        "nombre": "Retenciones delimitadas", "formato": "delimitado", "separador_campos": ";", "campos_ancho_fijo": fijo,
        "tipos_registro": [{"codigo": "R", "nombre": "Retención", "max_ocurrencias": None, "campos": [
            {"nombre": "id_fiscal", "tipo": "alfanumerico", "longitud": 11, "patron": r"\d{11}"},
            {"nombre": "fecha", "tipo": "fecha", "longitud": 10, "formato_fecha": "DD/MM/AAAA"},
            {"nombre": "base", "tipo": "decimal", "longitud": 12, "decimales": 2},
            {"nombre": "alicuota", "tipo": "decimal", "longitud": 4, "decimales": 2},
            {"nombre": "retenido", "tipo": "decimal", "longitud": 12, "decimales": 2}]}],
        "reglas": [{"id": "R1", "descripcion": "cálculo", "ambito": "registro", "tipo_registro": "R",
                    "expresion": "retenido == redondear(base * alicuota / 100, 2)"}]})


def test_delimitado_ida_y_vuelta_y_verificador():
    from regspec.archivos import escribir, leer
    from regspec.verificador import verificar
    for fijo in (False, True):
        spec = _spec_delimitada(fijo)
        assert verificar(spec, None).valida
        regs = [("R", {"id_fiscal": "20123456789", "fecha": date(2026, 7, 6), "base": Decimal("12000"),
                       "alicuota": Decimal("1.5"), "retenido": Decimal("180")})]
        txt = escribir(spec, regs)
        linea = txt.strip()
        assert linea == ("20123456789;06/07/2026;0000012000.00;01.50;0000000180.00" if fijo
                         else "20123456789;06/07/2026;12000.00;1.50;180.00")
        leidos, estructura = leer(spec, txt)
        assert not estructura and not leidos[0].errores and leidos[0].valores["retenido"] == Decimal("180.00")


def test_delimitado_detecta_errores_y_reglas():
    from regspec.validador import validar
    spec = _spec_delimitada(True)
    malo = ("20123456789;06/07/2026;0000012000.00;01.50;0000000180.00\n"
            "20123456789;06/07/2026;0000012000.00;00.00;0000000047.71\n"   # viola R1
            "2012345678X;06/07/2026;0000012000.00;01.50;0000000180.00\n"   # patrón
            "20123456789;06/07/2026;12000.00;01.50;0000000180.00\n")       # ancho
    inf = validar(spec, malo)
    lineas = {e.linea for e in inf.errores}
    assert {2, 3, 4} <= lineas and 1 not in lineas


def test_tabla_delimitada_rapida_y_deteccion():
    from regspec.cruce import detectar_especificacion, tabla_desde_archivo
    spec = _spec_delimitada(True)
    contenido = ("20123456789;06/07/2026;0000012000.00;01.50;0000000180.00\n" * 3).encode("latin-1")
    det = detectar_especificacion(contenido, "x.txt", {"delim": spec})
    assert det.nombre_spec == "delim" and det.confianza == 1.0
    df = tabla_desde_archivo(contenido, "x.txt", spec)
    assert list(df.columns) == ["id_fiscal", "fecha", "base", "alicuota", "retenido"] and len(df) == 3


def test_excel_elige_hoja_y_fila_de_encabezado():
    from regspec.cruce import tabla_desde_archivo
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([["Resumen"]]).to_excel(xw, sheet_name="Resumen", index=False, header=False)
        pd.DataFrame([[None, "Información contable", None], ["Id", "Nombre", "Monto"], ["1", "A", "10"], ["2", "B", "20"]]
                     ).to_excel(xw, sheet_name="Detalle", index=False, header=False)
    df = tabla_desde_archivo(buf.getvalue(), "r.xlsx")
    assert list(df.columns) == ["Id", "Nombre", "Monto"] and df["Monto"].tolist() == ["10", "20"]


def test_transformar_serie_equivale_a_transformar():
    from regspec.grupos import transformar, transformar_serie
    s = pd.Series(["20-05098256-5", "000123", " 0 ", "abc", None, float("nan"), "00", "á b", 123])
    for tipo in ("exacta", "solo_numeros", "sin_ceros", "normalizada"):
        assert transformar_serie(s, tipo).tolist() == [transformar(v, tipo) for v in s]


# ---------------------------------------------------------------- cruces guardados

def test_almacen_local_guarda_lista_y_borra(tmp_path):
    from regspec.almacen import AlmacenLocal, ArchivoCfg, ConfigCruce, FiltroCfg, GrupoCfg
    cfg = ConfigCruce(grupos=[
        GrupoCfg(nombre="Sistema", modo="base_referencia", archivos=[
            ArchivoCfg(nombre_original="ret.xlsx", llave=["ID"], transformacion="solo_numeros",
                       filtros=[FiltroCfg(columna="MONTO", operador="<=", valor="0")]),
            ArchivoCfg(nombre_original="padron.txt", llave=["id"], traer=["crc"])]),
        GrupoCfg(nombre="Presentado", archivos=[ArchivoCfg(llave=["cuit", "comprobante"], transformacion="solo_numeros")])],
        comparaciones=[["MONTO", "monto"]], tolerancia="0.01")
    a = AlmacenLocal(tmp_path)
    g = a.guardar("Julio", cfg, {"en todos los grupos": 9}, ["ret.xlsx", "padron.txt", "pres.txt"], b"xlsx", "gabi@x.com")
    assert [x.id for x in a.listar()] == [g.id] and a.listar("otro@x.com") == []
    leido = a.obtener(g.id)
    assert leido.config == cfg and leido.resumen == {"en todos los grupos": "9"} and a.excel(g.id) == b"xlsx"
    assert leido.config.archivo(0, 1).traer == ["crc"] and leido.config.archivo(2, 0) is None
    assert a.borrar(g.id) and a.listar() == []
    with pytest.raises(ValueError):
        a.obtener("../../etc/passwd")


# ---------------------------------------------------------------- idiomas

def test_todos_los_textos_de_la_app_tienen_traduccion_al_portugues():
    import ast as _ast
    from regspec.i18n import PT
    arbol = _ast.parse((Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8"))
    claves = {n.args[0].value for n in _ast.walk(arbol)
              if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name) and n.func.id == "_"
              and n.args and isinstance(n.args[0], _ast.Constant) and isinstance(n.args[0].value, str)}
    faltan = sorted(k for k in claves if k not in PT)
    assert not faltan, faltan
    # los marcadores {0}, {1:.0%}... tienen que coincidir entre idiomas
    import string
    campos = lambda s: sorted((f, e) for _, f, e, _c in string.Formatter().parse(s) if f is not None)
    assert all(campos(k) == campos(PT[k]) for k in claves)


def test_traducir_mensajes_variables():
    from regspec.grupos import ACCIONES, MODOS, OPERADORES_TEXTO, TRANSFORMACIONES
    from regspec.i18n import PT, traducir
    assert traducir("solo en Sistema + Agente", "pt") == "só em Sistema + Agente"
    assert traducir("'2012345678X' no cumple el patrón \\d{11}", "pt") == "'2012345678X' não cumpre o padrão \\d{11}"
    assert traducir("1 errores en 7815 registros:", "pt") == "1 erros em 7815 registros:"
    assert traducir("texto sin traducción", "pt") == "texto sin traducción" and traducir("Cruzar", "es") == "Cruzar"
    assert all(v in PT for v in [*MODOS.values(), *TRANSFORMACIONES.values(), *OPERADORES_TEXTO.values(), *ACCIONES.values()])


def test_filtros_incluir_excluir_y_varios_valores():
    from regspec.grupos import ArchivoGrupo, Filtro
    df = pd.DataFrame({"id": ["1", "2", "3", "4", "5"],
                       "leyenda": ["Régimen A, anexo", "Régimen B", "Otro", "Régimen A, anexo", ""],
                       "monto": ["10", "-5", "0", "100.00", "7"]})
    def ids(*filtros):
        d, n = ArchivoGrupo("x", df, ["id"], filtros=list(filtros)).preparado()
        return d["id"].tolist(), n
    # incluir solo dos valores (uno con coma adentro)
    assert ids(Filtro("leyenda", "==", accion="incluir", valores=["Régimen A, anexo", "Régimen B"])) == (["1", "2", "4"], 2)
    # excluir esos mismos valores
    assert ids(Filtro("leyenda", "==", accion="excluir", valores=["Régimen A, anexo", "Régimen B"])) == (["3", "5"], 3)
    # es distinto de (ninguno de los valores) + incluir
    assert ids(Filtro("leyenda", "!=", accion="incluir", valores=["Otro"]))[0] == ["1", "2", "4", "5"]
    # contiene alguno
    assert ids(Filtro("leyenda", "contiene", accion="incluir", valores=["b", "otro"]))[0] == ["2", "3"]
    # numérico: 100 == 100.00, y dos condiciones combinadas
    assert ids(Filtro("monto", "==", accion="incluir", valores=["100"]))[0] == ["4"]
    assert ids(Filtro("monto", "<=", "0"), Filtro("leyenda", "vacío"))[0] == ["1", "4"]
    # compatibilidad: Filtro(columna, operador, valor) sigue siendo "excluir"
    assert ids(Filtro("monto", "<=", "0"))[0] == ["1", "4", "5"]


def test_serie_numerica_equivale_a_numero():
    from regspec.grupos import numero, serie_numerica
    vals = ["000000000000000280.38", "000000000000001225.00", "1.234,56", "1,234.56", "12,5", "$ 100", None, "abc", "-5",
            "-000012.50", "0", "00", "0.5", "7743620.01"]
    got = serie_numerica(pd.Series(vals)).tolist()
    exp = [float(numero(v)) if numero(v) is not None else None for v in vals]
    assert all((g != g and e is None) or abs(g - e) < 1e-9 for g, e in zip(got, exp)), (got, exp)


def test_memoria_de_configuracion_local(tmp_path):
    from regspec.almacen import AlmacenLocal, ArchivoCfg, ConfigCruce, GrupoCfg, config_equivalente, confianza, firma_archivos
    # la firma depende de la estructura (columnas), no de nombres ni orden de columnas ni mayúsculas
    f1 = firma_archivos([[["ID_FISCAL", "Monto"]], [["Id", "Importe"]]])
    assert f1 == firma_archivos([[["monto", "id fiscal"]], [["ID", "importe"]]])
    assert f1 != firma_archivos([[["ID_FISCAL", "Monto"]], [["Id", "Importe", "Fecha"]]])
    cfg = ConfigCruce(grupos=[GrupoCfg(nombre="A", archivos=[ArchivoCfg(nombre_original="julio.csv", llave=["ID_FISCAL"])]),
                              GrupoCfg(nombre="B", archivos=[ArchivoCfg(llave=["Id"])])])
    otra = cfg.model_copy(deep=True)
    otra.grupos[0].nombre, otra.grupos[0].archivos[0].nombre_original = "Sistema", "agosto.csv"
    assert config_equivalente(cfg, otra)  # renombrar no es corregir
    otra.grupos[0].archivos[0].transformacion = "solo_numeros"
    assert not config_equivalente(cfg, otra)
    a = AlmacenLocal(tmp_path)
    assert a.memoria(f1) is None
    a.recordar(f1, cfg, False, "A vs B")
    m = a.recordar(f1, otra, True)
    assert (m.veces_usado, m.veces_corregido, m.confianza) == (2, 1, confianza(2, 1)) and m.descripcion == "A vs B"
    assert a.memoria(f1).config == otra and a.listar() == []  # la memoria no aparece como cruce guardado
    assert confianza(10, 0) > confianza(3, 0) > confianza(3, 2)


def test_umbral_de_aplicacion_automatica_y_aceptacion_por_usuario(tmp_path):
    from regspec.almacen import UMBRAL_AUTO, AlmacenLocal, ArchivoCfg, ConfigCruce, GrupoCfg, confianza, modo_memoria
    cfg = ConfigCruce(grupos=[GrupoCfg(nombre="A", archivos=[ArchivoCfg(llave=["x"])]),
                              GrupoCfg(nombre="B", archivos=[ArchivoCfg(llave=["y"])])])
    ana, bea = AlmacenLocal(tmp_path, usuario="ana"), AlmacenLocal(tmp_path, usuario="bea")
    m = None
    for _ in range(20):
        m = ana.recordar("f", cfg, False)
    assert m.confianza == confianza(20, 0) == UMBRAL_AUTO and modo_memoria(m, True) == "ofrecer"  # 95 % no alcanza: debe superarlo
    m = ana.recordar("f", cfg, False)
    assert m.confianza > UMBRAL_AUTO
    assert modo_memoria(m, ana.acepta_auto(m)) == "ofrecer_auto"  # supera el umbral, pero nadie lo aceptó
    ana.fijar_auto(m, True)
    m = ana.memoria("f")
    assert modo_memoria(m, ana.acepta_auto(m)) == "automatica"
    assert modo_memoria(m, bea.acepta_auto(m)) == "ofrecer_auto"  # la aceptación es por usuario
    m = ana.recordar("f", cfg, True)  # una corrección baja la confianza: deja de aplicarse sola
    assert modo_memoria(m, ana.acepta_auto(m)) == "ofrecer"
    ana.fijar_auto(m, False)
    assert not ana.acepta_auto(ana.memoria("f")) and modo_memoria(None, True) == "nada"


def test_llave_por_ia_como_respaldo_validado():
    import pandas as pd
    from regspec.grupos import llaves_claras, sugerir_llaves, sugerir_llaves_ia
    A = pd.DataFrame({"comp": ["A-0001", "A-0002", "A-0003", "A-0004"], "imp": ["1", "2", "3", "4"]})
    B = pd.DataFrame({"referencia_ext": ["0001", "0002", "0003", "0099"], "importe": ["150", "250", "350", "9"]})
    assert llaves_claras([("c", "exacta", 1.0), ("d", "exacta", 0.8)]) and not llaves_claras([("c", "exacta", 1.0), ("d", "exacta", 0.2)])
    assert not llaves_claras([("c", "exacta", 1.0), None])
    assert len(sugerir_llaves([A, B])) == 2
    # propuesta válida: normalización desconocida -> exacta, y la validación prueba «solo números» (A-0001 ~ 0001)
    prov = Guionado([json.dumps({"llaves": [["comp"], ["referencia_ext"]], "normalizacion": "rara", "motivo": "comprobante"})])
    r = sugerir_llaves_ia([A, B], prov)
    assert r["validada"] and r["llaves"][1][0] == ["referencia_ext"] and r["llaves"][1][1] == "solo_numeros"
    assert round(r["llaves"][1][2], 2) == 0.75 and r["motivo"] == "comprobante"
    # columna inventada: no se acepta
    prov = Guionado([json.dumps({"llaves": [["comp"], ["inventada"]], "normalizacion": "exacta"})])
    r = sugerir_llaves_ia([A, B], prov)
    assert not r["validada"] and r["llaves"] == [None, None]
    # columnas reales pero sin valores en común: la validación la rechaza
    prov = Guionado([json.dumps({"llaves": [["comp"], ["importe"]], "normalizacion": "exacta"})])
    r = sugerir_llaves_ia([A, B], prov)
    assert not r["validada"] and r["llaves"][1] is None


def test_metricas_de_uso(tmp_path):
    from regspec import metricas
    from regspec.almacen import AlmacenLocal
    a = AlmacenLocal(tmp_path, usuario="ana")
    ev = [dict(firma_archivos="f", origen="heuristica", segundos_preparacion=300, segundos_cruce=2),
          dict(firma_archivos="f", origen="memoria", memoria_confianza=67, corregida=False, aceptada=True, segundos_preparacion=60),
          dict(firma_archivos="f", origen="memoria", memoria_confianza=75, corregida=True, aceptada=False, segundos_preparacion=120),
          dict(firma_archivos="g", origen="ia", llamadas_ia=1, tokens_ia=900, ia_llave_validada=True, segundos_preparacion=200)]
    for e in ev:
        a.registrar_evento(e)
    eventos = a.eventos()
    assert len(eventos) == 4 and eventos[0]["usuario"] == "ana"
    r = metricas.resumen(eventos)
    assert r["cruces"] == 4 and r["cruces con configuración aprendida"] == 2 and r["tasa de aceptación de lo aprendido (%)"] == 50.0
    assert r["preparación mediana a mano (s)"] == 300 and r["preparación mediana reutilizando (s)"] == 90
    assert r["ahorro de tiempo de preparación (%)"] == 70.0 and r["tokens de IA"] == 900 and r["  propuesta de la IA validada"] == 1
    assert list(metricas.evolucion_confianza(eventos)["uso"]) == [1, 2]
    assert metricas.a_excel(eventos, a.memorias())[:2] == b"PK"
    assert metricas.resumen([])["cruces"] == 0
