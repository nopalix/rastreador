"""Cliente SASA / QuickSASA (modelo MEV)."""

from __future__ import annotations

import logging

from ..config import Settings
from ..credentials import CredentialsProvider
from ..http_session import WebSession
from ..models import Fuente
from .base import PlatformClient


class SasaClient(PlatformClient):
    """Consulta el endpoint público de QuickSASA.

    Se reutiliza el endpoint y los parámetros del proyecto QUICKSASA existente:
    ``POST BuscarRFE`` con ``edo`` y ``find`` (RFE). La respuesta se parsea con
    el módulo genérico porque incluye ``Situación``, ``Etapa`` y ``Promedio``.
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
