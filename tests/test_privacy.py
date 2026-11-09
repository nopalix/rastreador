"""Pruebas de consentimiento, máscaras y retención."""

from __future__ import annotations

import os
import time
from dataclasses import replace

import pytest

from rfe_rastreador.config import Settings
from rfe_rastreador.errors import ConsentRequiredError
from rfe_rastreador.privacy import ConsentManager, PrivacyManager


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        logs_dir=tmp_path / "logs",
        consent_file=tmp_path / "state" / "consentimiento.json",
        privacy_salt_file=tmp_path / "state" / ".privacy_salt",
    )


def test_consentimiento_requerido(settings):
    manager = ConsentManager(settings.consent_file, "1.0")
    with pytest.raises(ConsentRequiredError):
        manager.require()
    assert not manager.is_valid()


def test_consentimiento_otorgado_y_revocado(settings):
    manager = ConsentManager(settings.consent_file, "1.0")
    manager.grant("tester")
    assert manager.is_valid()
    assert manager.status().operator == "tester"

    manager.revoke("tester")
    assert not manager.is_valid()


def test_version_de_consentimiento_invalida(settings):
    ConsentManager(settings.consent_file, "1.0").grant("tester")
    assert not ConsentManager(settings.consent_file, "2.0").is_valid()


def test_mascaras():
    assert PrivacyManager.mask_rfe("AAGJ7703195R4") == "AAG********R4"
    assert PrivacyManager.mask_telefono("4771234567") == "******4567"
    assert PrivacyManager.mask_nombre("Ana Pérez") == "A.** P.****"


def test_hash_estable_y_sin_rfe_en_claro(settings):
    privacy = PrivacyManager(settings)
    clave1 = privacy.rfe_key("AAGJ7703195R4")
    clave2 = privacy.rfe_key("AAGJ7703195R4")
    assert clave1 == clave2
    assert "AAGJ" not in clave1


def test_sin_hash_cuando_se_desactiva(settings):
    settings = replace(settings, checkpoint_hash_identifiers=False)
    assert PrivacyManager(settings).rfe_key("AAGJ7703195R4") == "AAGJ7703195R4"


def test_purga_respeta_consentimiento_y_archivos_recientes(settings):
    settings.ensure_dirs()
    viejo = settings.output_dir / "viejo"
    viejo.mkdir()
    reciente = settings.output_dir / "reciente"
    reciente.mkdir()
    consentimiento = settings.state_dir / "consentimiento.json"
    consentimiento.write_text("{}", encoding="utf-8")

    antiguo = time.time() - 10 * 24 * 3600
    os.utime(viejo, (antiguo, antiguo))

    eliminados = PrivacyManager(settings).purge_old(days=5)
    assert viejo in eliminados
    assert not viejo.exists()
    assert reciente.exists()
    assert consentimiento.exists()
