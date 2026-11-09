"""Motor de reglas de negocio.

Prioridad estricta (definida en ``Contexto_proyecto.md``):

1. BASURA          -> calificación >= 6 y nivel Avanzado/Secundaria (SASA)
                      o Secundaria (SIGA)
2. CONTINUIDAD     -> SIGA Primaria aprobada + SASA Inicial/Intermedio aprobado
3. NO_ACREDITADO   -> 0 < calificación < 6 (se evalúa la calificación ANTES del nivel)
4. NO_PRESENTADO   -> nivel asignado y calificación vacía o cero

Las reglas son funciones puras: reciben resultados de plataformas y devuelven
una ``Evaluation``. No hacen red, no leen Excel y no escriben archivos.
"""

from __future__ import annotations

from typing import Iterable

from .models import (
    AcademicRecord,
    Categoria,
    EstadoConsulta,
    Evaluation,
    Fuente,
    NivelAcademico,
    PlatformResult,
)
from .normalization import es_aprobatoria, es_rango_no_acreditado


NOTA_APROBATORIA = 6.0
NIVELES_SASA_PRIMARIA = frozenset(
    {NivelAcademico.INICIAL, NivelAcademico.INTERMEDIO}
)
NIVELES_SASA_BASURA = frozenset(
    {NivelAcademico.AVANZADO, NivelAcademico.SECUNDARIA}
)


def _registros_de(
    resultados: dict[Fuente, PlatformResult], fuente: Fuente
) -> Iterable[AcademicRecord]:
    resultado = resultados.get(fuente)
    if not resultado or resultado.estado is not EstadoConsulta.OK:
        return ()
    return tuple(resultado.registros)


def _todos_los_registros(
    resultados: dict[Fuente, PlatformResult],
) -> Iterable[AcademicRecord]:
    for resultado in resultados.values():
        if resultado.estado is EstadoConsulta.OK:
            yield from resultado.registros


def _buscar_basura(
    resultados: dict[Fuente, PlatformResult],
) -> AcademicRecord | None:
    """Regla 1: concluidos con nota aprobatoria en el nivel terminal.

    En SASA cuentan tanto ``AVANZADO`` como ``SECUNDARIA``; en SIGA solo
    ``SECUNDARIA``.
    """

    for registro in _registros_de(resultados, Fuente.SASA):
        if (
            registro.nivel in NIVELES_SASA_BASURA
            and es_aprobatoria(registro.calificacion, NOTA_APROBATORIA)
        ):
            return registro

    for registro in _registros_de(resultados, Fuente.SIGA):
        if (
            registro.nivel is NivelAcademico.SECUNDARIA
            and es_aprobatoria(registro.calificacion, NOTA_APROBATORIA)
        ):
            return registro

    return None


def _buscar_continuidad(
    resultados: dict[Fuente, PlatformResult],
) -> tuple[AcademicRecord, AcademicRecord] | None:
    """Regla 2: equivalencia Primaria (SIGA) -> Inicial/Intermedio (SASA)."""

    siga_primaria = next(
        (
            r
            for r in _registros_de(resultados, Fuente.SIGA)
            if r.nivel is NivelAcademico.PRIMARIA
            and es_aprobatoria(r.calificacion, NOTA_APROBATORIA)
        ),
        None,
    )
    if siga_primaria is None:
        return None

    sasa_primaria = next(
        (
            r
            for r in _registros_de(resultados, Fuente.SASA)
            if r.nivel in NIVELES_SASA_PRIMARIA
            and es_aprobatoria(r.calificacion, NOTA_APROBATORIA)
        ),
        None,
    )
    if sasa_primaria is None:
        return None

    return siga_primaria, sasa_primaria


def _buscar_no_acreditado(
    resultados: dict[Fuente, PlatformResult],
) -> AcademicRecord | None:
    """Regla 3: el rango de calificación manda, aunque el nivel esté vacío.

    Se revisan todas las fuentes y no se exige nivel, precisamente para cubrir
    los registros de SIGA que llegan sin nivel asignado.
    """

    return next(
        (
            r
            for r in _todos_los_registros(resultados)
            if es_rango_no_acreditado(r.calificacion)
        ),
        None,
    )


def _buscar_no_presentado(
    resultados: dict[Fuente, PlatformResult],
) -> AcademicRecord | None:
    """Regla 4: hay nivel, pero la calificación está vacía o es cero."""

    for registro in _todos_los_registros(resultados):
        nivel_asignado = registro.nivel is not NivelAcademico.DESCONOCIDO
        sin_calificacion = registro.calificacion is None
        calificacion_cero = registro.calificacion == 0.0
        if nivel_asignado and (sin_calificacion or calificacion_cero):
            return registro
    return None


