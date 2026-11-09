"""Configuración central del rastreador RFE.

Toda la configuración se obtiene de variables de entorno para que el mismo
código funcione en local y dentro de Docker sin cambios. Los valores sensibles
(credenciales) nunca se escriben aquí: se leen con `credentials.py`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(
    os.getenv("RFE_PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))
).expanduser()


def _cargar_dotenv(ruta: Path) -> None:
    """Carga un archivo .env sin sobrescribir variables ya definidas.

    Se implementa sin dependencias externas para que el arranque del contenedor
    sea predecible incluso si `python-dotenv` no está instalado.
    """

    if not ruta.exists():
        return

    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if linea.startswith("export "):
            linea = linea[len("export ") :].strip()
        if "=" not in linea:
            continue

        clave, valor = linea.split("=", 1)
        clave = clave.strip()
        valor = valor.strip().strip("'").strip('"')
        if clave:
            os.environ.setdefault(clave, valor)


def _env(nombre: str, default: str = "") -> str:
    return os.getenv(nombre, default).strip()


def _env_bool(nombre: str, default: bool) -> bool:
    valor = _env(nombre, "1" if default else "0").lower()
    return valor in {"1", "true", "yes", "si", "sí", "on"}


def _env_int(nombre: str, default: int) -> int:
    try:
        return int(_env(nombre, str(default)))
    except ValueError:
        return default


def _env_float(nombre: str, default: float) -> float:
    try:
        return float(_env(nombre, str(default)))
    except ValueError:
        return default


def _env_list(nombre: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Lista separada por comas; si viene vacía, conserva el valor por defecto."""

    valores = tuple(
        elemento.strip() for elemento in _env(nombre).split(",") if elemento.strip()
    )
    return valores or default


def _env_mode(nombre: str, default: int) -> int:
    """Modo octal (p. ej. ``2770`` o ``0o2770``) con validación."""

    valor = _env(nombre)
    if not valor:
        return default
    texto = valor.lower()
    if texto.startswith("0o"):
        texto = texto[2:]
    try:
        modo = int(texto, 8)
    except ValueError:
        return default
    return modo if 0 <= modo <= 0o7777 else default


def _ruta(valor: str, default: Path) -> Path:
    if not valor:
        return default
    ruta = Path(valor).expanduser()
    if not ruta.is_absolute():
        ruta = PROJECT_ROOT / ruta
    return ruta.resolve()


