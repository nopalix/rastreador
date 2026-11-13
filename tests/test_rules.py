"""Pruebas del motor de reglas y de la normalización."""

from __future__ import annotations

import pytest

from rfe_rastreador.models import (
    AcademicRecord,
    Categoria,
    EstadoConsulta,
    Fuente,
    NivelAcademico,
    PlatformResult,
)
from rfe_rastreador.normalization import (
    es_curp,
    es_rfe_valida,
    extraer_curp,
    normalizar_nivel,
    normalizar_rfe,
    parsear_calificacion,
)
from rfe_rastreador.rules import evaluar_registro


def registro(
    fuente: Fuente,
    nivel: NivelAcademico,
    calificacion: float | None,
    nivel_raw: str = "",
    calificacion_raw: str = "",
) -> AcademicRecord:
    return AcademicRecord(
        fuente=fuente,
        nivel=nivel,
        calificacion=calificacion,
        nivel_raw=nivel_raw or nivel.value,
        calificacion_raw=calificacion_raw,
    )


def resultado(
    fuente: Fuente,
    *registros: AcademicRecord,
    estado: EstadoConsulta = EstadoConsulta.OK,
) -> PlatformResult:
    return PlatformResult(fuente=fuente, estado=estado, registros=list(registros))


def evaluar(*resultados: PlatformResult) -> Categoria:
    mapa = {r.fuente: r for r in resultados}
    return evaluar_registro("AAGJ7703195R4", mapa).categoria


# ---------------------------------------------------------------------------
# Regla 1: Basura
# ---------------------------------------------------------------------------
def test_basura_sasa_avanzado_aprobado():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.AVANZADO, 7.0),
            ),
            resultado(Fuente.SIGA, estado=EstadoConsulta.NO_ENCONTRADO),
        )
        is Categoria.BASURA
    )


def test_basura_sasa_secundaria_aprobada():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.SECUNDARIA, 6.0),
            ),
            resultado(Fuente.SIGA, estado=EstadoConsulta.NO_ENCONTRADO),
        )
        is Categoria.BASURA
    )


def test_basura_sasa_secundaria_reprobada_no_es_basura():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.SECUNDARIA, 5.9),
            ),
        )
        is Categoria.NO_ACREDITADO
    )


def test_basura_siga_secundaria_aprobada():
    assert (
        evaluar(
            resultado(Fuente.SASA, estado=EstadoConsulta.NO_ENCONTRADO),
            resultado(
                Fuente.SIGA,
                registro(Fuente.SIGA, NivelAcademico.SECUNDARIA, 6.0),
            ),
        )
        is Categoria.BASURA
    )


def test_basura_exige_seis_o_mas():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.AVANZADO, 5.9),
            ),
        )
        is Categoria.NO_ACREDITADO
    )


def test_basura_tiene_prioridad_sobre_continuidad():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.INICIAL, 7.0),
                registro(Fuente.SASA, NivelAcademico.SECUNDARIA, 7.0),
            ),
            resultado(
                Fuente.SIGA,
                registro(Fuente.SIGA, NivelAcademico.PRIMARIA, 7.0),
            ),
        )
        is Categoria.BASURA
    )


# ---------------------------------------------------------------------------
# Regla 2: Continuidad
# ---------------------------------------------------------------------------
def test_continuidad_primaria_siga_mas_inicial_sasa():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.INICIAL, 6.0),
            ),
            resultado(
                Fuente.SIGA,
                registro(Fuente.SIGA, NivelAcademico.PRIMARIA, 7.5),
            ),
        )
        is Categoria.CONTINUIDAD
    )


def test_continuidad_acepta_intermedio():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.INTERMEDIO, 8.0),
            ),
            resultado(
                Fuente.SIGA,
                registro(Fuente.SIGA, NivelAcademico.PRIMARIA, 6.0),
            ),
        )
        is Categoria.CONTINUIDAD
    )


