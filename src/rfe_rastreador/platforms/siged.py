"""Cliente SIGED (antecedentes de escolarizados).

SIGED se consulta solo cuando SASA/SIGA no alcanzan para clasificar (modo
``on_demand``), para no aumentar innecesariamente el volumen de peticiones.

La consulta pública real es un API JSON que trabaja con CURP, no con RFE::

    GET https://api.siged.sep.gob.mx/CoreServices/servicios
        /alumno/schoolCyclesByStudent/curp=<CURP>

El adaptador conserva además el modo ``html`` para instancias que publiquen una
página con etiquetas o tablas, reutilizando el parser genérico.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

from ..config import Settings
from ..credentials import CredentialsProvider
from ..http_session import WebSession
from ..models import (
    AcademicRecord,
    EstadoConsulta,
    Fuente,
    PlatformResult,
)
from ..normalization import normalizar_nivel, parsear_calificacion
from .base import PlatformClient


def _texto(valor) -> str:
    if valor is None:
        return ""
    return str(valor).strip()


class SigedClient(PlatformClient):
    fuente = Fuente.SIGED

    def __init__(
        self,
        settings: Settings,
        session: WebSession,
        credentials: CredentialsProvider | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(settings, session, credentials, logger)

    @property
    def configurado(self) -> bool:
        return bool(self.settings.siged_url)

    def _obtener(self, rfe: str) -> str | None:
        if self.settings.siged_format == "json":
            url = (
                f"{self.settings.siged_url.rstrip('/')}"
                f"/alumno/schoolCyclesByStudent/curp={quote(rfe, safe='')}"
            )
            kwargs = self._kwargs_auth()
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("Accept", "application/json")
            respuesta = self.session.get(
                url,
                headers=headers,
                verify=self.settings.siged_verify_tls,
                **kwargs,
            )
        else:
            parametros = {self.settings.siged_id_param: rfe}
            if self.settings.siged_method == "POST":
                respuesta = self.session.post(
                    self.settings.siged_url,
                    data=parametros,
                    **self._kwargs_auth(),
                )
            else:
                respuesta = self.session.get(
                    self.settings.siged_url,
                    params=parametros,
                    **self._kwargs_auth(),
                )

        if not self._validar_respuesta(respuesta):
            return None
        return respuesta.text

    def _interpretar(self, contenido: str) -> PlatformResult:
        """Parsea el JSON de SIGED; si no es JSON, cae al parser HTML."""

        try:
            datos = json.loads(contenido)
        except (ValueError, TypeError):
            return super()._interpretar(contenido)

        if not isinstance(datos, dict):
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.NO_ENCONTRADO,
            )

        estudiantes = datos.get("students") or []
        if not isinstance(estudiantes, list):
            estudiantes = []

        registros: list[AcademicRecord] = []
        vistos: set[tuple[str, str, str]] = set()

        for estudiante in estudiantes:
            if not isinstance(estudiante, dict):
                continue
            ciclos = estudiante.get("schoolCyclesDTO") or []
            if not isinstance(ciclos, list):
                continue

            for ciclo in ciclos:
                if not isinstance(ciclo, dict):
                    continue

                nivel_raw = _texto(ciclo.get("level"))
                promedio = ciclo.get("avgAcreditation")
                calificacion_raw = _texto(promedio)
                ciclo_escolar = _texto(ciclo.get("schoolCycle"))

                clave = (nivel_raw, calificacion_raw, ciclo_escolar)
                if clave in vistos:
                    continue
                vistos.add(clave)

                detalles = {
                    nombre: valor
                    for nombre, valor in (
                        ("ciclo_escolar", ciclo_escolar),
                        ("grado", _texto(ciclo.get("grade"))),
                        ("grupo", _texto(ciclo.get("group"))),
                        ("turno", _texto(ciclo.get("shift"))),
                        ("id_ciclo", _texto(ciclo.get("idStudentCycle"))),
                    )
                    if valor
                }

                registros.append(
                    AcademicRecord(
                        fuente=self.fuente,
                        nivel=normalizar_nivel(nivel_raw),
                        calificacion=parsear_calificacion(promedio),
                        nivel_raw=nivel_raw,
                        calificacion_raw=calificacion_raw,
                        detalles=detalles,
                    )
                )

        if not registros:
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.NO_ENCONTRADO,
            )

        return PlatformResult(
            fuente=self.fuente,
            estado=EstadoConsulta.OK,
            registros=registros,
        )
