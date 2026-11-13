"""Parseo genérico de páginas académicas (SASA, SIGA, SIGED).

Los sitios devuelven HTML heterogéneo: a veces pares ``<b>Etiqueta</b>: valor``,
a veces tablas ``<td>Etiqueta</td><td>Valor</td>`` y a veces tablas con
encabezados. Este módulo concentra esa tolerancia para que los clientes de
plataforma solo se ocupen del transporte.
"""

from __future__ import annotations

from typing import Any, Iterable

from bs4 import BeautifulSoup

from ..models import AcademicRecord, Fuente
from ..normalization import (
    normalizar_nivel,
    normalizar_texto,
    parsear_calificacion,
)


ALIAS_NIVEL = (
    "NIVEL",
    "NIVEL EDUCATIVO",
    "ETAPA",
    "GRADO",
    "CURSO",
    "NIVEL ACADEMICO",
)

ALIAS_CALIFICACION = (
    "CALIFICACION",
    "PROMEDIO",
    "NOTA",
    "CALIF",
    "RESULTADO",
    "CALIFICACION FINAL",
)

ALIAS_SITUACION = (
    "SITUACION",
    "ESTATUS",
    "ESTADO",
)

ALIAS_ACADEMICOS = frozenset(ALIAS_NIVEL + ALIAS_CALIFICACION + ALIAS_SITUACION)

_FRASES_NO_ENCONTRADO = (
    "NO SASA",
    "NO SE ENCONTRO",
    "NO SE ENCONTRÓ",
    "NO EXISTE",
    "SIN REGISTRO",
    "NO HAY REGISTRO",
    "NO LOCALIZADO",
    "NO LOCALIZADA",
    "NO DISPONIBLE",
)


def limpiar_valor(valor: Any) -> str:
    if valor is None:
        return ""
    texto = str(valor).replace("\xa0", " ").strip()
    texto = texto.lstrip(":").strip()
    return " ".join(texto.split())


def normalizar_etiqueta(valor: Any) -> str:
    texto = normalizar_texto(valor)
    texto = texto.rstrip(":").strip()
    return " ".join(texto.split())


def _texto_siguiente(etiqueta) -> str:
    """Obtiene el valor que sigue a un ``<b>`` o celda de etiqueta."""

    for hermano in etiqueta.next_siblings:
        if getattr(hermano, "name", None) in {"b", "br", "tr", "table", "div"}:
            break
        texto = (
            hermano.get_text(" ", strip=True)
            if hasattr(hermano, "get_text")
            else str(hermano)
        )
        texto = limpiar_valor(texto)
        if texto:
            return texto
    return ""


def extraer_pares(html: str) -> dict[str, str]:
    """Extrae pares etiqueta/valor de ``<b>`` y de filas ``<td>``."""

    soup = BeautifulSoup(html, "html.parser")
    pares: dict[str, str] = {}

    for b_tag in soup.find_all("b"):
        etiqueta = normalizar_etiqueta(b_tag.get_text(" ", strip=True))
        if not etiqueta:
            continue
        valor = _texto_siguiente(b_tag)
        if valor and etiqueta not in pares:
            pares[etiqueta] = valor

    # Tablas de dos columnas: <td>Etapa</td><td>AVANZADO</td>
    for fila in soup.find_all("tr"):
        celdas = fila.find_all(["td", "th"], recursive=False)
        for indice, celda in enumerate(celdas[:-1]):
            # No confundir encabezados de tabla con pares etiqueta/valor.
            if celda.name == "th" or celdas[indice + 1].name == "th":
                continue
            etiqueta = normalizar_etiqueta(celda.get_text(" ", strip=True))
            if etiqueta not in ALIAS_ACADEMICOS:
                continue
            valor = limpiar_valor(celdas[indice + 1].get_text(" ", strip=True))
            if normalizar_etiqueta(valor) in ALIAS_ACADEMICOS:
                continue
            if valor and etiqueta not in pares:
                pares[etiqueta] = valor

    return pares


def _indice_columna(encabezados: list[str], alias: Iterable[str]) -> int | None:
    alias_set = set(alias)
    for indice, encabezado in enumerate(encabezados):
        if encabezado in alias_set:
            return indice
    # Tolerancia a encabezados compuestos ("Calificación final del módulo").
    for indice, encabezado in enumerate(encabezados):
        if any(a in encabezado for a in alias_set):
            return indice
    return None