def test_continuidad_requiere_ambos_lados():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.INICIAL, 8.0),
            ),
            resultado(
                Fuente.SIGA,
                registro(Fuente.SIGA, NivelAcademico.PRIMARIA, 5.0),
            ),
        )
        is Categoria.NO_ACREDITADO
    )


# ---------------------------------------------------------------------------
# Regla 3: No acreditado
# ---------------------------------------------------------------------------
def test_no_acreditado_con_nivel_vacio_en_siga():
    """El rango de calificación debe evaluarse antes que el nivel."""

    assert (
        evaluar(
            resultado(Fuente.SASA, estado=EstadoConsulta.NO_ENCONTRADO),
            resultado(
                Fuente.SIGA,
                registro(Fuente.SIGA, NivelAcademico.DESCONOCIDO, 4.0),
            ),
        )
        is Categoria.NO_ACREDITADO
    )


def test_no_acreditado_prioridad_sobre_no_presentado():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.INICIAL, None),
            ),
            resultado(
                Fuente.SIGA,
                registro(Fuente.SIGA, NivelAcademico.PRIMARIA, 5.0),
            ),
        )
        is Categoria.NO_ACREDITADO
    )


def test_cero_no_es_no_acreditado():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.INICIAL, 0.0),
            ),
        )
        is Categoria.NO_PRESENTADO
    )


# ---------------------------------------------------------------------------
# Regla 4: No presentado
# ---------------------------------------------------------------------------
def test_no_presentado_con_calificacion_vacia():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.INTERMEDIO, None),
            ),
        )
        is Categoria.NO_PRESENTADO
    )


def test_no_presentado_con_calificacion_cero():
    assert (
        evaluar(
            resultado(
                Fuente.SIGA,
                registro(Fuente.SIGA, NivelAcademico.PRIMARIA, 0.0),
            ),
        )
        is Categoria.NO_PRESENTADO
    )


def test_no_presentado_requiere_nivel_asignado():
    assert (
        evaluar(
            resultado(
                Fuente.SIGA,
                registro(Fuente.SIGA, NivelAcademico.DESCONOCIDO, None),
            ),
        )
        is Categoria.REVISION_MANUAL
    )


# ---------------------------------------------------------------------------
# Fallbacks
# ---------------------------------------------------------------------------
def test_sin_datos_cuando_no_hay_registros():
    assert (
        evaluar(
            resultado(Fuente.SASA, estado=EstadoConsulta.NO_ENCONTRADO),
            resultado(Fuente.SIGA, estado=EstadoConsulta.NO_ENCONTRADO),
        )
        is Categoria.SIN_DATOS
    )


def test_error_cuando_todas_las_consultas_fallan():
    assert (
        evaluar(
            resultado(Fuente.SASA, estado=EstadoConsulta.ERROR),
            resultado(Fuente.SIGA, estado=EstadoConsulta.ERROR),
        )
        is Categoria.ERROR
    )


def test_revision_manual_con_datos_fuera_de_reglas():
    assert (
        evaluar(
            resultado(
                Fuente.SASA,
                registro(Fuente.SASA, NivelAcademico.DESCONOCIDO, 9.0),
            ),
        )
        is Categoria.REVISION_MANUAL
    )


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (6, 6.0),
        (6.0, 6.0),
        ("6,5", 6.5),
        ("7.8", 7.8),
        (" 8 ", 8.0),
        ("6.0/10", 6.0),
        ("", None),
        ("NP", None),
        ("Sin calificación", None),
        ("-", None),
        (None, None),
        (float("nan"), None),
        (0, 0.0),
        (11, None),
        (True, None),
    ],
)
def test_parsear_calificacion(entrada, esperado):
    assert parsear_calificacion(entrada) == esperado


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Avanzado", NivelAcademico.AVANZADO),
        ("NIVEL AVANZADO", NivelAcademico.AVANZADO),
        ("Inicial", NivelAcademico.INICIAL),
        ("Intermedio", NivelAcademico.INTERMEDIO),
        ("Primaria", NivelAcademico.PRIMARIA),
        ("Educación Secundaria", NivelAcademico.SECUNDARIA),
        ("", NivelAcademico.DESCONOCIDO),
        (None, NivelAcademico.DESCONOCIDO),
        ("Otro", NivelAcademico.DESCONOCIDO),
    ],
)
def test_normalizar_nivel(entrada, esperado):
    assert normalizar_nivel(entrada) is esperado