def _registro_evidencia(registro: AcademicRecord) -> dict:
    return {
        "fuente": registro.fuente.value,
        "nivel": registro.nivel.value,
        "nivel_raw": registro.nivel_raw,
        "calificacion": registro.calificacion,
        "calificacion_raw": registro.calificacion_raw,
    }


def _construir_evidencia(
    resultados: dict[Fuente, PlatformResult],
    registros: list[AcademicRecord] | None = None,
) -> dict:
    evidencia = {
        "plataformas": {
            fuente.value: {
                "estado": resultado.estado.value,
                "error": resultado.error,
                "registros": [
                    _registro_evidencia(r) for r in resultado.registros
                ],
            }
            for fuente, resultado in resultados.items()
        }
    }
    if registros:
        evidencia["registros_detonantes"] = [
            _registro_evidencia(r) for r in registros
        ]
    return evidencia


def _fallback(resultados: dict[Fuente, PlatformResult]) -> tuple[Categoria, str, str]:
    """Categoría para casos que no cubren las cuatro reglas."""

    if not resultados:
        return (
            Categoria.SIN_DATOS,
            "SIN_CONFIGURACION",
            "No hay plataformas configuradas para consultar.",
        )

    estados = [r.estado for r in resultados.values()]
    hay_datos = any(r.tiene_datos for r in resultados.values())

    if hay_datos:
        return (
            Categoria.REVISION_MANUAL,
            "REVISION_MANUAL",
            "Hay datos académicos, pero no cumplen ninguna regla automática.",
        )

    if any(e is EstadoConsulta.ERROR for e in estados):
        return (
            Categoria.ERROR,
            "ERROR_CONSULTA",
            "Todas las consultas con datos fallaron o ninguna plataforma "
            "devolvió información utilizable.",
        )

    if all(e is EstadoConsulta.NO_CONFIGURADO for e in estados):
        return (
            Categoria.SIN_DATOS,
            "SIN_CONFIGURACION",
            "Las plataformas requeridas no están configuradas (URL vacía).",
        )

    return (
        Categoria.SIN_DATOS,
        "SIN_DATOS",
        "No se encontraron registros académicos en las plataformas.",
    )


def evaluar_registro(
    rfe: str,
    resultados: dict[Fuente, PlatformResult],
) -> Evaluation:
    """Aplica las reglas en orden estricto y devuelve la clasificación."""

    # Regla 1: Basura (excluir)
    basura = _buscar_basura(resultados)
    if basura is not None:
        return Evaluation(
            rfe=rfe,
            categoria=Categoria.BASURA,
            regla_id="R1_BASURA",
            motivo=(
                f"Nivel terminal aprobado: {basura.nivel.value} con "
                f"calificación {basura.calificacion}."
            ),
            evidencia=_construir_evidencia(resultados, [basura]),
            resultados=resultados,
        )

    # Regla 2: Continuidad
    continuidad = _buscar_continuidad(resultados)
    if continuidad is not None:
        siga, sasa = continuidad
        return Evaluation(
            rfe=rfe,
            categoria=Categoria.CONTINUIDAD,
            regla_id="R2_CONTINUIDAD",
            motivo=(
                f"Primaria aprobada en SIGA ({siga.calificacion}) y "
                f"{sasa.nivel.value} aprobado en SASA ({sasa.calificacion})."
            ),
            evidencia=_construir_evidencia(resultados, [siga, sasa]),
            resultados=resultados,
        )

    # Regla 3: No acreditado (primero calificación, luego nivel)
    no_acreditado = _buscar_no_acreditado(resultados)
    if no_acreditado is not None:
        return Evaluation(
            rfe=rfe,
            categoria=Categoria.NO_ACREDITADO,
            regla_id="R3_NO_ACREDITADO",
            motivo=(
                "Calificación en rango 0 < x < 6: "
                f"{no_acreditado.calificacion} "
                f"(nivel {no_acreditado.nivel.value})."
            ),
            evidencia=_construir_evidencia(resultados, [no_acreditado]),
            resultados=resultados,
        )

    # Regla 4: No presentado (nivel asignado, calificación vacía/cero)
    no_presentado = _buscar_no_presentado(resultados)
    if no_presentado is not None:
        return Evaluation(
            rfe=rfe,
            categoria=Categoria.NO_PRESENTADO,
            regla_id="R4_NO_PRESENTADO",
            motivo=(
                f"Nivel {no_presentado.nivel.value} asignado sin "
                "calificación válida (vacía o cero)."
            ),
            evidencia=_construir_evidencia(resultados, [no_presentado]),
            resultados=resultados,
        )

    categoria, regla_id, motivo = _fallback(resultados)
    return Evaluation(
        rfe=rfe,
        categoria=categoria,
        regla_id=regla_id,
        motivo=motivo,
        evidencia=_construir_evidencia(resultados),
        resultados=resultados,
    )
