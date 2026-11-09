"""Normalización de datos académicos y llaves RFE/CURP.

Estas funciones son puras (sin red, sin Excel) para poder probar las reglas de
negocio de forma aislada.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from .models import NivelAcademico


_VALORES_VACIOS = {
    "",
    "-",
    "--",
    "N/A",
    "NA",
    "NP",
    "NONE",
    "NULL",
    "NAN",
    "<NA>",
    "SIN CALIFICACION",
    "SIN CALIFICACIÓN",
    "SIN DATO",
    "SIN DATOS",
    "NO PRESENTADO",
    "NO PRESENTADA",
    "NO APLICA",
    "∅",
    "Ø",
}

_MAPA_NIVELES = {
    "INICIAL": NivelAcademico.INICIAL,
    "NIVEL INICIAL": NivelAcademico.INICIAL,
    "ALFABETIZACION": NivelAcademico.INICIAL,
    "ALFABETIZACIÓN": NivelAcademico.INICIAL,
    "INTERMEDIO": NivelAcademico.INTERMEDIO,
    "NIVEL INTERMEDIO": NivelAcademico.INTERMEDIO,
    "AVANZADO": NivelAcademico.AVANZADO,
    "NIVEL AVANZADO": NivelAcademico.AVANZADO,
    "PRIMARIA": NivelAcademico.PRIMARIA,
    "PRIM": NivelAcademico.PRIMARIA,
    "EDUCACION PRIMARIA": NivelAcademico.PRIMARIA,
    "EDUCACIÓN PRIMARIA": NivelAcademico.PRIMARIA,
    "SECUNDARIA": NivelAcademico.SECUNDARIA,
    "SEC": NivelAcademico.SECUNDARIA,
    "EDUCACION SECUNDARIA": NivelAcademico.SECUNDARIA,
    "EDUCACIÓN SECUNDARIA": NivelAcademico.SECUNDARIA,
}


def sin_acentos(texto: str) -> str:
    """Quita acentos y diacríticos conservando el resto del texto."""

    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def normalizar_texto(valor: Any) -> str:
    """Devuelve texto en mayúsculas, sin acentos y con espacios colapsados."""

    if valor is None:
        return ""
    if isinstance(valor, float) and math.isnan(valor):
        return ""
    texto = sin_acentos(str(valor)).strip().upper()
    return re.sub(r"\s+", " ", texto)


def _es_vacio(valor: Any) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float) and math.isnan(valor):
        return True
    return normalizar_texto(valor) in _VALORES_VACIOS


def parsear_calificacion(valor: Any) -> float | None:
    """Convierte una calificación heterogénea a float en el rango 0..10.

    Acepta números, cadenas con coma decimal y valores vacíos/NP. Devuelve
    ``None`` cuando el dato no es una calificación utilizable.
    """

    if _es_vacio(valor) or isinstance(valor, bool):
        return None

    if isinstance(valor, (int, float)):
        numero = float(valor)
        if math.isnan(numero) or math.isinf(numero):
            return None
        return numero if 0.0 <= numero <= 10.0 else None

    texto = normalizar_texto(valor).replace(",", ".")
    # Algunos reportes agregan sufijos como "6.0/10" o "6 DE 10".
    coincidencia = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(?:/\s*10|DE\s*10)?", texto
    )
    if not coincidencia:
        return None

    numero = float(coincidencia.group(1))
    return numero if 0.0 <= numero <= 10.0 else None


def normalizar_nivel(valor: Any) -> NivelAcademico:
    """Mapea el nivel/etapa de SASA o SIGA al enum interno."""

    texto = normalizar_texto(valor)
    if not texto:
        return NivelAcademico.DESCONOCIDO

    if texto in _MAPA_NIVELES:
        return _MAPA_NIVELES[texto]

    # Tolerancia a etiquetas compuestas: "PRIMARIA (PEC)", "NIVEL: AVANZADO".
    for etiqueta, nivel in _MAPA_NIVELES.items():
        if etiqueta and etiqueta in texto:
            return nivel

    return NivelAcademico.DESCONOCIDO


def es_aprobatoria(calificacion: float | None, minimo: float = 6.0) -> bool:
    """Una calificación es aprobatoria si existe y es mayor o igual al mínimo."""

    return calificacion is not None and calificacion >= minimo


def es_rango_no_acreditado(calificacion: float | None) -> bool:
    """Rango estricto 0 < calificación < 6."""

    return calificacion is not None and 0.0 < calificacion < 6.0


def normalizar_rfe(valor: Any) -> str:
    """Normaliza una llave RFE/CURP proveniente de Excel.

    Evita los errores clásicos de pandas: floats como ``123.0`` y valores
    ``NaN`` convertidos a la cadena ``"nan"``.
    """

    if valor is None:
        return ""
    if isinstance(valor, float):
        if math.isnan(valor):
            return ""
        if valor.is_integer():
            return str(int(valor)).strip().upper()

    texto = str(valor).strip().upper()
    if texto in {"NAN", "NONE", "<NA>"}:
        return ""
    # Excel a veces guarda una llave numérica como "1234567890123.0".
    if re.fullmatch(r"\d+\.0", texto):
        texto = texto[:-2]
    return re.sub(r"\s+", "", texto)


def es_rfe_valida(valor: Any, longitudes: tuple[int, ...] = (13, 18)) -> bool:
    """Valida que la llave tenga una longitud permitida y sea alfanumérica."""

    texto = normalizar_rfe(valor)
    return len(texto) in longitudes and texto.isalnum()


_CURP_RE = re.compile(
    r"^[A-Z]{4}\d{6}[HM]"
    r"(?:AS|BC|BS|CC|CS|CH|CL|CM|DF|DG|GT|GR|HG|JC|MC|MN|MS|NT|NL|"
    r"OC|PL|QT|QR|SP|SL|SR|TC|TS|TL|VZ|YN|ZS|NE)"
    r"[B-DF-HJ-NP-TV-Z]{3}[0-9A-Z]\d$"
)


def es_curp(valor: Any) -> bool:
    """True si el valor tiene la estructura de una CURP mexicana."""

    return bool(_CURP_RE.fullmatch(normalizar_texto(valor)))


def extraer_curp(datos: dict[str, Any], clave_principal: Any = "") -> str:
    """Encuentra una CURP en la fila, sin confundirla con el RFE.

    SIGED necesita CURP aunque la llave principal del Excel sea el RFE. Se
    revisa primero la llave principal, luego una columna llamada ``CURP`` y,
    por último, cualquier valor de la fila con estructura de CURP.
    """

    principal = normalizar_rfe(clave_principal)
    if es_curp(principal):
        return principal

    for nombre, valor in datos.items():
        if "CURP" in normalizar_texto(nombre):
            candidato = normalizar_rfe(valor)
            if es_curp(candidato):
                return candidato

    for valor in datos.values():
        candidato = normalizar_rfe(valor)
        if es_curp(candidato):
            return candidato

    return ""
