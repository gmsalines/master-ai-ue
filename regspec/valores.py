"""Conversión campo <-> texto, para ancho fijo, delimitado y XML.

`formato` es el modo de representación: "ancho_fijo" (decimales implícitos), "xml" y "delimitado"
(punto decimal explícito) o "delimitado_fijo" (como delimitado, pero cada campo con su ancho exacto).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

from .dsl import Campo, mascara_a_strftime


class ErrorValor(ValueError):
    pass


def _fecha_desde_texto(campo: Campo, txt: str) -> date:
    fmt = mascara_a_strftime(campo.formato_fecha or "")
    try:
        d = datetime.strptime(txt, fmt)
    except ValueError:
        raise ErrorValor(f"'{txt}' no es una fecha válida con formato {campo.formato_fecha}")
    if datetime.strftime(d, fmt) != txt:  # strptime acepta días sin cero a la izquierda, etc.
        raise ErrorValor(f"'{txt}' no respeta exactamente el formato {campo.formato_fecha}")
    return d.date()


def _chequear_dominio(campo: Campo, txt: str) -> None:
    if campo.valores_permitidos:
        permitidos = [v.strip() for v in campo.valores_permitidos]
        if txt not in permitidos:
            ok = False
            if txt.isdigit():  # "1" vs "01" en campos numéricos
                ok = any(p.isdigit() and int(p) == int(txt) for p in permitidos)
            if not ok:
                raise ErrorValor(f"'{txt}' no está entre los valores permitidos {permitidos}")
    if campo.patron and not re.fullmatch(campo.patron, txt):
        raise ErrorValor(f"'{txt}' no cumple el patrón {campo.patron}")


def ancho_texto(campo: Campo) -> int:
    """Ancho en caracteres de un campo en modo delimitado_fijo."""
    if campo.tipo == "fecha" and campo.formato_fecha:
        return len(campo.formato_fecha)
    return campo.longitud + (1 if campo.tipo == "decimal" and campo.decimales else 0)


def desde_texto(campo: Campo, crudo: Optional[str], formato: str) -> Any:
    """Convierte el texto de un campo a valor Python. Devuelve None si no fue informado."""
    ancho_fijo = formato == "ancho_fijo"
    if formato == "delimitado_fijo" and crudo is not None and crudo.strip() != "" and len(crudo) != ancho_texto(campo):
        raise ErrorValor(f"'{crudo}' mide {len(crudo)} caracteres y el campo tiene ancho {ancho_texto(campo)}")
    if crudo is None:
        txt = ""
    elif ancho_fijo and campo.tipo in ("alfanumerico", "constante"):
        txt = crudo.rstrip(" ")
    else:
        txt = crudo.strip()

    if txt == "":
        if campo.obligatorio:
            raise ErrorValor("campo obligatorio no informado")
        return None

    if campo.tipo == "constante":
        esperado = (campo.valor_constante or "").strip()
        if txt.strip() != esperado:
            raise ErrorValor(f"se esperaba el valor fijo '{esperado}' y se encontró '{txt}'")
        return txt.strip()

    if campo.tipo == "alfanumerico":
        if not ancho_fijo and len(txt) > campo.longitud:
            raise ErrorValor(f"longitud {len(txt)} mayor que la máxima ({campo.longitud})")
        _chequear_dominio(campo, txt)
        return txt

    if campo.tipo == "fecha":
        return _fecha_desde_texto(campo, txt)

    # numérico / decimal
    if ancho_fijo:
        if not crudo.isdigit():
            raise ErrorValor(f"'{crudo}' contiene caracteres no numéricos")
        _chequear_dominio(campo, txt)
        entero = int(txt)
        if campo.tipo == "decimal":
            return Decimal(entero).scaleb(-campo.decimales)
        return Decimal(entero)
    # XML y delimitado: representación con punto decimal explícito
    if not re.fullmatch(r"-?\d+(\.\d+)?", txt):
        raise ErrorValor(f"'{txt}' no es un número válido")
    if campo.tipo == "numerico" and "." in txt:
        raise ErrorValor(f"'{txt}' debe ser entero")
    try:
        v = Decimal(txt)
    except InvalidOperation:
        raise ErrorValor(f"'{txt}' no es un número válido")
    if campo.tipo == "decimal" and -v.as_tuple().exponent > campo.decimales:
        raise ErrorValor(f"'{txt}' tiene más de {campo.decimales} decimales")
    digitos = len(txt.replace("-", "").replace(".", "").lstrip("0") or "0")
    if digitos > campo.longitud:
        raise ErrorValor(f"'{txt}' supera los {campo.longitud} dígitos")
    _chequear_dominio(campo, txt)
    return v


def a_texto(campo: Campo, valor: Any, formato: str) -> Optional[str]:
    """Convierte un valor Python al texto del campo.

    Ancho fijo: devuelve exactamente `longitud` caracteres.
    XML: devuelve el texto del elemento, o None si el campo opcional no se informa.
    """
    ancho_fijo = formato == "ancho_fijo"
    if campo.tipo == "constante" and valor is None:
        valor = campo.valor_constante
    if valor is None or (isinstance(valor, str) and valor.strip() == ""):
        if campo.obligatorio:
            raise ErrorValor(f"campo obligatorio '{campo.nombre}' sin valor")
        if ancho_fijo or formato == "delimitado_fijo":
            return " " * (campo.longitud if ancho_fijo else ancho_texto(campo))
        return "" if formato == "delimitado" else None

    if campo.tipo in ("alfanumerico", "constante"):
        s = str(valor)
        if len(s) > campo.longitud:
            raise ErrorValor(f"'{s}' supera la longitud {campo.longitud} del campo '{campo.nombre}'")
        return s.ljust(campo.longitud) if ancho_fijo or formato == "delimitado_fijo" else s

    if campo.tipo == "fecha":
        if isinstance(valor, str):
            valor = coercer(campo, valor)
        if isinstance(valor, datetime):
            valor = valor.date()
        return valor.strftime(mascara_a_strftime(campo.formato_fecha or ""))

    try:
        d = Decimal(str(valor))
    except InvalidOperation:
        raise ErrorValor(f"'{valor}' no es numérico (campo '{campo.nombre}')")
    dec = campo.decimales if campo.tipo == "decimal" else 0
    d = d.quantize(Decimal(1).scaleb(-dec), rounding=ROUND_HALF_UP)
    if ancho_fijo:
        if d < 0:
            raise ErrorValor(f"valor negativo {d} en campo sin signo '{campo.nombre}'")
        entero = int(d.scaleb(dec))
        s = str(entero).zfill(campo.longitud)
        if len(s) > campo.longitud:
            raise ErrorValor(f"{d} desborda los {campo.longitud} dígitos del campo '{campo.nombre}'")
        return s
    s = f"{d:.{dec}f}"
    if len(s.replace("-", "").replace(".", "").lstrip("0") or "0") > campo.longitud:
        raise ErrorValor(f"{d} supera los {campo.longitud} dígitos del campo '{campo.nombre}'")
    if formato == "delimitado_fijo":
        s = ("-" if s.startswith("-") else "") + s.lstrip("-").zfill(ancho_texto(campo) - (1 if s.startswith("-") else 0))
    return s


def coercer(campo: Campo, valor: Any) -> Any:
    """Convierte datos de entrada (p. ej. texto de un CSV) al tipo Python del campo."""
    if valor is None:
        return None
    if isinstance(valor, float) and valor != valor:  # NaN de pandas
        return None
    if campo.tipo in ("numerico", "decimal"):
        if isinstance(valor, str):
            t = valor.strip().replace(" ", "")
            if t == "":
                return None
            if "," in t and "." in t:  # 1.234,56 -> 1234.56
                t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
            elif "," in t:
                t = t.replace(",", ".")
            try:
                return Decimal(t)
            except InvalidOperation:
                raise ErrorValor(f"'{valor}' no es numérico (campo '{campo.nombre}')")
        return Decimal(str(valor))
    if campo.tipo == "fecha":
        if isinstance(valor, datetime):
            return valor.date()
        if isinstance(valor, date):
            return valor
        t = str(valor).strip()
        if t == "":
            return None
        try:
            return _fecha_desde_texto(campo, t)
        except ErrorValor:
            pass
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d", "%Y-%m"):
            try:
                return datetime.strptime(t, fmt).date()
            except ValueError:
                continue
        raise ErrorValor(f"'{valor}' no es una fecha reconocible (campo '{campo.nombre}')")
    return valor if not isinstance(valor, str) else valor.strip()
