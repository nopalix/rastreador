"""Clientes de plataformas académicas."""

from .base import PlatformClient
from .factory import crear_clientes
from .parsing import extraer_registros, extraer_pares, extraer_tablas
from .sasa import SasaClient
from .siga import SigaClient
from .siged import SigedClient

__all__ = [
    "PlatformClient",
    "SasaClient",
    "SigaClient",
    "SigedClient",
    "crear_clientes",
    "extraer_registros",
    "extraer_pares",
    "extraer_tablas",
]
