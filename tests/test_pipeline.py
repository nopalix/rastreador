"""Prueba de integración del pipeline con clientes simulados (sin red)."""

from __future__ import annotations

import pytest

from rfe_rastreador.config import Settings
from rfe_rastreador.models import (
    AcademicRecord,
    EstadoConsulta,
    Fuente,
    NivelAcademico,
    PlatformResult,
)
from rfe_rastreador.pipeline import Pipeline
from rfe_rastreador.privacy import PrivacyManager


pd = pytest.importorskip("pandas")
pytest.importorskip("openpyxl")


class ClienteFalso:
    def __init__(self, fuente, resultados_por_rfe, configurado=True):
        self.fuente = fuente
        self.resultados_por_rfe = resultados_por_rfe
        self.configurado = configurado
        self.llamadas = 0
        self.claves: list[str] = []

    def consultar(self, rfe):
        self.llamadas += 1
        self.claves.append(rfe)
        return self.resultados_por_rfe.get(
            rfe,
            PlatformResult(fuente=self.fuente, estado=EstadoConsulta.NO_ENCONTRADO),
        )


class SesionesFalsas:
    def close_all(self):
        pass


def reg(fuente, nivel, calificacion):
    return AcademicRecord(
        fuente=fuente,
        nivel=nivel,
        calificacion=calificacion,
        nivel_raw=nivel.value,
        calificacion_raw="" if calificacion is None else str(calificacion),
    )


def test_pipeline_clasifica_y_reanuda(tmp_path):
    ruta = tmp_path / "entrada.xlsx"
    pd.DataFrame(
        {
            "RFE": [
                "AAGJ7703195R4",
                "BBBB800101AAA",
                "CCCC900101CCC",
                "DDDD850101DDD",
            ],
            "Nombre": ["Uno", "Dos", "Tres", "Cuatro"],
        }
    ).to_excel(ruta, index=False)

    sasa = {
        "AAGJ7703195R4": PlatformResult(
            Fuente.SASA,
            EstadoConsulta.OK,
            [reg(Fuente.SASA, NivelAcademico.AVANZADO, 8.0)],
        ),
        "BBBB800101AAA": PlatformResult(
            Fuente.SASA,
            EstadoConsulta.OK,
            [reg(Fuente.SASA, NivelAcademico.INICIAL, 7.0)],
        ),
        "CCCC900101CCC": PlatformResult(
            Fuente.SASA,
            EstadoConsulta.OK,
            [reg(Fuente.SASA, NivelAcademico.INICIAL, 4.0)],
        ),
        "DDDD850101DDD": PlatformResult(
            Fuente.SASA,
            EstadoConsulta.OK,
            [reg(Fuente.SASA, NivelAcademico.INTERMEDIO, None)],
        ),
    }
    siga = {
        "BBBB800101AAA": PlatformResult(
            Fuente.SIGA,
            EstadoConsulta.OK,
            [reg(Fuente.SIGA, NivelAcademico.PRIMARIA, 7.0)],
        ),
    }

    cliente_sasa = ClienteFalso(Fuente.SASA, sasa)
    cliente_siga = ClienteFalso(Fuente.SIGA, siga)
    cliente_siged = ClienteFalso(Fuente.SIGED, {}, configurado=False)

    settings = Settings(
        data_dir=tmp_path / "data",
        input_dir=tmp_path / "data" / "input",
        output_dir=tmp_path / "data" / "output",
        state_dir=tmp_path / "data" / "state",
        logs_dir=tmp_path / "data" / "logs",
        consent_file=tmp_path / "data" / "state" / "consentimiento.json",
        privacy_salt_file=tmp_path / "data" / "state" / ".privacy_salt",
    )
    settings.ensure_dirs()
    privacy = PrivacyManager(settings)
    pipeline = Pipeline(
        settings,
        privacy,
        SesionesFalsas(),
        {
            Fuente.SASA: cliente_sasa,
            Fuente.SIGA: cliente_siga,
            Fuente.SIGED: cliente_siged,
        },
    )

    resultado = pipeline.run(ruta, accept_privacy=True, operator="test", resume=True)

    assert resultado.categorias == {
        "BASURA": 1,
        "CONTINUIDAD": 1,
        "NO_ACREDITADO": 1,
        "NO_PRESENTADO": 1,
    }
    assert resultado.procesados == 4
    assert resultado.reanudados == 0
    assert cliente_sasa.llamadas == 4
    assert cliente_siga.llamadas == 4
    assert resultado.output_dir is not None
    assert (resultado.output_dir / "RESUMEN.xlsx").exists()
    assert (resultado.output_dir / "BASURA.xlsx").exists()

    # Segunda corrida: no debe volver a consultar ni perder resultados.
    resultado2 = pipeline.run(ruta, accept_privacy=True, operator="test", resume=True)
    assert resultado2.procesados == 4
    assert resultado2.reanudados == 4
    assert cliente_sasa.llamadas == 4
    assert cliente_siga.llamadas == 4
    assert resultado2.categorias == resultado.categorias


