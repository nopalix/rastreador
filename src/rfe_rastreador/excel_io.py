"""Lectura del Excel de entrada y escritura de salidas agrupadas."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .errors import ExcelError
from .models import (
    Categoria,
    Evaluation,
    ExcelReadResult,
    Fuente,
    InputRecord,
)
from .normalization import es_rfe_valida, normalizar_rfe, normalizar_texto
from .privacy import PrivacyManager


COLUMNA_RFE_INTERNA = "_RFE_NORMALIZADO"

_ALIAS_RFE = (
    "RFE",
    "MATRICULA",
    "MATRÍCULA",
    "CURP",
    "CLAVE",
    "LLAVE",
)

_CATEGORIAS_SALIDA = (
    Categoria.BASURA,
    Categoria.CONTINUIDAD,
    Categoria.NO_ACREDITADO,
    Categoria.NO_PRESENTADO,
    Categoria.REVISION_MANUAL,
    Categoria.SIN_DATOS,
    Categoria.ERROR,
)


def detectar_columna_rfe(
    columnas: Iterable[Any], preferida: str | None = None
) -> str:
    """Encuentra la columna RFE/CURP por nombre, con tolerancia a variantes."""

    columnas = list(columnas)
    if not columnas:
        raise ExcelError("El archivo no tiene columnas.")

    if preferida:
        for columna in columnas:
            if str(columna) == preferida:
                return str(columna)
        normalizada = normalizar_texto(preferida)
        for columna in columnas:
            if normalizar_texto(columna) == normalizada:
                return str(columna)
        raise ExcelError(
            f"No existe la columna '{preferida}'. Columnas disponibles: "
            f"{', '.join(map(str, columnas))}"
        )

    normalizadas = {normalizar_texto(c): str(c) for c in columnas}
    for alias in _ALIAS_RFE:
        if alias in normalizadas:
            return normalizadas[alias]

    for alias in ("RFE", "CURP", "MATRICULA", "CLAVE", "LLAVE"):
        for normalizada, original in normalizadas.items():
            if alias in normalizada:
                return original

    raise ExcelError(
        "No se encontró una columna RFE/CURP. Columnas disponibles: "
        f"{', '.join(map(str, columnas))}"
    )


def detectar_columna_zona(
    columnas: Iterable[Any], preferida: str = "CVE ZONA"
) -> str:
    """Encuentra la columna de zona por nombre exacto o normalizado."""

    columnas = list(columnas)
    for columna in columnas:
        if str(columna) == preferida:
            return str(columna)
    normalizada = normalizar_texto(preferida)
    for columna in columnas:
        if normalizar_texto(columna) == normalizada:
            return str(columna)
    raise ExcelError(
        f"No existe la columna de zona '{preferida}'. Columnas disponibles: "
        f"{', '.join(map(str, columnas))}"
    )


def _normalizar_zona(valor: Any) -> str:
    """Normaliza 1101, '1101' y 1101.0 a la misma clave ``1101``."""

    texto = normalizar_texto(valor)
    if texto.endswith(".0"):
        texto = texto[:-2]
    return texto


def leer_excel(
    ruta: str | Path,
    *,
    hoja: int | str = 0,
    columna_rfe: str | None = None,
    longitudes: tuple[int, ...] = (13, 18),
    columna_zona: str = "CVE ZONA",
    zonas: tuple[str, ...] = (),
) -> ExcelReadResult:
    """Lee el Excel, filtra por zona, valida la llave y deduplica por RFE."""

    ruta = Path(ruta)
    if not ruta.exists():
        raise ExcelError(f"No existe el archivo de entrada: {ruta}")

    try:
        dataframe = pd.read_excel(ruta, sheet_name=hoja, dtype=object)
    except Exception as exc:
        raise ExcelError(f"No se pudo leer {ruta}: {exc}") from exc

    if dataframe.empty:
        raise ExcelError(f"El archivo {ruta} no contiene filas.")

    dataframe = dataframe.copy()
    if zonas:
        columna_zonas = detectar_columna_zona(dataframe.columns, columna_zona)
        objetivo = {_normalizar_zona(zona) for zona in zonas if str(zona).strip()}
        mascara_zona = dataframe[columna_zonas].map(_normalizar_zona).isin(objetivo)
        dataframe = dataframe[mascara_zona].copy()
        if dataframe.empty:
            raise ExcelError(
                "Ninguna fila coincide con las zonas configuradas "
                f"({', '.join(sorted(objetivo))}) en la columna "
                f"'{columna_zonas}'."
            )

    columna = detectar_columna_rfe(dataframe.columns, columna_rfe)
    dataframe[COLUMNA_RFE_INTERNA] = dataframe[columna].map(normalizar_rfe)

    mascara_valida = dataframe[COLUMNA_RFE_INTERNA].map(
        lambda valor: es_rfe_valida(valor, longitudes)
    )
    validos = dataframe[mascara_valida].copy()
    invalidos_df = dataframe[~mascara_valida].copy()

    duplicados = int(validos[COLUMNA_RFE_INTERNA].duplicated().sum())
    unicos = validos.drop_duplicates(
        subset=COLUMNA_RFE_INTERNA, keep="first"
    ).copy()

    registros = [
        InputRecord(
            rfe=str(fila[COLUMNA_RFE_INTERNA]),
            fila=int(indice) + 2,  # +2: encabezado y base 1 de Excel
            datos={
                str(k): v
                for k, v in fila.items()
                if str(k) != COLUMNA_RFE_INTERNA
            },
        )
        for indice, fila in unicos.iterrows()
    ]
    invalidos = [
        InputRecord(
            rfe=normalizar_rfe(fila[columna]),
            fila=int(indice) + 2,
            datos={str(k): v for k, v in fila.items()},
        )
        for indice, fila in invalidos_df.iterrows()
    ]

    return ExcelReadResult(
        ruta=ruta,
        columna_rfe=columna,
        dataframe=unicos,
        registros=registros,
        invalidos=invalidos,
        duplicados=duplicados,
    )


def _unir_valores(
    evaluacion: Evaluation, fuente: Fuente, campo: str
) -> str:
    resultado = evaluacion.resultados.get(fuente)
    if resultado is None:
        return ""
    valores: list[str] = []
    for registro in resultado.registros:
        if campo == "nivel":
            valores.append(registro.nivel_raw or registro.nivel.value)
        elif campo == "calificacion":
            valores.append(
                "" if registro.calificacion is None else f"{registro.calificacion:g}"
            )
    return " | ".join(valores)


def construir_dataframe_salida(
    evaluaciones: list[Evaluation],
    lectura: ExcelReadResult,
) -> pd.DataFrame:
    """Une las filas originales con la clasificación y la evidencia."""

    evaluaciones_por_rfe = {e.rfe: e for e in evaluaciones if e.rfe}
    dataframe = lectura.dataframe.copy()
    if COLUMNA_RFE_INTERNA not in dataframe.columns:
        raise ExcelError("Falta la columna interna de RFE.")

    # Solo se exportan los registros que ya fueron procesados. Así una corrida
    # con --limit no etiqueta como "SIN_DATOS" a los pendientes.
    dataframe = dataframe[
        dataframe[COLUMNA_RFE_INTERNA].isin(evaluaciones_por_rfe)
    ].copy()

    filas: list[dict[str, Any]] = []
    for _, fila in dataframe.iterrows():
        rfe = str(fila[COLUMNA_RFE_INTERNA])
        evaluacion = evaluaciones_por_rfe.get(rfe)
        if evaluacion is None:
            continue

        salida = {
            str(k): v
            for k, v in fila.items()
            if str(k) != COLUMNA_RFE_INTERNA
        }
        salida.update(
            {
                "Categoria": evaluacion.categoria.value,
                "Etiqueta_Categoria": evaluacion.categoria.etiqueta,
                "Regla": evaluacion.regla_id,
                "Motivo": evaluacion.motivo,
                "Revisar_Manual": evaluacion.revisar_manual,
            }
        )

        for fuente in Fuente:
            resultado = evaluacion.resultados.get(fuente)
            salida[f"{fuente.value}_Estado"] = (
                resultado.estado.value if resultado else ""
            )
            salida[f"{fuente.value}_Nivel"] = _unir_valores(
                evaluacion, fuente, "nivel"
            )
            salida[f"{fuente.value}_Calificacion"] = _unir_valores(
                evaluacion, fuente, "calificacion"
            )

        salida["Evidencia"] = json.dumps(
            evaluacion.evidencia, ensure_ascii=False
        )
        filas.append(salida)

    return pd.DataFrame(filas)


def _escribir_seguro(
    dataframe: pd.DataFrame, destino: Path, file_mode: int = 0o600
) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(f"{destino.stem}.tmp.xlsx")
    dataframe.to_excel(temporal, index=False, engine="openpyxl")
    os.replace(temporal, destino)
    try:
        destino.chmod(file_mode)
    except OSError:
        pass


def escribir_salidas(
    evaluaciones: list[Evaluation],
    lectura: ExcelReadResult,
    output_dir: str | Path,
    run_id: str,
    privacy: PrivacyManager | None = None,
    dir_mode: int = 0o700,
    file_mode: int = 0o600,
) -> Path:
    """Escribe un Excel por categoría, el consolidado y el resumen."""

    run_dir = Path(output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.chmod(dir_mode)
    except OSError:
        pass

    dataframe = construir_dataframe_salida(evaluaciones, lectura)
    if dataframe.empty:
        # Aun sin filas procesadas se escribe un resumen para dejar evidencia.
        dataframe = pd.DataFrame(columns=["Categoria", "Motivo"])

    for categoria in _CATEGORIAS_SALIDA:
        subconjunto = dataframe[dataframe["Categoria"] == categoria.value]
        if subconjunto.empty:
            continue
        _escribir_seguro(
            subconjunto, run_dir / f"{categoria.value}.xlsx", file_mode
        )

    _escribir_seguro(dataframe, run_dir / "TODOS.xlsx", file_mode)

    conteos = (
        dataframe["Categoria"].value_counts().to_dict()
        if "Categoria" in dataframe.columns
        else {}
    )
    resumen = pd.DataFrame(
        [
            {
                "Categoria": categoria.value,
                "Etiqueta": categoria.etiqueta,
                "Total": int(conteos.get(categoria.value, 0)),
            }
            for categoria in _CATEGORIAS_SALIDA
        ]
    )
    resumen.loc[len(resumen)] = {
        "Categoria": "TOTAL",
        "Etiqueta": "Total procesado",
        "Total": int(len(dataframe)),
    }
    _escribir_seguro(resumen, run_dir / "RESUMEN.xlsx", file_mode)

    if lectura.invalidos:
        invalidos_df = pd.DataFrame(
            [
                {
                    "Fila": registro.fila,
                    "RFE": registro.rfe,
                    **{
                        str(k): v
                        for k, v in registro.datos.items()
                        if str(k) != COLUMNA_RFE_INTERNA
                    },
                }
                for registro in lectura.invalidos
            ]
        )
        _escribir_seguro(invalidos_df, run_dir / "INVALIDOS.xlsx", file_mode)

    return run_dir
