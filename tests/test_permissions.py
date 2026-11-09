"""Permisos configurables de carpetas y archivos sensibles."""

from __future__ import annotations

import stat

import pytest

from rfe_rastreador.checkpoint import CheckpointManager
from rfe_rastreador.config import Settings
from rfe_rastreador.excel_io import escribir_salidas, leer_excel
from rfe_rastreador.models import Categoria, Evaluation
from rfe_rastreador.privacy import PrivacyManager

pd = pytest.importorskip("pandas")
pytest.importorskip("openpyxl")


def modo(ruta) -> int:
    return stat.S_IMODE(ruta.stat().st_mode)


def _settings(tmp_path, dir_mode=0o2770, file_mode=0o660) -> Settings:
    data = tmp_path / "data"
    return Settings(
        data_dir=data,
        input_dir=data / "input",
        output_dir=data / "output",
        state_dir=data / "state",
        logs_dir=data / "logs",
        dir_mode=dir_mode,
        file_mode=file_mode,
    )


def test_modos_se_leen_desde_el_entorno(monkeypatch):
    monkeypatch.setenv("RFE_DIR_MODE", "2770")
    monkeypatch.setenv("RFE_FILE_MODE", "0o660")

    settings = Settings.from_env()

    assert settings.dir_mode == 0o2770
    assert settings.file_mode == 0o660


def test_modos_invalidos_conservan_el_default(monkeypatch):
    monkeypatch.setenv("RFE_DIR_MODE", "no-es-octal")
    monkeypatch.setenv("RFE_FILE_MODE", "9999")

    settings = Settings.from_env()

    assert settings.dir_mode == 0o700
    assert settings.file_mode == 0o600


def test_ensure_dirs_aplica_dir_mode_a_las_nuevas(tmp_path):
    settings = _settings(tmp_path)
    settings.ensure_dirs()

    assert modo(settings.data_dir) == 0o2770
    assert modo(settings.output_dir) == 0o2770
    assert modo(settings.state_dir) == 0o2770


def test_checkpoint_usa_modos_configurables(tmp_path):
    entrada = tmp_path / "entrada.xlsx"
    pd.DataFrame({"RFE": ["AAGJ7703195R4"]}).to_excel(entrada, index=False)

    checkpoint = CheckpointManager.for_input(
        entrada,
        tmp_path / "state",
        dir_mode=0o2770,
        file_mode=0o660,
    )
    with checkpoint:
        checkpoint.load()
        checkpoint.mark(
            Evaluation(
                rfe="AAGJ7703195R4",
                categoria=Categoria.NO_ACREDITADO,
                regla_id="R3_NO_ACREDITADO",
                motivo="prueba",
            )
        )

    assert modo(checkpoint.run_dir) == 0o2770
    assert modo(checkpoint.state_path) == 0o660
    assert modo(checkpoint.results_path) == 0o660


def test_salidas_usan_modos_configurables(tmp_path):
    entrada = tmp_path / "entrada.xlsx"
    pd.DataFrame({"RFE": ["AAGJ7703195R4"]}).to_excel(entrada, index=False)
    lectura = leer_excel(entrada)

    run_dir = escribir_salidas(
        [],
        lectura,
        tmp_path / "output",
        "run-prueba",
        dir_mode=0o2770,
        file_mode=0o660,
    )

    assert modo(run_dir) == 0o2770
    assert modo(run_dir / "TODOS.xlsx") == 0o660
    assert modo(run_dir / "RESUMEN.xlsx") == 0o660


def test_consentimiento_y_salt_usan_file_mode(tmp_path):
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    privacy = PrivacyManager(settings)

    privacy.ensure_consent(aceptar=True, operator="prueba")
    privacy.rfe_key("AAGJ7703195R4")

    assert modo(settings.consent_file) == 0o660
    assert modo(settings.privacy_salt_file) == 0o660
