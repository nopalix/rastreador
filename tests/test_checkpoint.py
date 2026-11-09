"""Pruebas del sistema de checkpoints."""

from __future__ import annotations

import json

import pytest

from rfe_rastreador.checkpoint import CheckpointManager
from rfe_rastreador.errors import CheckpointMismatchError
from rfe_rastreador.models import Categoria, Evaluation


def evaluacion(rfe: str = "AAGJ7703195R4") -> Evaluation:
    return Evaluation(
        rfe=rfe,
        categoria=Categoria.NO_ACREDITADO,
        regla_id="R3_NO_ACREDITADO",
        motivo="prueba",
    )


def manager(tmp_path, store_raw=False):
    return CheckpointManager(
        state_dir=tmp_path,
        run_id="run-prueba",
        input_fingerprint="huella",
        input_path="entrada.xls",
        total=1,
        identity_hasher=lambda valor: f"h:{valor}",
        store_raw_identifiers=store_raw,
    )


def test_mark_y_reanudar(tmp_path):
    primero = manager(tmp_path)
    with primero:
        primero.load()
        primero.mark(evaluacion())

    segundo = manager(tmp_path)
    with segundo:
        _, resultados = segundo.load()
        assert "h:AAGJ7703195R4" in resultados
        assert segundo.completed_keys() == {"h:AAGJ7703195R4"}
        assert segundo.summary()["processed"] == 1
        evaluaciones = segundo.load_evaluations()
        assert len(evaluaciones) == 1
        assert evaluaciones[0].categoria is Categoria.NO_ACREDITADO
        # Por defecto no se guarda el RFE en claro.
        assert evaluaciones[0].rfe == ""


def test_checkpoint_con_rfe_en_claro(tmp_path):
    with manager(tmp_path, store_raw=True) as cp:
        cp.load()
        cp.mark(evaluacion())
        assert cp.load_evaluations()[0].rfe == "AAGJ7703195R4"


def test_fingerprint_distinto_falla(tmp_path):
    with manager(tmp_path) as cp:
        cp.load()
        cp.mark(evaluacion())

    otro = CheckpointManager(
        state_dir=tmp_path,
        run_id="run-prueba",
        input_fingerprint="otra-huella",
        input_path="entrada.xls",
        total=1,
        identity_hasher=lambda valor: f"h:{valor}",
    )
    with pytest.raises(CheckpointMismatchError):
        with otro:
            otro.load()


def test_run_suffix_separa_checkpoints(tmp_path):
    entrada = tmp_path / "entrada.xls"
    entrada.write_text("contenido", encoding="utf-8")

    general = CheckpointManager.for_input(entrada, tmp_path / "state")
    filtrado = CheckpointManager.for_input(
        entrada,
        tmp_path / "state",
        run_suffix="zonas-1101-1111",
    )

    assert general.run_id != filtrado.run_id
    assert "zonas-1101-1111" in filtrado.run_id


def test_linea_corrupta_se_ignora(tmp_path):
    with manager(tmp_path) as cp:
        cp.load()
        cp.mark(evaluacion())

    with open(cp.results_path, "a", encoding="utf-8") as archivo:
        archivo.write("{esto no es json}\n")

    with manager(tmp_path) as cp:
        cp.load()
        assert cp.completed_keys() == {"h:AAGJ7703195R4"}


def test_reset(tmp_path):
    with manager(tmp_path) as cp:
        cp.load()
        cp.mark(evaluacion())
        cp.reset()
        cp.load()
        assert cp.completed_keys() == set()
        assert cp.summary()["processed"] == 0
