"""Contrato común de los clientes de plataforma."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import requests

from ..config import Settings
from ..credentials import CredentialsProvider, PlatformCredentials
from ..errors import AuthenticationError, PlatformError
from ..http_session import WebSession
from ..models import EstadoConsulta, Fuente, PlatformResult
from .parsing import (
    extraer_registros,
    parece_login,
    parece_no_encontrado,
)


def _mask(rfe: str) -> str:
    texto = str(rfe or "")
    if len(texto) <= 5:
        return "*" * len(texto)
    return f"{texto[:3]}{'*' * (len(texto) - 5)}{texto[-2:]}"


class PlatformClient(ABC):
    """Base para SASA, SIGA y SIGED.

    ``consultar()`` nunca lanza excepciones de red: devuelve un
    ``PlatformResult`` con estado ``ERROR`` para que el pipeline pueda continuar
    con el resto de los RFE.
    """

    fuente: Fuente

    def __init__(
        self,
        settings: Settings,
        session: WebSession,
        credentials: CredentialsProvider | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.session = session
        self.credentials = credentials
        self.log = logger or logging.getLogger(
            f"rfe_rastreador.platforms.{self.fuente.value.lower()}"
        )

    @property
    @abstractmethod
    def configurado(self) -> bool:
        """Indica si la plataforma tiene URL (y credenciales si aplican)."""

    @abstractmethod
    def _obtener(self, rfe: str) -> str | None:
        """Hace la petición y devuelve el HTML, o ``None`` si no hay registro."""

    def _credenciales(self) -> PlatformCredentials:
        if self.credentials is None:
            return PlatformCredentials()
        return self.credentials.get(self.fuente)

    def _kwargs_auth(self) -> dict[str, Any]:
        cred = self._credenciales()
        if cred.token:
            return {"headers": {"Authorization": f"Bearer {cred.token}"}}
        if cred.usa_basic_auth:
            return {"auth": (cred.usuario, cred.password)}
        return {}

    @staticmethod
    def _validar_respuesta(respuesta: requests.Response) -> bool:
        """True si la respuesta puede contener datos; False si es 404."""

        if respuesta.status_code in (401, 403):
            raise AuthenticationError(
                f"La plataforma respondió {respuesta.status_code}; "
                "revisa credenciales o permisos."
            )
        if respuesta.status_code == 404:
            return False
        if respuesta.status_code >= 400:
            raise PlatformError(
                f"La plataforma respondió HTTP {respuesta.status_code}."
            )
        return True

    def _interpretar(self, contenido: str) -> PlatformResult:
        """Convierte la respuesta por defecto (HTML) en un resultado."""

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

        return PlatformResult(
            fuente=self.fuente,
            estado=EstadoConsulta.OK,
            registros=registros,
        )

    def consultar(self, rfe: str) -> PlatformResult:
        if not self.configurado:
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.NO_CONFIGURADO,
                error=(
                    f"{self.fuente.value} no tiene URL configurada "
                    f"({self.fuente.value}_URL)."
                ),
            )

        try:
            contenido = self._obtener(rfe)
            if contenido is None:
                return PlatformResult(
                    fuente=self.fuente,
                    estado=EstadoConsulta.NO_ENCONTRADO,
                )

            return self._interpretar(contenido)

        except AuthenticationError as exc:
            self.log.warning(
                "%s: autenticación fallida para %s: %s",
                self.fuente.value,
                _mask(rfe),
                exc,
            )
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.ERROR,
                error=f"AuthenticationError: {exc}",
            )
        except PlatformError as exc:
            self.log.warning(
                "%s: error de plataforma para %s: %s",
                self.fuente.value,
                _mask(rfe),
                exc,
            )
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.ERROR,
                error=f"PlatformError: {exc}",
            )
        except requests.RequestException as exc:
            self.log.warning(
                "%s: error de red para %s: %s",
                self.fuente.value,
                _mask(rfe),
                type(exc).__name__,
            )
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.ERROR,
                error=f"RequestException: {type(exc).__name__}",
            )
        except Exception as exc:  # defensa: una fila no debe tumbar el lote
            self.log.exception(
                "%s: error inesperado para %s", self.fuente.value, _mask(rfe)
            )
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.ERROR,
                error=f"{type(exc).__name__}: {exc}",
            )
