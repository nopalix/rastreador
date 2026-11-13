"""Sesiones HTTP reutilizables con reintentos y delays aleatorios.

La estrategia replica la del proyecto QUICKSASA: una ``requests.Session`` por
plataforma, reintentos con backoff exponencial y una pausa aleatoria antes de
cada petición para no golpear los sitios públicos.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings


@dataclass(frozen=True)
class RetryPolicy:
    total: int = 3
    backoff_factor: float = 1.5
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504)
    allowed_methods: frozenset[str] = frozenset({"GET", "POST"})

    @classmethod
    def desde_settings(cls, settings: Settings) -> "RetryPolicy":
        return cls(
            total=max(0, settings.max_retries),
            backoff_factor=max(0.0, settings.backoff_factor),
        )


@dataclass(frozen=True)
class DelayPolicy:
    minimo: float = 1.0
    maximo: float = 2.5

    @classmethod
    def desde_settings(cls, settings: Settings) -> "DelayPolicy":
        minimo = max(0.0, settings.delay_min)
        maximo = max(minimo, settings.delay_max)
        return cls(minimo=minimo, maximo=maximo)

    def siguiente(self) -> float:
        if self.maximo <= self.minimo:
            return self.minimo
        return random.uniform(self.minimo, self.maximo)

    def dormir(self) -> None:
        pausa = self.siguiente()
        if pausa > 0:
            time.sleep(pausa)


class WebSession:
    """Sesión HTTP con autolimitación y logging seguro."""

    def __init__(
        self,
        nombre: str,
        *,
        timeout: int = 20,
        retry_policy: RetryPolicy | None = None,
        delay_policy: DelayPolicy | None = None,
        headers: dict[str, str] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.nombre = nombre
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self.delay_policy = delay_policy or DelayPolicy()
        self.log = logger or logging.getLogger(f"rfe_rastreador.http.{nombre}")
        self._lock = threading.Lock()
        self._ultima_peticion: float | None = None

        self._session = requests.Session()
        retry = Retry(
            total=self.retry_policy.total,
            connect=self.retry_policy.total,
            read=self.retry_policy.total,
            status=self.retry_policy.total,
            backoff_factor=self.retry_policy.backoff_factor,
            status_forcelist=list(self.retry_policy.status_forcelist),
            allowed_methods=set(self.retry_policy.allowed_methods),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self._session.headers.update(
            headers
            or {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
            }
        )

    def _esperar_turno(self) -> None:
        with self._lock:
            ahora = time.monotonic()
            pausa = self.delay_policy.siguiente()
            if self._ultima_peticion is not None:
                transcurrido = ahora - self._ultima_peticion
                pausa = max(0.0, pausa - transcurrido)
            if pausa > 0:
                time.sleep(pausa)
            self._ultima_peticion = time.monotonic()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        self._esperar_turno()

        partes = urlsplit(url)
        self.log.debug("%s %s%s", method.upper(), partes.netloc, partes.path or "/")
        return self._session.request(method.upper(), url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "WebSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class SessionManager:
    """Crea y cierra una sesión por plataforma."""

    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.log = logger or logging.getLogger("rfe_rastreador.http")
        self._retry = RetryPolicy.desde_settings(settings)
        self._delay = DelayPolicy.desde_settings(settings)
        self._sessions: dict[str, WebSession] = {}

    def get(self, nombre: str) -> WebSession:
        if nombre not in self._sessions:
            self._sessions[nombre] = WebSession(
                nombre,
                timeout=self.settings.request_timeout,
                retry_policy=self._retry,
                delay_policy=self._delay,
                headers={"User-Agent": self.settings.user_agent},
                logger=self.log.getChild(nombre),
            )
        return self._sessions[nombre]

    def close_all(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()

    def __enter__(self) -> "SessionManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close_all()