def test_pipeline_usa_curp_para_siged_y_omite_filas_sin_curp(tmp_path):
    curp = "AAGJ770319HGTXXX00"
    ruta = tmp_path / "entrada.xlsx"
    pd.DataFrame(
        {
            "RFE": ["AAGJ7703195R4", "BBBB800101AAA"],
            "CURP": [curp, None],
        }
    ).to_excel(ruta, index=False)

    sasa = {
        rfe: PlatformResult(
            Fuente.SASA,
            EstadoConsulta.OK,
            [reg(Fuente.SASA, NivelAcademico.INICIAL, 7.0)],
        )
        for rfe in ("AAGJ7703195R4", "BBBB800101AAA")
    }
    cliente_sasa = ClienteFalso(Fuente.SASA, sasa)
    cliente_siga = ClienteFalso(Fuente.SIGA, {})
    cliente_siged = ClienteFalso(Fuente.SIGED, {}, configurado=True)

    data = tmp_path / "data"
    settings = Settings(
        data_dir=data,
        input_dir=data / "input",
        output_dir=data / "output",
        state_dir=data / "state",
        logs_dir=data / "logs",
        consent_file=data / "state" / "consentimiento.json",
        privacy_salt_file=data / "state" / ".privacy_salt",
        siged_url="http://siged.local",
        siged_mode="on_demand",
    )
    settings.ensure_dirs()
    pipeline = Pipeline(
        settings,
        PrivacyManager(settings),
        SesionesFalsas(),
        {
            Fuente.SASA: cliente_sasa,
            Fuente.SIGA: cliente_siga,
            Fuente.SIGED: cliente_siged,
        },
    )

    resultado = pipeline.run(ruta, accept_privacy=True, operator="test")

    assert resultado.categorias == {"REVISION_MANUAL": 2}
    assert cliente_siged.claves == [curp]


def test_pipeline_filtra_por_zonas_y_separa_checkpoint(tmp_path):
    ruta = tmp_path / "entrada.xlsx"
    pd.DataFrame(
        {
            "CVE ZONA": [1101, 1102, 1111],
            "RFE": ["AAGJ7703195R4", "BBBB800101AAA", "CCCC900101CCC"],
        }
    ).to_excel(ruta, index=False)

    cliente_sasa = ClienteFalso(Fuente.SASA, {})
    cliente_siga = ClienteFalso(Fuente.SIGA, {})
    data = tmp_path / "data"
    settings = Settings(
        data_dir=data,
        input_dir=data / "input",
        output_dir=data / "output",
        state_dir=data / "state",
        logs_dir=data / "logs",
        consent_file=data / "state" / "consentimiento.json",
        privacy_salt_file=data / "state" / ".privacy_salt",
        zonas=("1101", "1111"),
    )
    settings.ensure_dirs()
    pipeline = Pipeline(
        settings,
        PrivacyManager(settings),
        SesionesFalsas(),
        {
            Fuente.SASA: cliente_sasa,
            Fuente.SIGA: cliente_siga,
            Fuente.SIGED: ClienteFalso(Fuente.SIGED, {}, configurado=False),
        },
    )

    resultado = pipeline.run(ruta, accept_privacy=True, operator="test")

    assert set(cliente_sasa.claves) == {"AAGJ7703195R4", "CCCC900101CCC"}
    assert resultado.procesados == 2
    assert resultado.categorias == {"SIN_DATOS": 2}
    assert "zonas-1101-1111" in resultado.run_id


def test_pipeline_reintenta_error_transitorio_en_corrida_posterior(tmp_path):
    ruta = tmp_path / "entrada.xlsx"
    pd.DataFrame({"RFE": ["AAGJ7703195R4"]}).to_excel(ruta, index=False)

    cliente_sasa = ClienteFalso(
        Fuente.SASA,
        {
            "AAGJ7703195R4": PlatformResult(
                Fuente.SASA,
                EstadoConsulta.ERROR,
                error="fallo temporal",
            )
        },
    )
    cliente_siga = ClienteFalso(Fuente.SIGA, {})
    data = tmp_path / "data"
    settings = Settings(
        data_dir=data,
        input_dir=data / "input",
        output_dir=data / "output",
        state_dir=data / "state",
        logs_dir=data / "logs",
        consent_file=data / "state" / "consentimiento.json",
        privacy_salt_file=data / "state" / ".privacy_salt",
    )
    settings.ensure_dirs()
    pipeline = Pipeline(
        settings,
        PrivacyManager(settings),
        SesionesFalsas(),
        {
            Fuente.SASA: cliente_sasa,
            Fuente.SIGA: cliente_siga,
            Fuente.SIGED: ClienteFalso(Fuente.SIGED, {}, configurado=False),
        },
    )

    primero = pipeline.run(ruta, accept_privacy=True, operator="test")
    assert primero.categorias == {"ERROR": 1}
    # El reintento de fin de corrida vuelve a consultar el ERROR.
    assert cliente_sasa.llamadas == 2

    cliente_sasa.resultados_por_rfe = {
        "AAGJ7703195R4": PlatformResult(
            Fuente.SASA,
            EstadoConsulta.OK,
            [reg(Fuente.SASA, NivelAcademico.INICIAL, 7.0)],
        )
    }
    segundo = pipeline.run(ruta, accept_privacy=True, operator="test")

    assert segundo.categorias == {"REVISION_MANUAL": 1}
    assert cliente_sasa.llamadas == 3
