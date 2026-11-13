"""Pruebas del parser genérico de HTML académico."""

from __future__ import annotations

from rfe_rastreador.models import Fuente, NivelAcademico
from rfe_rastreador.platforms.parsing import (
    extraer_pares,
    extraer_registros,
    extraer_tablas,
    parece_no_encontrado,
)


HTML_SASA = """
<html><body>
<table>
  <tr><td class="negras">Etapa</td><td class="normal">AVANZADO</td></tr>
  <tr><td class="normal"><b>Situación</b> : BAJA</td></tr>
  <tr><td class="normal"><b>Promedio</b> : 7.8</td></tr>
</table>
</body></html>
"""


HTML_SIGA = """
<html><body>
<table>
  <tr><th>Nivel</th><th>Calificación</th></tr>
  <tr><td>Primaria</td><td>7</td></tr>
  <tr><td>Secundaria</td><td>5</td></tr>
</table>
</body></html>
"""


def test_extraer_pares_sasa():
    pares = extraer_pares(HTML_SASA)
    assert pares["ETAPA"] == "AVANZADO"
    assert pares["SITUACION"] == "BAJA"
    assert pares["PROMEDIO"] == "7.8"


def test_extraer_registros_sasa():
    registros = extraer_registros(HTML_SASA, Fuente.SASA)
    assert len(registros) == 1
    assert registros[0].nivel is NivelAcademico.AVANZADO
    assert registros[0].calificacion == 7.8


def test_extraer_tabla_siga():
    filas = extraer_tablas(HTML_SIGA)
    assert len(filas) == 2
    assert filas[0]["nivel"] == "Primaria"
    assert filas[1]["calificacion"] == "5"


def test_extraer_registros_siga_multinivel():
    registros = extraer_registros(HTML_SIGA, Fuente.SIGA)
    assert len(registros) == 2
    assert {r.nivel for r in registros} == {
        NivelAcademico.PRIMARIA,
        NivelAcademico.SECUNDARIA,
    }


def test_detectar_no_encontrado():
    assert parece_no_encontrado("<html>NO SASA</html>")
    assert not parece_no_encontrado(HTML_SASA)


def test_extraer_curp_desde_etiqueta():
    from rfe_rastreador.platforms.siga import _extraer_curp

    assert _extraer_curp("<b>CURP:</b> AAGJ7703195R4") == "AAGJ7703195R4"
    assert (
        _extraer_curp("<td>CURP</td><td>ABCD010203HDFXXX01</td>")
        == "ABCD010203HDFXXX01"
    )
    assert _extraer_curp("<div>Sin CURP</div>") == ""