@dataclass(frozen=True)
class Settings:
    """Configuración inmutable del proceso."""

    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    input_dir: Path = PROJECT_ROOT / "data" / "input"
    output_dir: Path = PROJECT_ROOT / "data" / "output"
    state_dir: Path = PROJECT_ROOT / "data" / "state"
    logs_dir: Path = PROJECT_ROOT / "data" / "logs"

    # --- SASA / QuickSASA ---
    sasa_url: str = "http://168.255.120.98/quickSASA/BuscarRFE"
    sasa_estado: str = "gto"
    sasa_method: str = "POST"

    # --- SIGA (PEC) ---
    siga_url: str = ""
    siga_method: str = "GET"
    siga_rfe_param: str = "rfe"
    # Ciclos escolares que se consultan en SIGA (Participantes).
    siga_ciclos: tuple[str, ...] = ("2016", "2017")
    # Renueva la sesión de SIGA tras N peticiones para evitar expiración.
    siga_relogin_cada: int = 500

    # --- SIGED ---
    # API pública de consulta del alumnado. Requiere CURP, no RFE.
    # Se deja vacía por defecto para no consultar un servicio externo sin
    # configuración explícita; .env.example trae la URL recomendada.
    siged_url: str = ""
    siged_method: str = "GET"
    siged_id_param: str = "rfe"
    siged_format: str = "json"  # json | html
    siged_mode: str = "on_demand"  # always | on_demand | never
    siged_verify_tls: bool = True

    # --- Red / resiliencia ---
    request_timeout: int = 20
    max_retries: int = 3
    backoff_factor: float = 1.5
    delay_min: float = 1.0
    delay_max: float = 2.5
    record_delay_min: float = 0.0
    record_delay_max: float = 0.5
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # --- Privacidad / consentimiento ---
    privacy_version: str = "1.0"
    consent_file: Path = PROJECT_ROOT / "data" / "state" / "consentimiento.json"
    privacy_salt_file: Path = PROJECT_ROOT / "data" / "state" / ".privacy_salt"
    checkpoint_hash_identifiers: bool = True
    checkpoint_store_raw_identifiers: bool = False
    retention_days: int = 90

    # --- Credenciales ---
    credentials_file: Path | None = None

    # --- Permisos de archivos y carpetas ---
    # Por defecto todo es privado. En Docker conviene 2770/660 para que el
    # grupo del host pueda leer las salidas sin exponerlas a otros usuarios.
    dir_mode: int = 0o700
    file_mode: int = 0o600

    # --- Excel ---
    valid_key_lengths: tuple[int, ...] = (13, 18)
    # Columna del Excel que contiene la zona y zonas a procesar. Si ``zonas``
    # está vacío se procesan todas las filas (comportamiento histórico).
    zone_column: str = "CVE ZONA"
    zonas: tuple[str, ...] = ()

    # --- Recuperación de fallos transitorios ---
    # Un ERROR de red no debe quedar marcado como definitivo: se reintenta en
    # la siguiente corrida y una vez más al final de la corrida actual.
    retry_errors: bool = True

    # --- Logging ---
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        """Construye la configuración leyendo `.env` y el entorno del proceso."""

        _cargar_dotenv(PROJECT_ROOT / ".env")

        data_dir = _ruta(_env("RFE_DATA_DIR"), PROJECT_ROOT / "data")
        input_dir = _ruta(_env("RFE_INPUT_DIR"), data_dir / "input")
        output_dir = _ruta(_env("RFE_OUTPUT_DIR"), data_dir / "output")
        state_dir = _ruta(_env("RFE_STATE_DIR"), data_dir / "state")
        logs_dir = _ruta(_env("RFE_LOGS_DIR"), data_dir / "logs")

        credenciales_raw = _env("RFE_CREDENTIALS_FILE")
        credentials_file = (
            _ruta(credenciales_raw, PROJECT_ROOT / "credentials.json")
            if credenciales_raw
            else None
        )

        return cls(
            project_root=PROJECT_ROOT,
            data_dir=data_dir,
            input_dir=input_dir,
            output_dir=output_dir,
            state_dir=state_dir,
            logs_dir=logs_dir,
            sasa_url=_env(
                "SASA_URL", "http://168.255.120.98/quickSASA/BuscarRFE"
            ),
            sasa_estado=_env("SASA_ESTADO", "gto"),
            sasa_method=_env("SASA_METHOD", "POST").upper(),
            siga_url=_env("SIGA_URL"),
            siga_method=_env("SIGA_METHOD", "GET").upper(),
            siga_rfe_param=_env("SIGA_RFE_PARAM", "rfe"),
            siga_ciclos=_env_list("SIGA_CICLOS", ("2016", "2017")),
            siga_relogin_cada=_env_int("SIGA_RELOGIN_CADA", 500),
            siged_url=_env("SIGED_URL"),
            siged_method=_env("SIGED_METHOD", "GET").upper(),
            siged_id_param=_env("SIGED_ID_PARAM", "rfe"),
            siged_format=_env("SIGED_FORMAT", "json").lower(),
            siged_mode=_env("SIGED_MODE", "on_demand").lower(),
            siged_verify_tls=_env_bool("SIGED_VERIFY_TLS", True),
            request_timeout=_env_int("REQUEST_TIMEOUT", 20),
            max_retries=_env_int("MAX_RETRIES", 3),
            backoff_factor=_env_float("BACKOFF_FACTOR", 1.5),
            delay_min=_env_float("DELAY_MIN", 1.0),
            delay_max=_env_float("DELAY_MAX", 2.5),
            record_delay_min=_env_float("RECORD_DELAY_MIN", 0.0),
            record_delay_max=_env_float("RECORD_DELAY_MAX", 0.5),
            user_agent=_env(
                "HTTP_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            ),
            privacy_version=_env("PRIVACY_VERSION", "1.0"),
            consent_file=_ruta(
                _env("CONSENT_FILE"), state_dir / "consentimiento.json"
            ),
            privacy_salt_file=_ruta(
                _env("PRIVACY_SALT_FILE"), state_dir / ".privacy_salt"
            ),
            checkpoint_hash_identifiers=_env_bool(
                "CHECKPOINT_HASH_IDENTIFIERS", True
            ),
            checkpoint_store_raw_identifiers=_env_bool(
                "CHECKPOINT_STORE_RAW", False
            ),
            retention_days=_env_int("RETENTION_DAYS", 90),
            credentials_file=credentials_file,
            dir_mode=_env_mode("RFE_DIR_MODE", 0o700),
            file_mode=_env_mode("RFE_FILE_MODE", 0o600),
            retry_errors=_env_bool("RETRY_ERRORS", True),
            valid_key_lengths=(
                _env_int("RFE_LENGTH", 13),
                _env_int("CURP_LENGTH", 18),
            ),
            zone_column=_env("RFE_ZONE_COLUMN", "CVE ZONA"),
            zonas=_env_list("RFE_ZONAS", ()),
            log_level=_env("LOG_LEVEL", "INFO").upper(),
        )

    def ensure_dirs(self) -> None:
        """Crea las carpetas de trabajo con permisos restrictivos."""

        for ruta in (
            self.data_dir,
            self.input_dir,
            self.output_dir,
            self.state_dir,
            self.logs_dir,
        ):
            existia = ruta.exists()
            ruta.mkdir(parents=True, exist_ok=True)
            # Solo se restringe el modo al crear la carpeta. Si ya existía se
            # respeta el modo puesto por el operador (p. ej. 2770 para
            # compartir el volumen entre el contenedor y el usuario host).
            if existia:
                continue
            try:
                ruta.chmod(self.dir_mode)
            except OSError:
                # En algunos volúmenes montados (p. ej. ciertos drivers de
                # Windows) no se pueden cambiar permisos. No es fatal.
                pass
