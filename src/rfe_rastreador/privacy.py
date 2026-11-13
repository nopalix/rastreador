"""Privacidad, consentimiento y minimización de datos.

El proyecto trata datos personales de educandos. Antes de consultar cualquier
plataforma se exige un consentimiento explícito y versionado. Además:

- Los logs y checkpoints no guardan el RFE en claro (se usa HMAC-SHA256).
- Las utilidades de máscara permiten mostrar identificadores en consola.
- La retención de salidas/checkpoints se puede limitar por días.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import ConsentRequiredError
from .normalization import normalizar_rfe


CONSENTIMIENTO_TEXTO = """\
AVISO DE PRIVACIDAD Y CONSENTIMIENTO

Este proceso consulta información académica en SASA, SIGA y SIGED usando el
RFE del educando. Los datos obtenidos se usarán únicamente para clasificar su
situación académica y priorizar el contacto institucional.

- Los datos se almacenan en el volumen local del contenedor.
- Los checkpoints se guardan con identificadores seudonimizados (HMAC).
- Los logs no incluyen nombre, teléfono ni RFE completo.
- La información se conserva únicamente el tiempo necesario y se puede purgar.

Para continuar debes aceptar el tratamiento de datos personales. Puedes hacerlo
con la bandera --accept-privacy o escribiendo ACEPTO en una terminal interactiva.
"""


@dataclass(frozen=True)
class ConsentRecord:
    version: str
    accepted: bool
    accepted_at: str
    operator: str
    scope: str

    def como_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "accepted": self.accepted,
            "accepted_at": self.accepted_at,
            "operator": self.operator,
            "scope": self.scope,
        }

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> "ConsentRecord":
        return cls(
            version=str(datos.get("version", "")),
            accepted=bool(datos.get("accepted", False)),
            accepted_at=str(datos.get("accepted_at", "")),
            operator=str(datos.get("operator", "")),
            scope=str(datos.get("scope", "")),
        )


class ConsentManager:
    """Lee y escribe el consentimiento en un archivo JSON local."""

    def __init__(self, ruta: Path, version: str, file_mode: int = 0o600) -> None:
        self.ruta = Path(ruta)
        self.version = version
        self.file_mode = file_mode

    def _leer(self) -> ConsentRecord | None:
        if not self.ruta.exists():
            return None
        try:
            datos = json.loads(self.ruta.read_text(encoding="utf-8"))
            return ConsentRecord.desde_dict(datos)
        except (json.JSONDecodeError, OSError, ValueError):
            return None

    def _escribir(self, registro: ConsentRecord) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as archivo:
            json.dump(registro.como_dict(), archivo, ensure_ascii=False, indent=2)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(tmp, self.ruta)
        try:
            self.ruta.chmod(self.file_mode)
        except OSError:
            pass

    def status(self) -> ConsentRecord | None:
        return self._leer()

    def is_valid(self) -> bool:
        registro = self._leer()
        return bool(registro and registro.accepted and registro.version == self.version)

    def grant(
        self,
        operator: str,
        scope: str = "procesamiento_rfe",
    ) -> ConsentRecord:
        registro = ConsentRecord(
            version=self.version,
            accepted=True,
            accepted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            operator=operator.strip() or "operador",
            scope=scope,
        )
        self._escribir(registro)
        return registro

    def revoke(self, operator: str = "") -> ConsentRecord:
        registro = ConsentRecord(
            version=self.version,
            accepted=False,
            accepted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            operator=operator.strip() or "operador",
            scope="revocado",
        )
        self._escribir(registro)
        return registro

    def require(
        self,
        *,
        aceptar: bool = False,
        operator: str = "",
        interactivo: bool = False,
    ) -> ConsentRecord:
        actual = self._leer()
        if actual and actual.accepted and actual.version == self.version:
            return actual

        if aceptar:
            return self.grant(operator)

        if interactivo:
            print(CONSENTIMIENTO_TEXTO)
            respuesta = input("Escribe ACEPTO para continuar: ").strip().upper()
            if respuesta == "ACEPTO":
                return self.grant(operator)
            raise ConsentRequiredError(
                "No se otorgó el consentimiento; no se procesarán datos."
            )

        raise ConsentRequiredError(
            "No hay consentimiento vigente. Ejecuta con --accept-privacy "
            "o desde una terminal interactiva."
        )


class PrivacyManager:
    """Fachada de privacidad usada por el pipeline."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.consent = ConsentManager(
            settings.consent_file, settings.privacy_version, settings.file_mode
        )
        self._salt: bytes | None = None

    # ------------------------------------------------------------------
    # Consentimiento
    # ------------------------------------------------------------------
    def ensure_consent(
        self,
        *,
        aceptar: bool = False,
        operator: str = "",
        interactivo: bool = False,
    ) -> ConsentRecord:
        return self.consent.require(
            aceptar=aceptar, operator=operator, interactivo=interactivo
        )

    # ------------------------------------------------------------------
    # Seudonimización
    # ------------------------------------------------------------------
    def _obtener_salt(self) -> bytes:
        if self._salt is not None:
            return self._salt

        salt_env = os.getenv("PRIVACY_SALT", "").strip()
        if salt_env:
            self._salt = salt_env.encode("utf-8")
            return self._salt

        ruta = Path(self.settings.privacy_salt_file)
        contenido = ruta.read_bytes().strip() if ruta.exists() else b""
        if contenido:
            self._salt = contenido
        else:
            self._salt = secrets.token_hex(32).encode("ascii")
            ruta.parent.mkdir(parents=True, exist_ok=True)
            with open(ruta, "wb") as archivo:
                archivo.write(self._salt)
                archivo.flush()
                os.fsync(archivo.fileno())
            try:
                ruta.chmod(self.settings.file_mode)
            except OSError:
                pass
        return self._salt

    def rfe_key(self, rfe: str) -> str:
        """Clave estable para checkpoints; no reversible sin el salt."""

        normalizado = normalizar_rfe(rfe)
        if not self.settings.checkpoint_hash_identifiers:
            return normalizado
        return hmac.new(
            self._obtener_salt(),
            normalizado.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ------------------------------------------------------------------
    # Máscaras para consola / logs
    # ------------------------------------------------------------------
    @staticmethod
    def mask_rfe(rfe: str) -> str:
        texto = normalizar_rfe(rfe)
        if len(texto) <= 5:
            return "*" * len(texto)
        return f"{texto[:3]}{'*' * (len(texto) - 5)}{texto[-2:]}"

    @staticmethod
    def mask_nombre(nombre: str) -> str:
        partes = [p for p in re.split(r"\s+", (nombre or "").strip()) if p]
        if not partes:
            return ""
        return " ".join(
            f"{parte[0]}.{'*' * max(1, len(parte) - 1)}" for parte in partes
        )

    @staticmethod
    def mask_telefono(telefono: str) -> str:
        digitos = re.sub(r"\D", "", str(telefono or ""))
        if len(digitos) <= 4:
            return "*" * len(digitos)
        return f"{'*' * (len(digitos) - 4)}{digitos[-4:]}"

    # ------------------------------------------------------------------
    # Retención
    # ------------------------------------------------------------------
    def purge_old(self, days: int | None = None) -> list[Path]:
        """Elimina corridas de salida y checkpoints más antiguos que ``days``."""

        dias = self.settings.retention_days if days is None else days
        limite = datetime.now(timezone.utc) - timedelta(days=max(0, dias))
        eliminados: list[Path] = []

        for base in (Path(self.settings.output_dir), Path(self.settings.state_dir)):
            if not base.exists():
                continue
            for hijo in base.iterdir():
                if hijo.name in {"consentimiento.json", ".privacy_salt"}:
                    continue
                try:
                    modificado = datetime.fromtimestamp(
                        hijo.stat().st_mtime, tz=timezone.utc
                    )
                except OSError:
                    continue
                if modificado >= limite:
                    continue
                if hijo.is_dir():
                    shutil.rmtree(hijo, ignore_errors=True)
                else:
                    try:
                        hijo.unlink()
                    except OSError:
                        continue
                eliminados.append(hijo)

        return eliminados
