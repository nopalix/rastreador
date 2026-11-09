"""Excepciones del dominio del rastreador RFE."""

from __future__ import annotations


class RfeRastreadorError(Exception):
    """Error base del proyecto."""


class ConfigurationError(RfeRastreadorError):
    """Falta configuración obligatoria o es inválida."""


class ExcelError(RfeRastreadorError):
    """El archivo de entrada no se puede leer o no tiene la columna esperada."""


class ConsentRequiredError(RfeRastreadorError):
    """No hay consentimiento vigente para tratar datos personales."""


class CheckpointError(RfeRastreadorError):
    """Error genérico del sistema de checkpoints."""


class CheckpointLockedError(CheckpointError):
    """Otro proceso está usando el mismo checkpoint."""


class CheckpointMismatchError(CheckpointError):
    """El checkpoint pertenece a otra versión del archivo de entrada."""


class PlatformError(RfeRastreadorError):
    """Error al consultar una plataforma."""


class PlatformNotConfiguredError(PlatformError):
    """La plataforma no tiene URL o credenciales configuradas."""


class AuthenticationError(PlatformError):
    """La plataforma rechazó la sesión o devolvió un formulario de login."""
