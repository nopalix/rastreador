"""Cliente SIGA (INEA, módulo Figuras).

Flujo real verificado en noviembre de 2026::

    GET  /Account/Login                     -> formulario + token antiforgery
    POST /Account/Login                     -> UserName / Password
    GET  /Actividad/Participantes?RFE=...   -> tabla de resultados por ciclo
    GET  /Actividad/Participantes/Seleccionar/{token}?ciclo=YYYY
         (X-Requested-With: XMLHttpRequest) -> detalle del participante

El detalle muestra ``Nivel: Primaria`` y la calificación dentro de un
``<h1>`` del bloque ``jumbotron`` (por ejemplo ``9.4``). Esa pantalla es
distinta de ``/Actividad/EducandoDatos/Ver/{token}``, que no incluye nota.

El cliente reutiliza una sola sesión autenticada, renueva el login cada
``SIGA_RELOGIN_CADA`` peticiones y busca en los ciclos configurados en
``SIGA_CICLOS``.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

import requests

from ..config import Settings
from ..credentials import CredentialsProvider
from ..errors import AuthenticationError, PlatformError
from ..http_session import WebSession
from ..models import AcademicRecord, EstadoConsulta, Fuente, PlatformResult
from ..normalization import normalizar_nivel, parsear_calificacion
from .base import PlatformClient, _mask


_TOKEN_LOGIN = re.compile(
    r"<input[^>]*__RequestVerificationToken[^>]*>", re.I
)
_VALOR_INPUT = re.compile(r"value=\"([^\"]*)\"", re.I)
_TOKEN_SELECCIONAR = re.compile(r"/Seleccionar/([^\"'?>\s]+)", re.I)
_NIVEL = re.compile(r"Nivel:\s*([^<]+)", re.I)
# La nota está en el <h1> del jumbotron; el otro <h1> de la página es el
# estatus ("Proceso finalizado. Certificado entregado.").
_CALIFICACION = re.compile(
    r"<div[^>]*class=\"[^\"]*jumbotron[^\"]*\"[^>]*>"
    r".*?<h1[^>]*>\s*([^<]*?)\s*</h1>",
    re.I | re.S,
)
_ESTATUS = re.compile(r"<div[^>]*id=\"AreaTrabajo\"[^>]*>\s*<h1[^>]*>(.*?)</h1>", re.I | re.S)
_ESTATUS_ALERTA = re.compile(r"<div[^>]*class=\"[^\"]*alert[^\"]*\"[^>]*>(.*?)</div>", re.I | re.S)
_ANTIGUEDAD = re.compile(r"Antig[^<:]*:\s*([^<]+)", re.I)
_ALIADOS = re.compile(r"Aliados:\s*(?:</strong>)?\s*(?:<[^>]+>)*\s*([^<]+)", re.I)


_CURP = re.compile(r"CURP\s*:?\s*([A-Z0-9]{6,30})", re.I)


def _extraer_curp(html: str) -> str:
    """Extrae la CURP mostrada en el detalle de SIGA.

    Se tolera que SIGA devuelva como CURP el propio RFE (dato mal
    capturado); por eso se aceptan longitudes de 6 a 30 caracteres.
    """
    texto = _limpiar(html)
    coincidencia = _CURP.search(texto)
    return coincidencia.group(1).upper() if coincidencia else ""


def _limpiar(valor: str | None) -> str:
    if not valor:
        return ""
    texto = re.sub(r"&nbsp;", " ", valor)
    texto = re.sub(r"<[^>]+>", " ", texto)
    return " ".join(texto.replace("\xa0", " ").split()).strip(" :;")


def _campo_tabla(html: str, etiqueta: str) -> str:
    """Valor de un bloque ``<strong>Etiqueta</strong> ... <div>valor</div>``."""

    patron = re.compile(
        r"<strong>\s*" + re.escape(etiqueta) + r"\s*</strong>"
        r".*?class=\"[^\"]*well-msjText[^\"]*\"[^>]*>(.*?)</div>",
        re.I | re.S,
    )
    coincidencia = patron.search(html)
    return _limpiar(coincidencia.group(1)) if coincidencia else ""


def _tiene_formulario_login(html: str) -> bool:
    return "id=\"UserName\"" in html or "name=\"UserName\"" in html


class SigaClient(PlatformClient):
    fuente = Fuente.SIGA

    def __init__(
        self,
        settings: Settings,
        session: WebSession,
        credentials: CredentialsProvider | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(settings, session, credentials, logger)
        self._logueado = False
        self._peticiones = 0

    # ------------------------------------------------------------------
    # Configuración y sesión
    # ------------------------------------------------------------------
    @property
    def configurado(self) -> bool:
        if not self.settings.siga_url:
            return False
        cred = self._credenciales()
        return bool(cred.usuario and cred.password)

    @property
    def _base(self) -> str:
        return self.settings.siga_url.rstrip("/")

    def _login(self) -> None:
        cred = self._credenciales()
        if not (cred.usuario and cred.password):
            raise AuthenticationError(
                "SIGA requiere SIGA_USER y SIGA_PASSWORD."
            )

        respuesta = self.session.get(f"{self._base}/Account/Login")
        self._validar_respuesta(respuesta)

        etiqueta = _TOKEN_LOGIN.search(respuesta.text)
        valor = _VALOR_INPUT.search(etiqueta.group(0)) if etiqueta else None
        if not valor:
            raise AuthenticationError(
                "SIGA no devolvió el token antiforgery del login."
            )

        respuesta = self.session.post(
            f"{self._base}/Account/Login",
            data={
                "__RequestVerificationToken": valor.group(1),
                "UserName": cred.usuario,
                "Password": cred.password,
                "RememberMe": "false",
            },
            allow_redirects=True,
        )
        if _tiene_formulario_login(respuesta.text):
            raise AuthenticationError("SIGA rechazó las credenciales.")

        self._logueado = True
        self._peticiones = 0
        self.log.debug("SIGA: sesión iniciada")

    def _asegurar_sesion(self) -> None:
        limite = max(1, self.settings.siga_relogin_cada)
        if not self._logueado or self._peticiones >= limite:
            self._login()

    def _get(self, url: str, **kwargs) -> requests.Response:
        self._asegurar_sesion()
        respuesta = self.session.get(url, **kwargs)
        self._peticiones += 1

        if _tiene_formulario_login(respuesta.text):
            self._login()
            respuesta = self.session.get(url, **kwargs)
            self._peticiones += 1
        return respuesta

    # ------------------------------------------------------------------
    # Búsqueda y detalle
    # ------------------------------------------------------------------
    def _buscar(self, rfe: str, ciclo: str) -> str | None:
        """Devuelve el token de ``Seleccionar`` o ``None`` si no aparece."""

        respuesta = self._get(
            f"{self._base}/Actividad/Participantes",
            params={
                "RFE": rfe,
                "Nombre": "",
                "Apellido_Paterno": "",
                "Apellido_Materno": "",
                "ciclo": str(ciclo),
            },
        )
        if not self._validar_respuesta(respuesta):
            return None

        buscado = rfe.strip().upper()
        for fila in re.findall(r"<tr[^>]*>(.*?)</tr>", respuesta.text, re.I | re.S):
            celdas = [
                _limpiar(celda)
                for celda in re.findall(
                    r"<td[^>]*>(.*?)</td>", fila, re.I | re.S
                )
            ]
            if not any(celda.upper() == buscado for celda in celdas[:4]):
                continue
            coincidencia = _TOKEN_SELECCIONAR.search(fila)
            if coincidencia:
                return coincidencia.group(1)
        return None

    def _detalle(self, token: str, ciclo: str) -> str:
        respuesta = self._get(
            f"{self._base}/Actividad/Participantes/Seleccionar/{quote(token, safe='')}",
            params={"ciclo": str(ciclo)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        if not self._validar_respuesta(respuesta):
            raise PlatformError(
                f"SIGA no devolvió el detalle del participante (HTTP "
                f"{respuesta.status_code})."
            )
        return respuesta.text

    def _interpretar_detalle(
        self, html: str, ciclo: str, rfe: str | None = None
    ) -> AcademicRecord | None:
        nivel_raw = ""
        coincidencia_nivel = _NIVEL.search(html)
        if coincidencia_nivel:
            nivel_raw = _limpiar(coincidencia_nivel.group(1))

        calificacion_raw = ""
        coincidencia_nota = _CALIFICACION.search(html)
        if coincidencia_nota:
            calificacion_raw = _limpiar(coincidencia_nota.group(1))

        nivel = normalizar_nivel(nivel_raw)
        calificacion = parsear_calificacion(calificacion_raw)

        curp = _extraer_curp(html)

        # Un detalle legítimo siempre trae la etiqueta "Nivel" (aunque sea
        # "Sin asignar") o una calificación. Si no hay ninguna, la respuesta
        # no es la pantalla esperada. Excepción: cuando SIGA devuelve como
        # CURP el mismo RFE (captura incorrecta), se conserva el registro
        # para que la regla de negocio lo clasifique como BASURA.
        curp_igual_rfe = bool(
            rfe and curp and curp.strip().upper() == rfe.strip().upper()
        )
        if not coincidencia_nivel and calificacion is None and not curp_igual_rfe:
            return None

        estatus = ""
        coincidencia_estatus = _ESTATUS.search(html) or _ESTATUS_ALERTA.search(html)
        if coincidencia_estatus:
            estatus = _limpiar(coincidencia_estatus.group(1))

        antiguedad = ""
        coincidencia_antiguedad = _ANTIGUEDAD.search(html)
        if coincidencia_antiguedad:
            antiguedad = _limpiar(coincidencia_antiguedad.group(1))

        aliados = ""
        coincidencia_aliados = _ALIADOS.search(html)
        if coincidencia_aliados:
            aliados = _limpiar(coincidencia_aliados.group(1))

        detalles = {
            nombre: valor
            for nombre, valor in (
                ("ciclo_escolar", str(ciclo)),
                ("estatus", estatus),
                ("antiguedad", antiguedad),
                ("aliados", aliados),
                ("coordinacion_zona", _campo_tabla(html, "Coordinación Zona")),
                ("curp", curp),
                ("alta_por", _campo_tabla(html, "Alta por")),
                ("fecha_alta", _campo_tabla(html, "Fecha de alta")),
            )
            if valor
        }

        return AcademicRecord(
            fuente=self.fuente,
            nivel=nivel,
            calificacion=calificacion,
            nivel_raw=nivel_raw,
            calificacion_raw=calificacion_raw,
            detalles=detalles,
        )

    # ------------------------------------------------------------------
    # Contrato de PlatformClient
    # ------------------------------------------------------------------
    def _obtener(self, rfe: str) -> str | None:
        """Primer detalle encontrado (útil para diagnóstico y pruebas)."""

        for ciclo in self.settings.siga_ciclos:
            token = self._buscar(rfe, ciclo)
            if token:
                return self._detalle(token, ciclo)
        return None

    def _interpretar(self, contenido: str) -> PlatformResult:
        registro = self._interpretar_detalle(contenido, "")
        if registro is None:
            return PlatformResult(
                fuente=self.fuente, estado=EstadoConsulta.NO_ENCONTRADO
            )
        return PlatformResult(
            fuente=self.fuente,
            estado=EstadoConsulta.OK,
            registros=[registro],
        )

    def consultar(self, rfe: str) -> PlatformResult:
        """Consulta el RFE en todos los ciclos configurados."""

        if not self.configurado:
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.NO_CONFIGURADO,
                error=(
                    "SIGA no tiene URL o credenciales configuradas "
                    "(SIGA_URL, SIGA_USER, SIGA_PASSWORD)."
                ),
            )

        try:
            registros: list[AcademicRecord] = []
            for ciclo in self.settings.siga_ciclos:
                token = self._buscar(rfe, ciclo)
                if not token:
                    continue
                html = self._detalle(token, ciclo)
                registro = self._interpretar_detalle(html, ciclo, rfe)
                if registro is not None:
                    registros.append(registro)

            if not registros:
                return PlatformResult(
                    fuente=self.fuente,
                    estado=EstadoConsulta.NO_ENCONTRADO,
                )

            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.OK,
                registros=registros,
            )

        except AuthenticationError as exc:
            self.log.warning(
                "SIGA: autenticación fallida para %s: %s", _mask(rfe), exc
            )
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.ERROR,
                error=f"AuthenticationError: {exc}",
            )
        except PlatformError as exc:
            self.log.warning(
                "SIGA: error de plataforma para %s: %s", _mask(rfe), exc
            )
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.ERROR,
                error=f"PlatformError: {exc}",
            )
        except requests.RequestException as exc:
            self.log.warning(
                "SIGA: error de red para %s: %s", _mask(rfe), type(exc).__name__
            )
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.ERROR,
                error=f"RequestException: {type(exc).__name__}",
            )
        except Exception as exc:  # defensa: una fila no debe tumbar el lote
            self.log.exception(
                "SIGA: error inesperado para %s", _mask(rfe)
            )
            return PlatformResult(
                fuente=self.fuente,
                estado=EstadoConsulta.ERROR,
                error=f"{type(exc).__name__}: {exc}",
            )