def extraer_tablas(html: str) -> list[dict[str, str]]:
    """Extrae filas de tablas que tengan columnas de nivel y calificación."""

    soup = BeautifulSoup(html, "html.parser")
    registros: list[dict[str, str]] = []

    for tabla in soup.find_all("table"):
        filas = tabla.find_all("tr")
        if len(filas) < 2:
            continue

        encabezados: list[str] | None = None
        indice_nivel: int | None = None
        indice_calificacion: int | None = None
        inicio_datos = 0

        for posicion, fila in enumerate(filas[:3]):
            celdas = fila.find_all(["th", "td"], recursive=False)
            textos = [normalizar_etiqueta(c.get_text(" ", strip=True)) for c in celdas]
            posible_nivel = _indice_columna(textos, ALIAS_NIVEL)
            posible_calificacion = _indice_columna(textos, ALIAS_CALIFICACION)
            if posible_nivel is not None and posible_calificacion is not None:
                encabezados = textos
                indice_nivel = posible_nivel
                indice_calificacion = posible_calificacion
                inicio_datos = posicion + 1
                break

        if encabezados is None or indice_nivel is None or indice_calificacion is None:
            continue

        for fila in filas[inicio_datos:]:
            celdas = fila.find_all(["td", "th"], recursive=False)
            if len(celdas) <= max(indice_nivel, indice_calificacion):
                continue
            nivel = limpiar_valor(celdas[indice_nivel].get_text(" ", strip=True))
            calificacion = limpiar_valor(
                celdas[indice_calificacion].get_text(" ", strip=True)
            )
            if not nivel and not calificacion:
                continue
            registros.append(
                {"nivel": nivel, "calificacion": calificacion, "origen": "tabla"}
            )

    return registros


def _primer_valor(pares: dict[str, str], alias: Iterable[str]) -> str:
    for etiqueta in alias:
        if etiqueta in pares:
            return pares[etiqueta]
    return ""


def _detalles_academicos(pares: dict[str, str]) -> dict[str, str]:
    """Solo conserva campos académicos; nunca nombre, CURP, correo o teléfono."""

    detalles: dict[str, str] = {}
    for etiqueta in ALIAS_SITUACION:
        if etiqueta in pares:
            detalles["situacion"] = pares[etiqueta]
            break
    for etiqueta in ("MODELO", "COORDINACION", "FORMA DE ATENCION", "ID"):
        if etiqueta in pares:
            detalles[etiqueta.lower().replace(" ", "_")] = pares[etiqueta]
    return detalles


def extraer_registros(html: str, fuente: Fuente) -> list[AcademicRecord]:
    """Convierte el HTML en uno o más ``AcademicRecord`` normalizados."""

    pares = extraer_pares(html)
    crudos: list[dict[str, str]] = []

    nivel = _primer_valor(pares, ALIAS_NIVEL)
    calificacion = _primer_valor(pares, ALIAS_CALIFICACION)
    if nivel or calificacion:
        crudos.append(
            {
                "nivel": nivel,
                "calificacion": calificacion,
                "origen": "etiquetas",
            }
        )

    crudos.extend(extraer_tablas(html))

    registros: list[AcademicRecord] = []
    vistos: set[tuple[str, str]] = set()
    detalles = _detalles_academicos(pares)

    for crudo in crudos:
        nivel_raw = limpiar_valor(crudo.get("nivel"))
        cal_raw = limpiar_valor(crudo.get("calificacion"))
        clave = (normalizar_texto(nivel_raw), normalizar_texto(cal_raw))
        if clave in vistos:
            continue
        vistos.add(clave)
        registros.append(
            AcademicRecord(
                fuente=fuente,
                nivel=normalizar_nivel(nivel_raw),
                calificacion=parsear_calificacion(cal_raw),
                nivel_raw=nivel_raw,
                calificacion_raw=cal_raw,
                detalles=dict(detalles),
            )
        )

    return registros


def parece_no_encontrado(html: str) -> bool:
    texto = normalizar_texto(html)
    return any(frase in texto for frase in _FRASES_NO_ENCONTRADO)


