"""Pruebas de lectura/escritura de Excel."""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("openpyxl")

from rfe_rastreador.excel_io import (  # noqa: E402
    COLUMNA_RFE_INTERNA,
    construir_dataframe_salida,
    escribir_salidas,
    leer_excel,
)
from rfe_rastreador.models import (  # noqa: E402
    AcademicRecord,
    Categoria,
    EstadoConsulta,
    Evaluation,
    Fuente,
    NivelAcademico,
    PlatformResult,
)


def crear_excel(tmp_path):
    ruta = tmp_path / "entrada.xlsx"
    pd.DataFrame(
        {
            "RFE": ["AAGJ7703195R4", "AAGJ7703195R4", "123", "BBBB800101AAA"],
            "Nombre": ["Ana", "Ana repetida", "Inválido", "Luis"],
        }
    ).to_excel(ruta, index=False)
    return ruta


def test_leer_excel_deduplica_y_separa_invalidos(tmp_path):
    lectura = leer_excel(crear_excel(tmp_path))

    assert lectura.columna_rfe == "RFE"
    assert len(lectura.registros) == 2
    assert lectura.duplicados == 1
    assert len(lectura.invalidos) == 1
    assert lectura.registros[0].rfe == "AAGJ7703195R4"


def crear_excel_con_zonas(tmp_path):
    ruta = tmp_path / "zonas.xlsx"
    pd.DataFrame(
        {
            "CVE ZONA": [1101, 1101, 1111, 1102, 1111, 1101],
            "RFE": [
                "AAGJ7703195R4",
                "AAGJ7703195R4",
                "BBBB800101AAA",
                "CCCC800101BBB",
                "BBBB800101AAA",
                "DDDD800101CCC",
            ],
            "Nombre": ["Uno", "Uno repetido", "Dos", "Fuera", "Dos repetido", "Tres"],
        }
    ).to_excel(ruta, index=False)
    return ruta


def test_leer_excel_filtra_zonas_antes_de_deduplicar(tmp_path):
    lectura = leer_excel(crear_excel_con_zonas(tmp_path), zonas=("1101", "1111"))

    assert {r.rfe for r in lectura.registros} == {
        "AAGJ7703195R4",
        "BBBB800101AAA",
        "DDDD800101CCC",
    }
    assert len(lectura.registros) == 3
    assert lectura.duplicados == 2
    assert len(lectura.invalidos) == 0
    assert all(r.datos["CVE ZONA"] in (1101, 1111) for r in lectura.registros)


def test_leer_excel_normaliza_zonas_numericas_y_texto(tmp_path):
    ruta = tmp_path / "zonas-mixtas.xlsx"
    pd.DataFrame(
        {
            "CVE ZONA": [1101, "1111", 1101.0, "1102"],
            "RFE": [
                "AAGJ7703195R4",
                "BBBB800101AAA",
                "CCCC800101BBB",
                "DDDD800101CCC",
            ],
        }
    ).to_excel(ruta, index=False)

    lectura = leer_excel(ruta, zonas=("1101", "1111"))

    assert len(lectura.registros) == 3
    assert {r.rfe for r in lectura.registros} == {
        "AAGJ7703195R4",
        "BBBB800101AAA",
        "CCCC800101BBB",
    }


def test_leer_excel_sin_zonas_conserva_todas_las_filas(tmp_path):
    lectura = leer_excel(crear_excel_con_zonas(tmp_path))

    assert len(lectura.registros) == 4
    assert lectura.duplicados == 2


def test_leer_excel_zona_inexistente_falla(tmp_path):
    from rfe_rastreador.errors import ExcelError

    with pytest.raises(ExcelError, match="columna de zona"):
        leer_excel(
            crear_excel_con_zonas(tmp_path),
            columna_zona="NO EXISTE",
            zonas=("1101",),
        )


def test_construir_dataframe_y_escribir(tmp_path):
    lectura = leer_excel(crear_excel(tmp_path))
    evaluacion = Evaluation(
        rfe="AAGJ7703195R4",
        categoria=Categoria.NO_ACREDITADO,
        regla_id="R3_NO_ACREDITADO",
        motivo="prueba",
        resultados={
            Fuente.SASA: PlatformResult(
                fuente=Fuente.SASA,
                estado=EstadoConsulta.OK,
                registros=[
                    AcademicRecord(
                        fuente=Fuente.SASA,
                        nivel=NivelAcademico.INICIAL,
                        calificacion=5.0,
                        nivel_raw="INICIAL",
                        calificacion_raw="5",
                    )
                ],
            )
        },
    )

    dataframe = construir_dataframe_salida([evaluacion], lectura)
    assert len(dataframe) == 1
    assert dataframe.iloc[0]["Categoria"] == "NO_ACREDITADO"
    assert dataframe.iloc[0]["SASA_Calificacion"] == "5"
    assert COLUMNA_RFE_INTERNA not in dataframe.columns

    salida = escribir_salidas(
        [evaluacion], lectura, tmp_path / "salida", "run-test"
    )
    assert (salida / "NO_ACREDITADO.xlsx").exists()
    assert (salida / "TODOS.xlsx").exists()
    assert (salida / "RESUMEN.xlsx").exists()
    assert (salida / "INVALIDOS.xlsx").exists()