def test_normalizar_rfe_y_validacion():
    assert normalizar_rfe(" aagj7703195r4 ") == "AAGJ7703195R4"
    assert normalizar_rfe(123.0) == "123"
    assert es_rfe_valida("AAGJ7703195R4")
    assert not es_rfe_valida("123")


def test_es_curp_y_extraccion_desde_la_fila():
    curp = "AAGJ770319HGTXXX00"

    assert es_curp(curp.lower())
    assert not es_curp("AAGJ7703195R4")
    assert extraer_curp({"RFE": "AAGJ7703195R4", "CURP": curp}) == curp
    assert extraer_curp({"RFE": "AAGJ7703195R4", "otro": curp}) == curp
    assert extraer_curp({"RFE": curp}) == curp
    assert extraer_curp({"RFE": "AAGJ7703195R4"}) == ""


# ---------------------------------------------------------------------------
# Regla 1: BASURA cuando la CURP de SIGA es idéntica al RFE
# ---------------------------------------------------------------------------
def _registro_siga_con_curp(
    curp: str,
    nivel: NivelAcademico = NivelAcademico.DESCONOCIDO,
    calificacion: float | None = None,
) -> AcademicRecord:
    return AcademicRecord(
        fuente=Fuente.SIGA,
        nivel=nivel,
        calificacion=calificacion,
        nivel_raw=nivel.value if nivel is not NivelAcademico.DESCONOCIDO else "",
        calificacion_raw="" if calificacion is None else str(calificacion),
        detalles={"curp": curp},
    )


def test_basura_cuando_curp_es_igual_al_rfe():
    rfe = "AAGJ7703195R4"
    evaluacion = evaluar_registro(
        rfe,
        {Fuente.SIGA: resultado(Fuente.SIGA, _registro_siga_con_curp(rfe))},
    )
    assert evaluacion.categoria is Categoria.BASURA
    assert evaluacion.regla_id == "R1_BASURA_CURP_IGUAL_RFE"


def test_basura_cuando_curp_es_igual_al_rfe_sin_calificacion():
    rfe = "AAGJ7703195R4"
    registro_siga = _registro_siga_con_curp(
        rfe, nivel=NivelAcademico.SECUNDARIA, calificacion=None
    )
    evaluacion = evaluar_registro(
        rfe, {Fuente.SIGA: resultado(Fuente.SIGA, registro_siga)}
    )
    assert evaluacion.categoria is Categoria.BASURA
    assert evaluacion.regla_id == "R1_BASURA_CURP_IGUAL_RFE"


def test_basura_curp_igual_rfe_es_case_insensitive():
    rfe = "AAGJ7703195R4"
    evaluacion = evaluar_registro(
        rfe,
        {Fuente.SIGA: resultado(Fuente.SIGA, _registro_siga_con_curp(rfe.lower()))},
    )
    assert evaluacion.categoria is Categoria.BASURA
    assert evaluacion.regla_id == "R1_BASURA_CURP_IGUAL_RFE"


def test_curp_distinta_no_activa_regla_curp():
    rfe = "AAGJ7703195R4"
    registro_siga = _registro_siga_con_curp("ABCD010203HDFXXX01")
    evaluacion = evaluar_registro(
        rfe, {Fuente.SIGA: resultado(Fuente.SIGA, registro_siga)}
    )
    assert evaluacion.categoria is not Categoria.BASURA