def parece_login(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("input", attrs={"type": "password"}):
        return True
    texto = normalizar_texto(html)
    return "INICIAR SESION" in texto and "CONTRASENA" in texto


# ------------------------------------------------------------------
# Extracción de datos de contacto
# ------------------------------------------------------------------

ALIAS_TELEFONO = frozenset({
    "TELEFONO", "TEL", "TELEFONO CELULAR", "CELULAR", "MOVIL",
    "NUMERO DE TELEFONO", "TELEFONO MOVIL", "TELEFONO CEL",
})

ALIAS_DIRECCION = frozenset({
    "DIRECCION", "DOMICILIO", "DIRECCION DOMICILIO",
    "CALLE", "DOMICILIO PARTICULAR", "DIRECCION PARTICULAR",
})

ALIAS_NOMBRE = frozenset({
    "NOMBRE", "NOMBRE DEL PARTICIPANTE", "NOMBRE COMPLETO",
    "NOMBRE Y APELLIDOS", "APELLIDO Y NOMBRE",
})


def extraer_contacto(html: str) -> dict[str, str]:
    """Extrae teléfono, dirección y nombre del HTML de una plataforma.

    Busca pares etiqueta/valor en <b>, <strong>, <th>/<td> y tablas.
    Devuelve un diccionario con claves: telefono, direccion, nombre.
    """
    soup = BeautifulSoup(html, "html.parser")
    contacto: dict[str, str] = {}

    # Buscar en pares <b>etiqueta</b>: valor
    for b_tag in soup.find_all("b"):
        etiqueta = normalizar_etiqueta(b_tag.get_text(" ", strip=True))
        if not etiqueta:
            continue
        valor = _texto_siguiente(b_tag)
        if not valor:
            continue
        etiq_norm = normalizar_etiqueta(etiqueta)
        if etiq_norm in ALIAS_TELEFONO and "telefono" not in contacto:
            contacto["telefono"] = limpiar_valor(valor)
        elif etiq_norm in ALIAS_DIRECCION and "direccion" not in contacto:
            contacto["direccion"] = limpiar_valor(valor)
        elif etiq_norm in ALIAS_NOMBRE and "nombre" not in contacto:
            contacto["nombre"] = limpiar_valor(valor)

    # Buscar en tablas <td>Etiqueta</td><td>Valor</td>
    for fila in soup.find_all("tr"):
        celdas = fila.find_all(["td", "th"], recursive=False)
        for indice in range(len(celdas) - 1):
            celda_actual = celdas[indice]
            celda_siguiente = celdas[indice + 1]
            if celda_actual.name == "th" or celda_siguiente.name == "th":
                continue
            etiqueta = normalizar_etiqueta(celda_actual.get_text(" ", strip=True))
            if not etiqueta:
                continue
            etiq_norm = normalizar_etiqueta(etiqueta)
            valor = limpiar_valor(celda_siguiente.get_text(" ", strip=True))
            if not valor:
                continue
            if etiq_norm in ALIAS_TELEFONO and "telefono" not in contacto:
                contacto["telefono"] = valor
            elif etiq_norm in ALIAS_DIRECCION and "direccion" not in contacto:
                contacto["direccion"] = valor
            elif etiq_norm in ALIAS_NOMBRE and "nombre" not in contacto:
                contacto["nombre"] = valor

    return contacto


def normalizar_telefono_mexico(telefono: str) -> str:
    """Normaliza un teléfono mexicano al formato +52XXXXXXXXXX.

    Elimina espacios, guiones, paréntesis y prefijos nacionales (044, 045, 01).
    Si ya empieza con +52, lo devuelve igual.
    """
    if not telefono:
        return ""

    t = telefono.strip()
    # Quitar todo lo que no sea dígito o +
    t = "".join(c for c in t if c.isdigit() or c == "+")

    # Si ya tiene +52, devolverlo limpio
    if t.startswith("+52"):
        return t

    # Quitar código de país 52 si lo tiene sin +
    if t.startswith("52") and len(t) > 10:
        t = t[2:]

    # Quitar prefijos nacionales mexicanos: 044, 045, 01
    for prefijo in ("044", "045", "01"):
        if t.startswith(prefijo):
            t = t[len(prefijo):]
            break

    # Si tiene 10 dígitos, agregar +52
    if len(t) == 10 and t.isdigit():
        return f"+52{t}"

    # Si tiene 11 dígitos y empieza con 1 (teléfono fijo con larga distancia nacional)
    if len(t) == 11 and t.startswith("1") and t.isdigit():
        return f"+52{t}"

    return t
