"""Checkpoints persistentes y reanudables.

Diseño:

- Un directorio por archivo de entrada (incluye hash del archivo). Si el Excel
  cambia, cambia el ``run_id`` y no se mezclan resultados de versiones distintas.
- Los resultados se guardan en un JSONL *append-only*: si el proceso muere a la
  mitad de una escritura, a lo sumo se pierde la última línea.
- ``estado.json`` se escribe de forma atómica (tmp + ``os.replace``) y se usa
  para métricas rápidas; la fuente de verdad es el JSONL.
- Un lock con ``fcntl`` evita dos procesos escribiendo el mismo checkpoint.
- Por defecto no se guardan RFE en claro: se guarda un hash/HMAC de la llave.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .errors import CheckpointLockedError, CheckpointMismatchError
from .models import Categoria, Evaluation
from .serialization import evaluation_from_dict, evaluation_to_dict

try:  # Linux / Docker
    import fcntl
except ImportError:  # pragma: no cover - solo para portabilidad
    fcntl = None  # type: ignore[assignment]


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(texto: str) -> str:
    limpio = re.sub(r"[^A-Za-z0-9_.-]+", "-", texto).strip(".-")
    return limpio[:80] or "run"


def fingerprint_archivo(ruta: Path) -> str:
    """SHA-256 del archivo de entrada, leído por bloques."""

    digest = hashlib.sha256()
    with open(ruta, "rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


@dataclass
class CheckpointState:
    run_id: str
    input_fingerprint: str
    input_path: str
    total: int
    processed: int = 0
    started_at: str = field(default_factory=_ahora)
    updated_at: str = field(default_factory=_ahora)
    category_counts: dict[str, int] = field(default_factory=dict)

    def como_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "input_fingerprint": self.input_fingerprint,
            "input_path": self.input_path,
            "total": self.total,
            "processed": self.processed,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "category_counts": dict(self.category_counts),
        }

    @classmethod
    def desde_dict(cls, datos: dict[str, Any]) -> "CheckpointState":
        return cls(
            run_id=str(datos["run_id"]),
            input_fingerprint=str(datos["input_fingerprint"]),
            input_path=str(datos.get("input_path", "")),
            total=int(datos.get("total", 0)),
            processed=int(datos.get("processed", 0)),
            started_at=str(datos.get("started_at", _ahora())),
            updated_at=str(datos.get("updated_at", _ahora())),
            category_counts=dict(datos.get("category_counts") or {}),
        )


class CheckpointManager:
    """Gestiona el estado de una corrida reanudable."""

    def __init__(
        self,
        state_dir: Path,
        run_id: str,
        input_fingerprint: str,
        input_path: str | Path = "",
        total: int = 0,
        identity_hasher: Callable[[str], str] | None = None,
        store_raw_identifiers: bool = False,
        dir_mode: int = 0o700,
        file_mode: int = 0o600,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.run_id = _slug(run_id)
        self.input_fingerprint = input_fingerprint
        self.input_path = str(input_path)
        self.total = total
        self.identity_hasher = identity_hasher or (lambda valor: str(valor))
        self.store_raw_identifiers = store_raw_identifiers
        self.dir_mode = dir_mode
        self.file_mode = file_mode

        self.run_dir = self.state_dir / self.run_id
        self.results_path = self.run_dir / "resultados.jsonl"
        self.state_path = self.run_dir / "estado.json"
        self.lock_path = self.run_dir / ".lock"

        self._lock_fd: int | None = None
        self._state: CheckpointState | None = None
        self._results_cache: dict[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # Construcción
    # ------------------------------------------------------------------
    @classmethod
    def for_input(
        cls,
        input_path: Path | str,
        state_dir: Path,
        total: int = 0,
        identity_hasher: Callable[[str], str] | None = None,
        store_raw_identifiers: bool = False,
        dir_mode: int = 0o700,
        file_mode: int = 0o600,
        run_suffix: str = "",
    ) -> "CheckpointManager":
        ruta = Path(input_path).expanduser().resolve()
        huella = fingerprint_archivo(ruta)
        sufijo = _slug(run_suffix) if run_suffix else ""
        base = _slug(ruta.stem)
        run_id = f"{base}-{sufijo}-{huella[:12]}" if sufijo else f"{base}-{huella[:12]}"
        return cls(
            state_dir=state_dir,
            run_id=run_id,
            input_fingerprint=huella,
            input_path=ruta,
            total=total,
            identity_hasher=identity_hasher,
            store_raw_identifiers=store_raw_identifiers,
            dir_mode=dir_mode,
            file_mode=file_mode,
        )

    # ------------------------------------------------------------------
    # Lock
    # ------------------------------------------------------------------
    def acquire(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.run_dir.chmod(self.dir_mode)
        except OSError:
            pass

        if fcntl is None:
            return

        self._lock_fd = os.open(
            self.lock_path, os.O_CREAT | os.O_RDWR, self.file_mode
        )
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._lock_fd)
            self._lock_fd = None
            raise CheckpointLockedError(
                f"El checkpoint {self.run_id} está siendo usado por otro proceso."
            ) from exc

    def release(self) -> None:
        if self._lock_fd is None or fcntl is None:
            return
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None

    def __enter__(self) -> "CheckpointManager":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    # ------------------------------------------------------------------
    # Estado / resultados
    # ------------------------------------------------------------------
    def _leer_state(self) -> CheckpointState | None:
        if not self.state_path.exists():
            return None
        try:
            datos = json.loads(self.state_path.read_text(encoding="utf-8"))
            return CheckpointState.desde_dict(datos)
        except (json.JSONDecodeError, KeyError, OSError, ValueError):
            return None

    def _escribir_state(self, state: CheckpointState) -> None:
        state.updated_at = _ahora()
        tmp = self.state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as archivo:
            json.dump(state.como_dict(), archivo, ensure_ascii=False, indent=2)
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(tmp, self.state_path)
        try:
            self.state_path.chmod(self.file_mode)
        except OSError:
            pass

    def _iter_lineas(self) -> Iterator[dict[str, Any]]:
        if not self.results_path.exists():
            return
        with open(self.results_path, "r", encoding="utf-8") as archivo:
            for linea in archivo:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    yield json.loads(linea)
                except json.JSONDecodeError:
                    # Línea cortada por un corte de energía: se ignora y la
                    # siguiente corrida volverá a procesar ese RFE.
                    continue

    def _cargar_resultados(self) -> dict[str, dict[str, Any]]:
        resultados: dict[str, dict[str, Any]] = {}
        for datos in self._iter_lineas():
            clave = datos.get("rfe_key")
            if clave:
                resultados[clave] = datos
        return resultados

    def load(self) -> tuple[CheckpointState, dict[str, dict[str, Any]]]:
        """Carga estado y resultados, validando que el input sea el mismo."""

        state = self._leer_state()
        if state is None:
            state = CheckpointState(
                run_id=self.run_id,
                input_fingerprint=self.input_fingerprint,
                input_path=self.input_path,
                total=self.total,
            )
        elif state.input_fingerprint != self.input_fingerprint:
            raise CheckpointMismatchError(
                "El checkpoint pertenece a otra versión del archivo de entrada "
                f"({state.input_fingerprint[:12]} != "
                f"{self.input_fingerprint[:12]})."
            )

        if self.total and state.total != self.total:
            # El total puede crecer si el Excel cambió, pero eso ya lo detecta
            # el fingerprint. Mantener el valor previo es seguro.
            state.total = max(state.total, self.total)

        self._state = state
        self._results_cache = self._cargar_resultados()
        return state, self._results_cache

    def completed_keys(self) -> set[str]:
        if self._results_cache is None:
            self.load()
        return set((self._results_cache or {}).keys())

    def rfe_key(self, rfe: str) -> str:
        return self.identity_hasher(rfe)

    def mark(self, evaluacion: Evaluation) -> str:
        """Añade una evaluación al checkpoint de forma durable."""

        if self._state is None or self._results_cache is None:
            self.load()

        assert self._state is not None
        assert self._results_cache is not None

        clave = self.rfe_key(evaluacion.rfe)
        datos = evaluation_to_dict(
            evaluacion, incluir_rfe=self.store_raw_identifiers
        )
        datos["rfe_key"] = clave

        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.results_path, "a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(datos, ensure_ascii=False) + "\n")
            archivo.flush()
            os.fsync(archivo.fileno())
        try:
            self.results_path.chmod(self.file_mode)
        except OSError:
            pass

        anterior = self._results_cache.get(clave)
        if anterior is not None:
            categoria_anterior = str(anterior.get("categoria", ""))
            if categoria_anterior in self._state.category_counts:
                self._state.category_counts[categoria_anterior] -= 1

        categoria = evaluacion.categoria.value
        self._state.category_counts[categoria] = (
            self._state.category_counts.get(categoria, 0) + 1
        )
        if anterior is None:
            self._state.processed += 1
        self._results_cache[clave] = datos
        self._escribir_state(self._state)
        return clave

    def load_evaluations(self) -> list[Evaluation]:
        """Devuelve las evaluaciones guardadas (sin RFE si no se guardó en claro)."""

        return [evaluacion for _, evaluacion in self.load_evaluations_con_clave()]

    def load_evaluations_con_clave(self) -> list[tuple[str, Evaluation]]:
        """Devuelve ``(rfe_key, evaluación)`` para poder rehidratar el RFE."""

        if self._results_cache is None:
            self.load()
        return [
            (clave, evaluation_from_dict(datos))
            for clave, datos in (self._results_cache or {}).items()
        ]

    def summary(self) -> dict[str, Any]:
        if self._state is None:
            self.load()
        assert self._state is not None
        return {
            "run_id": self._state.run_id,
            "input_path": self._state.input_path,
            "total": self._state.total,
            "processed": self._state.processed,
            "pendientes": max(0, self._state.total - self._state.processed),
            "categorias": dict(self._state.category_counts),
            "iniciado": self._state.started_at,
            "actualizado": self._state.updated_at,
        }

    # ------------------------------------------------------------------
    # Mantenimiento
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Borra resultados y estado de este run (conserva el lock)."""

        for ruta in (self.results_path, self.state_path):
            try:
                ruta.unlink()
            except FileNotFoundError:
                pass
        self._state = None
        self._results_cache = None

    def delete(self) -> None:
        """Elimina todo el directorio del checkpoint."""

        self.release()
        shutil.rmtree(self.run_dir, ignore_errors=True)


def contar_categorias(evaluaciones: list[Evaluation]) -> dict[str, int]:
    """Utilidad para resúmenes en memoria."""

    conteo: dict[str, int] = {}
    for evaluacion in evaluaciones:
        categoria = evaluacion.categoria.value
        conteo[categoria] = conteo.get(categoria, 0) + 1
    return conteo


__all__ = [
    "CheckpointManager",
    "CheckpointState",
    "Categoria",
    "contar_categorias",
    "fingerprint_archivo",
]
