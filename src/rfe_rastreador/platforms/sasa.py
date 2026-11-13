"""Cliente SASA / QuickSASA (modelo MEV)."""

from __future__ import annotations

import logging

from ..config import Settings
from ..credentials import CredentialsProvider
from ..errors import AuthenticationError
from ..http_session import WebSession
from ..models import EstadoConsulta, Fuente, PlatformResult
from ..platforms.parsing import (
    extraer_contacto,
    extraer_registros,
    normalizar_telefono_mexico,
    parece_login,
    parece_no_encontrado,
)
from .base import PlatformClient


class SasaClient(PlatformClient):
    """Consulta el endpoint público de QuickSASA.

    Se reutiliza el endpoint y los parámetros del proyecto QUICKSASA existente:
    ``POST BuscarRFE`` con ``edo`` y ``find`` (RFE). La respuesta se parsea con
    el módulo genérico porque incluye ``Situación``, ``Etapa`` y ``Promedio``.
    Además, se extraen datos de contacto (teléfono, dirección, nombre) cuando
    estén disponibles en el HTML.
    """

    fuente = Fuente.SASA

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
        return bool(self.settings.sasa_url)

    def _obtener(self, rfe: str) -> str | None:
        datos = {"edo": self.settings.sasa_estado, "find": rfe}
        if self.settings.sasa_method == "GET":
            respuesta = self.session.get(
                self.settings.sasa_url, params=datos, **self._kwargs_auth()
            )
        else:
            respuesta = self.session.post(
                self.settings.sasa_url, data=datos, **self._kwargs_auth()
            )

        if not self._validar_respuesta(respuesta):
            return None
        return respuesta.text

    def _interpretar(self, contenido: str) -> PlatformResult:
        """Convierte la respuesta HTML en un resultado con datos de contacto."""

        if parece_login(contenido):
            raise AuthenticationError(
                "La plataforma devolvió un formulario de inicio de sesión."
            )

        if parece_no_encontrado(contenido):
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.NO_ENCONTRADO,
            )

        registros = extraer_registros(contenido, self.fuente)
        if not registros:
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.NO_ENCONTRADO,
            )

        # Extraer datos de contacto del HTML
        contacto = extraer_contacto(contenido)

        # Enriquecer los registros con datos de contacto
        for registro in registros:
            if contacto.get("telefono") and not registro.telefono:
                registro.telefono = normalizar_telefono_mexico(
                    contacto["telefono"]
                )
            if contacto.get("direccion") and not registro.direccion:
                registro.direccion = contacto["direccion"]
            if contacto.get("nombre") and not registro.nombre:
                registro.nombre = contacto["nombre"]

        return PlatformResult(
            fuente=self.fuente,
            estado=EstadoConsulta.OK,
            registros=registros,
        )
