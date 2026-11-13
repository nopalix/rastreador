"""Modelos de dominio compartidos por el pipeline y las reglas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class Fuente(str, Enum):
    """Plataformas consultadas."""

    SASA = "SASA"
    SIGA = "SIGA"
    SIGED = "SIGED"


class EstadoConsulta(str, Enum):
    """Resultado técnico de consultar una plataforma."""

    OK = "OK"
    NO_ENCONTRADO = "NO_ENCONTRADO"
    NO_CONFIGURADO = "NO_CONFIGURADO"
    ERROR = "ERROR"


class NivelAcademico(str, Enum):
    """Niveles normalizados de SASA (MEV) y SIGA (PEC)."""

    INICIAL = "INICIAL"
    INTERMEDIO = "INTERMEDIO"
    AVANZADO = "AVANZADO"
    PRIMARIA = "PRIMARIA"
    SECUNDARIA = "SECUNDARIA"
    DESCONOCIDO = "DESCONOCIDO"


class Categoria(str, Enum):
    """Categorías de salida del motor de reglas."""

    BASURA = "BASURA"
    CONTINUIDAD = "CONTINUIDAD"
    NO_ACREDITADO = "NO_ACREDITADO"
    NO_PRESENTADO = "NO_PRESENTADO"
    REVISION_MANUAL = "REVISION_MANUAL"
    SIN_DATOS = "SIN_DATOS"
    ERROR = "ERROR"

    @property
    def etiqueta(self) -> str:
        return {
            Categoria.BASURA: "Basura (excluir)",
            Categoria.CONTINUIDAD: "Continuidad",
            Categoria.NO_ACREDITADO: "No acreditado",
            Categoria.NO_PRESENTADO: "No presentado",
            Categoria.REVISION_MANUAL: "Revisión manual",
            Categoria.SIN_DATOS: "Sin datos",
            Categoria.ERROR: "Error de consulta",
        }[self]


@dataclass
class AcademicRecord:
    """Un registro académico puntual (nivel + calificación y contacto)."""

    fuente: Fuente
    nivel: NivelAcademico
    calificacion: float | None
    nivel_raw: str = ""
    calificacion_raw: str = ""
    detalles: dict[str, Any] = field(default_factory=dict)
    telefono: str = ""
    direccion: str = ""
    nombre: str = ""

    def como_dict(self) -> dict[str, Any]:
        return {
            "fuente": self.fuente.value,
            "nivel": self.nivel.value,
            "calificacion": self.calificacion,
            "nivel_raw": self.nivel_raw,
            "calificacion_raw": self.calificacion_raw,
            "detalles": dict(self.detalles),
            "telefono": self.telefono,
            "direccion": self.direccion,
            "nombre": self.nombre,
        }


@dataclass
class PlatformResult:
    """Resultado completo de consultar una plataforma para un RFE."""

    fuente: Fuente
    estado: EstadoConsulta
    registros: list[AcademicRecord] = field(default_factory=list)
    error: str | None = None
    consultado_en: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def tiene_datos(self) -> bool:
        return self.estado is EstadoConsulta.OK and bool(self.registros)

    def como_dict(self) -> dict[str, Any]:
        return {
            "fuente": self.fuente.value,
            "estado": self.estado.value,
            "registros": [r.como_dict() for r in self.registros],
            "error": self.error,
            "consultado_en": self.consultado_en.isoformat(),
        }


@dataclass
class Evaluation:
    """Clasificación final de un RFE con su evidencia académica."""

    rfe: str
    categoria: Categoria
    regla_id: str
    motivo: str
    evidencia: dict[str, Any] = field(default_factory=dict)
    resultados: dict[Fuente, PlatformResult] = field(default_factory=dict)

    @property
    def revisar_manual(self) -> bool:
        return self.categoria in {
            Categoria.REVISION_MANUAL,
            Categoria.SIN_DATOS,
            Categoria.ERROR,
        }


@dataclass
class InputRecord:
    """Fila válida del Excel de entrada."""

    rfe: str
    fila: int
    datos: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExcelReadResult:
    """Resultado de leer y validar el Excel de origen."""

    ruta: Path
    columna_rfe: str
    dataframe: Any  # pandas.DataFrame, sin importar pandas en este módulo
    registros: list[InputRecord]
    invalidos: list[InputRecord]
    duplicados: int


@dataclass
class PipelineResult:
    """Resumen de una corrida."""

    run_id: str
    output_dir: Path | None
    total: int
    procesados: int
    reanudados: int
    invalidos: int
    duplicados: int
    categorias: dict[str, int] = field(default_factory=dict)
