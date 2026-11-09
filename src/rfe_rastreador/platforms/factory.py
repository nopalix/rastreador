"""Fábrica de clientes de plataforma."""

from __future__ import annotations

import logging

from ..config import Settings
from ..credentials import CredentialsProvider
from ..http_session import SessionManager
from ..models import Fuente
from .base import PlatformClient
from .sasa import SasaClient
from .siga import SigaClient
from .siged import SigedClient


def crear_clientes(
    settings: Settings,
    sessions: SessionManager,
    credentials: CredentialsProvider | None = None,
    logger: logging.Logger | None = None,
) -> dict[Fuente, PlatformClient]:
    return {
        Fuente.SASA: SasaClient(
            settings,
            sessions.get("sasa"),
            credentials,
            logger,
        ),
        Fuente.SIGA: SigaClient(
            settings,
            sessions.get("siga"),
            credentials,
            logger,
        ),
        Fuente.SIGED: SigedClient(
            settings,
            sessions.get("siged"),
            credentials,
            logger,
        ),
    }


__all__ = ["crear_clientes"]
