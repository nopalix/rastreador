"""Configuración de logging con rotación y filtro básico de secretos."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import Settings


_PATRONES_SECRETOS = (
    re.compile(r"(?i)(password|passwd|contrasena|contraseña|token|secret)\s*[=:]\s*\S+"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
)


class RedactionFilter(logging.Filter):
    """Evita que un mensaje accidental incluya secretos en claro."""

    def filter(self, record: logging.LogRecord) -> bool:
        mensaje = record.getMessage()
        for patron in _PATRONES_SECRETOS:
            mensaje = patron.sub(lambda m: f"{m.group(1)}=***", mensaje)
        record.msg = mensaje
        record.args = ()
        return True


def setup_logging(
    settings: Settings,
    level: str | None = None,
) -> logging.Logger:
    """Configura consola + archivo rotativo y devuelve el logger raíz."""

    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("rfe_rastreador")
    logger.setLevel((level or settings.log_level).upper())
    logger.propagate = False

    # Evita handlers duplicados si se llama más de una vez en el mismo proceso.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )

    consola = logging.StreamHandler()
    consola.setFormatter(formato)
    consola.addFilter(RedactionFilter())
    logger.addHandler(consola)

    archivo = RotatingFileHandler(
        Path(settings.logs_dir) / "rfe_rastreador.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    archivo.setFormatter(formato)
    archivo.addFilter(RedactionFilter())
    logger.addHandler(archivo)

    return logger


def get_logger(nombre: str) -> logging.Logger:
    return logging.getLogger(f"rfe_rastreador.{nombre}")
