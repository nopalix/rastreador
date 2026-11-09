"""Orquestador del pipeline: Excel -> plataformas -> reglas -> salidas."""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Callable

from .checkpoint import CheckpointManager, contar_categorias
from .config import Settings
from .credentials import CredentialsProvider
from .excel_io import escribir_salidas, leer_excel
from .http_session import SessionManager
from .models import (
    Categoria,
    EstadoConsulta,
    Evaluation,
    Fuente,
    InputRecord,
    PipelineResult,
    PlatformResult,
)
from .normalization import es_curp, extraer_curp
from .platforms.base import PlatformClient
from .platforms.factory import crear_clientes
from .privacy import PrivacyManager
from .rules import evaluar_registro


ProgressCallback = Callable[[int, int, str], None]


class Pipeline:
    """Ejecuta una corrida completa y reanudable."""

    def __init__(
        self,
        settings: Settings,
        privacy: PrivacyManager,
        sessions: SessionManager,
        clients: dict[Fuente, PlatformClient],
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.privacy = privacy
        self.sessions = sessions
        self.clients = clients
        self.log = logger or logging.getLogger("rfe_rastreador.pipeline")

    # ------------------------------------------------------------------
    # Procesamiento de una fila
    # ------------------------------------------------------------------
    def _debe_consultar_siged(self, evaluacion: Evaluation) -> bool:
        modo = self.settings.siged_mode
        if modo == "never":
            return False
        if modo == "always":
            return True
        return evaluacion.categoria in {
            Categoria.SIN_DATOS,
            Categoria.REVISION_MANUAL,
            Categoria.ERROR,
        }

    def _consultar(self, fuente: Fuente, rfe: str) -> PlatformResult:
        cliente = self.clients.get(fuente)
        if cliente is None:
            return PlatformResult(
                fuente=fuente,
                estado=EstadoConsulta.NO_CONFIGURADO,
                error=f"No hay cliente configurado para {fuente.value}.",
            )
        return cliente.consultar(rfe)

    def _procesar_registro(
        self, rfe: str, curp: str | None = None
    ) -> Evaluation:
        resultados: dict[Fuente, PlatformResult] = {}

        for fuente in (Fuente.SASA, Fuente.SIGA):
            resultados[fuente] = self._consultar(fuente, rfe)

        evaluacion = evaluar_registro(rfe, resultados)

        if self._debe_consultar_siged(evaluacion):
            cliente_siged = self.clients.get(Fuente.SIGED)
            clave_siged = curp or (rfe if es_curp(rfe) else "")
            if (
                cliente_siged is not None
                and cliente_siged.configurado
                and clave_siged
            ):
                resultados[Fuente.SIGED] = cliente_siged.consultar(clave_siged)
                evaluacion = evaluar_registro(rfe, resultados)

        return evaluacion

    def _pausa_entre_registros(self) -> None:
        minimo = max(0.0, self.settings.record_delay_min)
        maximo = max(minimo, self.settings.record_delay_max)
        if maximo > 0:
            time.sleep(random.uniform(minimo, maximo))

    def _reintentar_errores(
        self,
        checkpoint: CheckpointManager,
        registros: list[InputRecord],
    ) -> None:
        """Segundo intento único para errores transitorios de la corrida."""

        por_clave = {
            self.privacy.rfe_key(registro.rfe): registro
            for registro in registros
        }
        pendientes = [
            por_clave[clave]
            for clave, evaluacion in checkpoint.load_evaluations_con_clave()
            if evaluacion.categoria is Categoria.ERROR and clave in por_clave
        ]
        if not pendientes:
            return

        self.log.info(
            "Reintentando %s registros con ERROR transitorio...",
            len(pendientes),
        )
        for indice, registro in enumerate(pendientes, start=1):
            curp = extraer_curp(registro.datos, registro.rfe)
            evaluacion = self._procesar_registro(
                registro.rfe, curp=curp or None
            )
            checkpoint.mark(evaluacion)

            if indice == len(pendientes) or indice % 10 == 0:
                self.log.info(
                    "Reintento ERROR: %s/%s", indice, len(pendientes)
                )
            if indice < len(pendientes):
                self._pausa_entre_registros()

    # ------------------------------------------------------------------
    # Hidratación de checkpoints
    # ------------------------------------------------------------------
    def _mapear_evaluaciones(
        self,
        pares: list[tuple[str, Evaluation]],
        registros: list[InputRecord],
    ) -> list[Evaluation]:
        """Recupera el RFE real desde la fila de entrada usando su clave HMAC."""

        rfe_por_clave = {
            self.privacy.rfe_key(registro.rfe): registro.rfe
            for registro in registros
        }
        por_clave: dict[str, Evaluation] = {}
        for clave, evaluacion in pares:
            rfe = rfe_por_clave.get(clave, evaluacion.rfe)
            evaluacion.rfe = rfe
            por_clave[clave] = evaluacion

        ordenadas: list[Evaluation] = []
        for registro in registros:
            clave = self.privacy.rfe_key(registro.rfe)
            evaluacion = por_clave.get(clave)
            if evaluacion is not None:
                ordenadas.append(evaluacion)
        return ordenadas

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def run(
        self,
        input_path: str | Path,
        *,
        hoja: int | str = 0,
        columna_rfe: str | None = None,
        columna_zona: str | None = None,
        zonas: tuple[str, ...] | None = None,
        limit: int | None = None,
        resume: bool = True,
        accept_privacy: bool = False,
        operator: str = "",
        dry_run: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineResult:
        self.privacy.ensure_consent(
            aceptar=accept_privacy,
            operator=operator,
        )

        zonas_efectivas = tuple(
            sorted(
                str(zona).strip()
                for zona in (
                    zonas if zonas is not None else self.settings.zonas
                )
                if str(zona).strip()
            )
        )
        columna_zona_efectiva = columna_zona or self.settings.zone_column
        if zonas_efectivas:
            self.log.info(
                "Filtro de zonas activo: %s (columna '%s').",
                ", ".join(zonas_efectivas),
                columna_zona_efectiva,
            )

        lectura = leer_excel(
            input_path,
            hoja=hoja,
            columna_rfe=columna_rfe,
            longitudes=self.settings.valid_key_lengths,
            columna_zona=columna_zona_efectiva,
            zonas=zonas_efectivas,
        )
        total = len(lectura.registros)

        if dry_run:
            self.log.info(
                "Dry-run: %s registros válidos, %s inválidos, %s duplicados. "
                "No se consultarán plataformas.",
                total,
                len(lectura.invalidos),
                lectura.duplicados,
            )
            return PipelineResult(
                run_id="dry-run",
                output_dir=None,
                total=total,
                procesados=0,
                reanudados=0,
                invalidos=len(lectura.invalidos),
                duplicados=lectura.duplicados,
                categorias={},
            )

        checkpoint = CheckpointManager.for_input(
            input_path,
            self.settings.state_dir,
            total=total,
            identity_hasher=self.privacy.rfe_key,
            store_raw_identifiers=self.settings.checkpoint_store_raw_identifiers,
            dir_mode=self.settings.dir_mode,
            file_mode=self.settings.file_mode,
            run_suffix=(
                f"zonas-{'-'.join(zonas_efectivas)}"
                if zonas_efectivas
                else ""
            ),
        )

        with checkpoint:
            if not resume:
                checkpoint.reset()

            _, previos = checkpoint.load()
            # Un ERROR puede ser transitorio: no se considera completado y se
            # vuelve a intentar en esta corrida o en la siguiente.
            completados = {
                clave
                for clave, datos in previos.items()
                if str(datos.get("categoria", "")).upper()
                != Categoria.ERROR.value
            }
            pendientes = [
                registro
                for registro in lectura.registros
                if self.privacy.rfe_key(registro.rfe) not in completados
            ]
            if limit is not None:
                pendientes = pendientes[: max(0, limit)]

            self.log.info(
                "Checkpoint %s: %s/%s ya procesados; %s pendientes en esta corrida.",
                checkpoint.run_id,
                len(completados),
                total,
                len(pendientes),
            )

            total_pendientes = len(pendientes)
            for indice, registro in enumerate(pendientes, start=1):
                curp = extraer_curp(registro.datos, registro.rfe)
                evaluacion = self._procesar_registro(
                    registro.rfe, curp=curp or None
                )
                checkpoint.mark(evaluacion)

                if progress_callback:
                    progress_callback(indice, total_pendientes, registro.rfe)
                elif indice == total_pendientes or indice % 10 == 0:
                    self.log.info(
                        "Progreso: %s/%s (%.1f%%)",
                        indice,
                        total_pendientes,
                        100.0 * indice / max(1, total_pendientes),
                    )

                if indice < total_pendientes:
                    self._pausa_entre_registros()

            if self.settings.retry_errors and limit is None:
                self._reintentar_errores(checkpoint, lectura.registros)

            pares = checkpoint.load_evaluations_con_clave()
            evaluaciones = self._mapear_evaluaciones(
                pares, lectura.registros
            )
            run_dir = escribir_salidas(
                evaluaciones,
                lectura,
                self.settings.output_dir,
                checkpoint.run_id,
                self.privacy,
                dir_mode=self.settings.dir_mode,
                file_mode=self.settings.file_mode,
            )

        categorias = contar_categorias(evaluaciones)
        self.log.info(
            "Corrida %s terminada: %s clasificados, %s inválidos, %s duplicados.",
            checkpoint.run_id,
            len(evaluaciones),
            len(lectura.invalidos),
            lectura.duplicados,
        )
        for categoria, cantidad in sorted(categorias.items()):
            self.log.info("  %-18s %s", categoria, cantidad)

        return PipelineResult(
            run_id=checkpoint.run_id,
            output_dir=run_dir,
            total=total,
            procesados=len(evaluaciones),
            reanudados=max(0, len(evaluaciones) - total_pendientes),
            invalidos=len(lectura.invalidos),
            duplicados=lectura.duplicados,
            categorias=categorias,
        )

    def close(self) -> None:
        self.sessions.close_all()

    def __enter__(self) -> "Pipeline":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def crear_pipeline(settings: Settings) -> Pipeline:
    """Construye todas las dependencias con la configuración dada."""

    privacy = PrivacyManager(settings)
    sessions = SessionManager(settings)
    credentials = CredentialsProvider(settings)
    clients = crear_clientes(settings, sessions, credentials)
    return Pipeline(settings, privacy, sessions, clients)
