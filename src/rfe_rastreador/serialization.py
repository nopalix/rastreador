"""Serialización JSON de resultados para checkpoints y auditoría."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import (
    AcademicRecord,
    Categoria,
    EstadoConsulta,
    Evaluation,
    Fuente,
    NivelAcademico,
    PlatformResult,
)


def academic_record_to_dict(registro: AcademicRecord) -> dict[str, Any]:
    return registro.como_dict()


def academic_record_from_dict(datos: dict[str, Any]) -> AcademicRecord:
    return AcademicRecord(
        fuente=Fuente(datos["fuente"]),
        nivel=NivelAcademico(datos.get("nivel", "DESCONOCIDO")),
        calificacion=datos.get("calificacion"),
        nivel_raw=str(datos.get("nivel_raw", "")),
        calificacion_raw=str(datos.get("calificacion_raw", "")),
        detalles=dict(datos.get("detalles") or {}),
    )


def platform_result_to_dict(resultado: PlatformResult) -> dict[str, Any]:
    return resultado.como_dict()


def platform_result_from_dict(datos: dict[str, Any]) -> PlatformResult:
    consultado = datos.get("consultado_en")
    try:
        consultado_en = (
            datetime.fromisoformat(consultado)
            if consultado
            else datetime.now(timezone.utc)
        )
    except ValueError:
        consultado_en = datetime.now(timezone.utc)

    return PlatformResult(
        fuente=Fuente(datos["fuente"]),
        estado=EstadoConsulta(datos["estado"]),
        registros=[
            academic_record_from_dict(r) for r in datos.get("registros", [])
        ],
        error=datos.get("error"),
        consultado_en=consultado_en,
    )


def evaluation_to_dict(
    evaluacion: Evaluation, incluir_rfe: bool = True
) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "categoria": evaluacion.categoria.value,
        "regla_id": evaluacion.regla_id,
        "motivo": evaluacion.motivo,
        "evidencia": evaluacion.evidencia,
        "resultados": {
            fuente.value: platform_result_to_dict(resultado)
            for fuente, resultado in evaluacion.resultados.items()
        },
    }
    if incluir_rfe:
        datos["rfe"] = evaluacion.rfe
    return datos


def evaluation_from_dict(datos: dict[str, Any], rfe: str = "") -> Evaluation:
    return Evaluation(
        rfe=datos.get("rfe", rfe),
        categoria=Categoria(datos["categoria"]),
        regla_id=datos.get("regla_id", ""),
        motivo=datos.get("motivo", ""),
        evidencia=dict(datos.get("evidencia") or {}),
        resultados={
            Fuente(fuente): platform_result_from_dict(resultado)
            for fuente, resultado in (datos.get("resultados") or {}).items()
        },
    )
