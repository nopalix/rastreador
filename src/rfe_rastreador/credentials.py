"""Carga de credenciales sin hardcodear secretos.

Fuentes admitidas, en orden de precedencia:

1. Variables de entorno (``SASA_USER``, ``SASA_PASSWORD``, etc.).
2. Archivo JSON opcional indicado por ``RFE_CREDENTIALS_FILE``.

El archivo JSON puede tener esta forma::

    {
      "sasa": {"usuario": "...", "password": "..."},
      "siga": {"usuario": "...", "password": "..."},
      "siged": {"token": "..."}
    }

Nunca se imprimen valores; ``describe()`` solo informa qué campos existen.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .config import Settings
from .errors import ConfigurationError
from .models import Fuente


_PREFIJOS = {
    Fuente.SASA: "SASA",
    Fuente.SIGA: "SIGA",
    Fuente.SIGED: "SIGED",
}

_ALIAS_USUARIO = ("USER", "USUARIO")
_ALIAS_PASSWORD = ("PASSWORD", "PASS", "CONTRASENA", "CONTRASEÑA")
_ALIAS_TOKEN = ("TOKEN", "API_KEY", "APIKEY")


@dataclass(frozen=True)
class PlatformCredentials:
    usuario: str | None = None
    password: str | None = None
    token: str | None = None
    extras: Mapping[str, str] = field(default_factory=dict)

    @property
    def tiene_credenciales(self) -> bool:
        return bool(self.token or (self.usuario and self.password))

    @property
    def usa_basic_auth(self) -> bool:
        return bool(self.usuario and self.password and not self.token)

    def require(self, fuente: Fuente) -> None:
        if not self.tiene_credenciales:
            prefijo = _PREFIJOS[fuente]
            raise ConfigurationError(
                f"Faltan credenciales para {fuente.value}. Define "
                f"{prefijo}_USER y {prefijo}_PASSWORD, o {prefijo}_TOKEN."
            )

    def __repr__(self) -> str:
        usuario = self.usuario or ""
        return (
            "PlatformCredentials("
            f"usuario={'*' * len(usuario) if usuario else None}, "
            f"password={'***' if self.password else None}, "
            f"token={'***' if self.token else None})"
        )


def _primer_env(prefijo: str, aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        valor = os.getenv(f"{prefijo}_{alias}", "").strip()
        if valor:
            return valor
    return None


def _desde_json(datos: dict[str, Any], fuente: Fuente) -> PlatformCredentials:
    bloque = datos.get(fuente.value.lower()) or datos.get(fuente.value) or {}
    if not isinstance(bloque, dict):
        return PlatformCredentials()

    usuario = (
        bloque.get("usuario")
        or bloque.get("user")
        or bloque.get("username")
        or None
    )
    password = (
        bloque.get("password")
        or bloque.get("contrasena")
        or bloque.get("contraseña")
        or bloque.get("pass")
        or None
    )
    token = bloque.get("token") or bloque.get("api_key") or None
    conocidos = {
        "usuario",
        "user",
        "username",
        "password",
        "contrasena",
        "contraseña",
        "pass",
        "token",
        "api_key",
    }
    extras = {
        str(k): str(v)
        for k, v in bloque.items()
        if str(k).lower() not in conocidos and v is not None
    }
    return PlatformCredentials(
        usuario=str(usuario) if usuario else None,
        password=str(password) if password else None,
        token=str(token) if token else None,
        extras=extras,
    )


def _combinar(
    base: PlatformCredentials, override: PlatformCredentials
) -> PlatformCredentials:
    return PlatformCredentials(
        usuario=override.usuario or base.usuario,
        password=override.password or base.password,
        token=override.token or base.token,
        extras={**dict(base.extras), **dict(override.extras)},
    )


class CredentialsProvider:
    """Proveedor de credenciales por plataforma."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._json: dict[str, Any] = {}
        self._json_error: str | None = None
        self._cargar_json()

    def _cargar_json(self) -> None:
        ruta = self.settings.credentials_file
        if not ruta:
            return
        ruta = Path(ruta)
        if not ruta.exists():
            self._json_error = f"No existe el archivo {ruta}"
            return
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            if not isinstance(datos, dict):
                raise ValueError("El JSON raíz debe ser un objeto.")
            self._json = datos
            self._advertir_permisos(ruta)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            self._json_error = f"No se pudo leer {ruta}: {exc}"

    @staticmethod
    def _advertir_permisos(ruta: Path) -> None:
        try:
            modo = ruta.stat().st_mode & 0o777
        except OSError:
            return
        if modo & 0o077:
            import logging

            logging.getLogger("rfe_rastreador.credentials").warning(
                "El archivo de credenciales %s tiene permisos %o; "
                "se recomienda 600.",
                ruta,
                modo,
            )

    def get(self, fuente: Fuente) -> PlatformCredentials:
        desde_json = _desde_json(self._json, fuente) if self._json else PlatformCredentials()
        prefijo = _PREFIJOS[fuente]
        desde_env = PlatformCredentials(
            usuario=_primer_env(prefijo, _ALIAS_USUARIO),
            password=_primer_env(prefijo, _ALIAS_PASSWORD),
            token=_primer_env(prefijo, _ALIAS_TOKEN),
        )
        return _combinar(desde_json, desde_env)

    def describe(self) -> dict[str, dict[str, bool]]:
        """Resumen sin secretos para el comando ``doctor``."""

        salida: dict[str, dict[str, bool]] = {}
        for fuente in Fuente:
            cred = self.get(fuente)
            salida[fuente.value] = {
                "usuario": bool(cred.usuario),
                "password": bool(cred.password),
                "token": bool(cred.token),
                "completo": cred.tiene_credenciales,
            }
        return salida

    @property
    def json_error(self) -> str | None:
        return self._json_error


__all__ = ["CredentialsProvider", "PlatformCredentials"]
