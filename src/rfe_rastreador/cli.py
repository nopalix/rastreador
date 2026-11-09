"""CLI del rastreador RFE."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

from .config import Settings
from .credentials import CredentialsProvider
from .errors import (
    ConfigurationError,
    ConsentRequiredError,
    ExcelError,
    RfeRastreadorError,
)
from .logging_config import setup_logging
from .pipeline import crear_pipeline
from .privacy import CONSENTIMIENTO_TEXTO, PrivacyManager


COMANDOS = {"run", "consent", "doctor", "purge"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rfe_rastreador",
        description=(
            "Procesa un Excel de RFE, consulta SASA/SIGA/SIGED y clasifica "
            "con las reglas de Basura, Continuidad, No Acreditado y No Presentado."
        ),
    )
    parser.add_argument(
        "--version", action="version", version="rfe_rastreador 0.1.0"
    )

    sub = parser.add_subparsers(dest="comando")

    run = sub.add_parser("run", help="Ejecutar el pipeline.")
    run.add_argument(
        "--input",
        "-i",
        default=None,
        help="Ruta del Excel de entrada (por defecto data/input/entrada.xls).",
    )
    run.add_argument(
        "--sheet",
        default="0",
        help="Hoja de Excel: índice (0) o nombre.",
    )
    run.add_argument(
        "--column",
        "--columna-rfe",
        dest="columna_rfe",
        default=None,
        help="Nombre exacto de la columna RFE/CURP.",
    )
    run.add_argument(
        "--columna-zona",
        dest="columna_zona",
        default=None,
        help="Columna de zona (por defecto RFE_ZONE_COLUMN o 'CVE ZONA').",
    )
    run.add_argument(
        "--zonas",
        default=None,
        help=(
            "Zonas a procesar, separadas por comas (p. ej. 1101,1111). "
            "Si se omite, usa RFE_ZONAS; vacío = todas."
        ),
    )
    run.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Procesar como máximo N registros pendientes (pruebas).",
    )
    run.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignorar el checkpoint y volver a procesar todo.",
    )
    run.add_argument(
        "--accept-privacy",
        action="store_true",
        help="Aceptar el aviso de privacidad y continuar.",
    )
    run.add_argument(
        "--operator",
        default=os.getenv("USER", "operador"),
        help="Nombre del operador responsable de la corrida.",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Validar el Excel y el consentimiento sin consultar plataformas.",
    )
    run.add_argument(
        "--log-level",
        default=None,
        help="DEBUG, INFO, WARNING, ERROR (por defecto LOG_LEVEL).",
    )

    consent = sub.add_parser(
        "consent", help="Consultar, otorgar o revocar el consentimiento."
    )
    grupo = consent.add_mutually_exclusive_group()
    grupo.add_argument("--status", action="store_true", help="Ver estado.")
    grupo.add_argument("--grant", action="store_true", help="Otorgar.")
    grupo.add_argument("--revoke", action="store_true", help="Revocar.")
    consent.add_argument(
        "--operator",
        default=os.getenv("USER", "operador"),
        help="Nombre del operador.",
    )

    sub.add_parser("doctor", help="Diagnosticar configuración y dependencias.")

    purge = sub.add_parser(
        "purge", help="Eliminar salidas/checkpoints antiguos."
    )
    purge.add_argument(
        "--days",
        type=int,
        default=None,
        help="Antigüedad mínima en días (por defecto RETENTION_DAYS).",
    )

    return parser


def _resolver_hoja(valor: str) -> int | str:
    return int(valor) if str(valor).isdigit() else valor


def _imprimir_resumen(resultado) -> None:
    print()
    print("=" * 64)
    print("RESUMEN DE LA CORRIDA")
    print("=" * 64)
    print(f"Run ID..............: {resultado.run_id}")
    print(f"Salida..............: {resultado.output_dir}")
    print(f"Registros válidos...: {resultado.total}")
    print(f"Procesados..........: {resultado.procesados}")
    print(f"Reanudados..........: {resultado.reanudados}")
    print(f"Inválidos...........: {resultado.invalidos}")
    print(f"Duplicados..........: {resultado.duplicados}")
    print("-" * 64)
    for categoria, cantidad in sorted(resultado.categorias.items()):
        print(f"{categoria:<22}: {cantidad}")
    print("=" * 64)


def _cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    settings.ensure_dirs()
    setup_logging(settings, args.log_level)

    ruta = (
        Path(args.input).expanduser().resolve()
        if args.input
        else settings.input_dir / "entrada.xls"
    )
    if not ruta.exists():
        print(f"ERROR: no existe el archivo de entrada: {ruta}", file=sys.stderr)
        return 2

    # Permite el flujo interactivo cuando no se pasa --accept-privacy.
    if not args.accept_privacy:
        try:
            PrivacyManager(settings).ensure_consent(
                operator=args.operator,
                interactivo=sys.stdin.isatty(),
            )
        except ConsentRequiredError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print(CONSENTIMIENTO_TEXTO, file=sys.stderr)
            return 3

    pipeline = crear_pipeline(settings)
    try:
        zonas = None
        if args.zonas is not None:
            zonas = tuple(
                zona.strip() for zona in args.zonas.split(",") if zona.strip()
            )
        resultado = pipeline.run(
            ruta,
            hoja=_resolver_hoja(args.sheet),
            columna_rfe=args.columna_rfe,
            columna_zona=args.columna_zona,
            zonas=zonas,
            limit=args.limit,
            resume=not args.no_resume,
            accept_privacy=args.accept_privacy,
            operator=args.operator,
            dry_run=args.dry_run,
        )
    except (ExcelError, ConfigurationError, RfeRastreadorError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        pipeline.close()

    _imprimir_resumen(resultado)
    return 0


def _cmd_consent(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    settings.ensure_dirs()
    privacy = PrivacyManager(settings)

    if args.grant:
        registro = privacy.consent.grant(args.operator)
    elif args.revoke:
        registro = privacy.consent.revoke(args.operator)
    else:
        registro = privacy.consent.status()
        if registro is None:
            print("No hay consentimiento registrado.")
            print(f"Archivo esperado: {settings.consent_file}")
            return 1

    print(json.dumps(registro.como_dict(), ensure_ascii=False, indent=2))
    print(f"Vigente para la versión {settings.privacy_version}: "
          f"{'sí' if privacy.consent.is_valid() else 'no'}")
    return 0


def _cmd_doctor(_args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    settings.ensure_dirs()
    credentials = CredentialsProvider(settings)
    privacy = PrivacyManager(settings)

    print("=" * 64)
    print("DIAGNÓSTICO RFE RASTREADOR")
    print("=" * 64)

    print("\nDependencias:")
    obligatorias = ["pandas", "openpyxl", "xlrd", "requests", "bs4"]
    faltantes = []
    for modulo in obligatorias:
        disponible = importlib.util.find_spec(modulo) is not None
        print(f"  {modulo:<12}: {'OK' if disponible else 'FALTA'}")
        if not disponible:
            faltantes.append(modulo)

    print("\nRutas:")
    for nombre, ruta in (
        ("Entrada", settings.input_dir),
        ("Salida", settings.output_dir),
        ("Estado", settings.state_dir),
        ("Logs", settings.logs_dir),
    ):
        print(f"  {nombre:<8}: {ruta} ({'existe' if ruta.exists() else 'no existe'})")
    print(
        f"  Permisos: carpetas {settings.dir_mode:04o}, "
        f"archivos {settings.file_mode:04o}"
    )
    if settings.zonas:
        print(
            f"  Zonas   : {', '.join(settings.zonas)} "
            f"(columna '{settings.zone_column}')"
        )
    else:
        print("  Zonas   : sin filtro")

    print("\nPlataformas:")
    print(f"  SASA : {'configurada' if settings.sasa_url else 'SIN URL'}")
    credenciales_siga = credentials.describe().get("SIGA", {})
    if not settings.siga_url:
        siga_estado = "SIN URL"
    elif credenciales_siga.get("usuario") and credenciales_siga.get("password"):
        siga_estado = f"configurada (ciclos {','.join(settings.siga_ciclos)})"
    else:
        siga_estado = "SIN CREDENCIALES"
    print(f"  SIGA : {siga_estado}")
    siged_detalle = (
        f"{settings.siged_format}, modo {settings.siged_mode}"
        + (", requiere CURP" if settings.siged_format == "json" else "")
    )
    print(
        f"  SIGED: {'configurada' if settings.siged_url else 'SIN URL'} "
        f"({siged_detalle})"
    )
    if settings.siged_url and not settings.siged_verify_tls:
        print("  Aviso: verificación TLS de SIGED desactivada.")

    print("\nCredenciales (solo presencia):")
    for fuente, estado in credentials.describe().items():
        campos = [
            nombre
            for nombre in ("usuario", "password", "token")
            if estado.get(nombre)
        ]
        detalle = ", ".join(campos) if campos else "sin credenciales"
        print(f"  {fuente:<5}: {detalle}")
    if credentials.json_error:
        print(f"  Aviso: {credentials.json_error}")

    print("\nPrivacidad:")
    registro = privacy.consent.status()
    print(f"  Consentimiento: {'vigente' if privacy.consent.is_valid() else 'pendiente'}")
    if registro:
        print(f"  Versión: {registro.version} | Operador: {registro.operator}")

    archivos = sorted(settings.input_dir.glob("*.xls*"))
    print("\nArchivos de entrada:")
    if archivos:
        for archivo in archivos:
            print(f"  {archivo.name} ({archivo.stat().st_size} bytes)")
    else:
        print(f"  No hay archivos .xls/.xlsx en {settings.input_dir}")

    if faltantes:
        print(
            "\nInstala las dependencias faltantes con: "
            "pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    settings.ensure_dirs()
    eliminados = PrivacyManager(settings).purge_old(args.days)
    print(f"Eliminados {len(eliminados)} elementos:")
    for ruta in eliminados:
        print(f"  {ruta}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)
    if not argumentos or (
        argumentos[0] not in COMANDOS
        and argumentos[0] not in {"-h", "--help", "--version"}
    ):
        argumentos.insert(0, "run")

    parser = _parser()
    args = parser.parse_args(argumentos)

    if args.comando == "consent":
        return _cmd_consent(args)
    if args.comando == "doctor":
        return _cmd_doctor(args)
    if args.comando == "purge":
        return _cmd_purge(args)
    return _cmd_run(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
