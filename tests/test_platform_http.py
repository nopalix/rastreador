"""Integración de los clientes de plataforma contra un servidor HTTP local.

Estas pruebas ejercitan el transporte real (método, parámetros, cabeceras,
códigos HTTP y parseo) sin salir a Internet. Complementan las pruebas de
parseo puro y las del pipeline con clientes simulados.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from rfe_rastreador.config import Settings
from rfe_rastreador.credentials import PlatformCredentials
from rfe_rastreador.http_session import SessionManager
from rfe_rastreador.models import EstadoConsulta, Fuente, NivelAcademico
from rfe_rastreador.pipeline import Pipeline
from rfe_rastreador.platforms.factory import crear_clientes
from rfe_rastreador.platforms.sasa import SasaClient
from rfe_rastreador.platforms.siga import SigaClient
from rfe_rastreador.platforms.siged import SigedClient
from rfe_rastreador.privacy import PrivacyManager


HTML_SASA = """
<html><body>
<table>
  <tr><td>Etapa</td><td>AVANZADO</td></tr>
  <tr><td><b>Promedio</b> : 8.1</td></tr>
</table>
</body></html>
"""

HTML_SIGA_LOGIN = """
<html><body>
<form action="/siga/Account/Login" method="post">
  <input name="__RequestVerificationToken" type="hidden" value="token-prueba">
  <input id="UserName" name="UserName" type="text">
  <input id="Password" name="Password" type="password">
</form>
</body></html>
"""

HTML_SIGA_BUSQUEDA = """
<html><body><table>
  <tr><th></th><th>RFE</th><th>Nombre</th><th>CURP</th><th>Ocupación</th><th></th></tr>
  <tr>
    <td></td><td>AAGJ7703195R4</td><td>NOMBRE DE PRUEBA</td>
    <td>AAGJ770319HGTXXX00</td><td>OCUPACION</td>
    <td><a href="/siga/Actividad/Participantes/Seleccionar/abc123?ciclo=2017">Ver</a></td>
  </tr>
</table></body></html>
"""

HTML_SIGA_DETALLE = """
<html><body>
<div class="row">
  <div class="col-md-5">
    <div><label>Nivel: Primaria </label></div>
    <hr />
    <div class="jumbotron text-center" style="padding:4px;">
      <h1>7.0</h1>
    </div>
  </div>
</div>
<div id="AreaTrabajo">
  <h1>Proceso finalizado. Certificado entregado.</h1>
  <div class="row">
    <strong>Coordinación Zona</strong>
    <div class="well well-msjText-verdedos">LEON NORTE</div>
  </div>
</div>
<label>Antigüedad: cero años</label>
<strong>Aliados:</strong> <label class="info"> INEA ;</label>
</body></html>
"""

HTML_SIGA_DETALLE_SIN_NOTA = HTML_SIGA_DETALLE.replace("<h1>7.0</h1>", "<h1></h1>")

HTML_SIGA_DETALLE_SIN_NIVEL = """
<html><body>
<div class="row">
  <div class="col-md-5">
    <div><label>Nivel: Sin asignar </label></div>
    <hr />
    <div class="alert alert-warning">Proceso sin concluir</div>
  </div>
</div>
</body></html>
"""

HTML_LOGIN = """
<html><body>
<form><input type="text" name="usuario">
<input type="password" name="contrasena"></form>
</body></html>
"""

HTML_SIN_REGISTRO = "<html><body>NO SE ENCONTRO INFORMACION</body></html>"

JSON_SIGED = json.dumps(
    {
        "students": [
            {
                "id": 123,
                "schoolCyclesDTO": [
                    {
                        "idStudentCycle": 456,
                        "schoolCycle": "2023-2024",
                        "level": "SECUNDARIA",
                        "shift": "MATUTINO",
                        "grade": "3",
                        "group": "A",
                        "avgAcreditation": 8.4,
                    }
                ],
            }
        ]
    }
)


@pytest.fixture()
def servidor():
    """Servidor HTTP local con rutas configurables y registro de peticiones."""

    rutas: dict[str, tuple[int, str]] = {}
    peticiones: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def _responder(self) -> None:
            partes = urlsplit(self.path)
            largo = int(self.headers.get("Content-Length") or 0)
            cuerpo = self.rfile.read(largo).decode("utf-8") if largo else ""
            peticiones.append(
                {
                    "metodo": self.command,
                    "ruta": partes.path,
                    "query": parse_qs(partes.query),
                    "cuerpo": parse_qs(cuerpo),
                    "autorizacion": self.headers.get("Authorization"),
                    "xhr": self.headers.get("X-Requested-With"),
                }
            )
            respuesta = rutas.get((partes.path, self.command))
            if respuesta is None:
                respuesta = rutas.get(partes.path, (404, "No encontrado"))
            estado, html = respuesta
            datos = html.encode("utf-8")
            self.send_response(estado)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)

        do_GET = _responder
        do_POST = _responder

        def log_message(self, *args) -> None:  # silencio en pytest
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    try:
        yield SimpleNamespace(
            base=f"http://127.0.0.1:{httpd.server_port}",
            rutas=rutas,
            peticiones=peticiones,
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        hilo.join(timeout=5)


def _settings(base: str, **overrides) -> Settings:
    valores = dict(
        sasa_url=f"{base}/sasa",
        siga_url=f"{base}/siga",
        siged_url=f"{base}/siged",
        siged_format="html",
        request_timeout=5,
        max_retries=0,
        backoff_factor=0.0,
        delay_min=0.0,
        delay_max=0.0,
        record_delay_min=0.0,
        record_delay_max=0.0,
    )
    valores.update(overrides)
    return Settings(**valores)


def _sesiones(settings: Settings) -> SessionManager:
    return SessionManager(settings)


class CredencialesFijas:
    """Proveedor mínimo para pruebas, sin depender de variables de entorno."""

    def __init__(self, usuario: str = "prueba", password: str = "secreto"):
        self._credenciales = PlatformCredentials(usuario=usuario, password=password)

    def get(self, _fuente):
        return self._credenciales

    def describe(self):
        return {
            "SASA": {
                "usuario": False,
                "password": False,
                "token": False,
                "completo": False,
            },
            "SIGA": {
                "usuario": True,
                "password": True,
                "token": False,
                "completo": True,
            },
            "SIGED": {
                "usuario": False,
                "password": False,
                "token": False,
                "completo": False,
            },
        }


def _rutas_siga(servidor, detalle: str = HTML_SIGA_DETALLE) -> None:
    servidor.rutas[("/siga/Account/Login", "GET")] = (200, HTML_SIGA_LOGIN)
    servidor.rutas[("/siga/Account/Login", "POST")] = (200, "<html>ok</html>")
    servidor.rutas["/siga/Actividad/Participantes"] = (200, HTML_SIGA_BUSQUEDA)
    servidor.rutas["/siga/Actividad/Participantes/Seleccionar/abc123"] = (200, detalle)


def test_sasa_post_envia_edo_y_find(servidor):
    servidor.rutas["/sasa"] = (200, HTML_SASA)
    settings = _settings(servidor.base)
    sesiones = _sesiones(settings)
    try:
        cliente = SasaClient(settings, sesiones.get("sasa"))
        resultado = cliente.consultar("AAGJ7703195R4")
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.OK
    assert len(resultado.registros) == 1
    assert resultado.registros[0].nivel is NivelAcademico.AVANZADO
    assert resultado.registros[0].calificacion == 8.1

    peticion = servidor.peticiones[-1]
    assert peticion["metodo"] == "POST"
    assert peticion["ruta"] == "/sasa"
    assert peticion["cuerpo"] == {
        "edo": ["gto"],
        "find": ["AAGJ7703195R4"],
    }


def test_siga_login_busqueda_y_detalle(servidor):
    _rutas_siga(servidor)
    settings = _settings(servidor.base, siga_ciclos=("2017",))
    sesiones = _sesiones(settings)
    try:
        cliente = SigaClient(settings, sesiones.get("siga"), CredencialesFijas())
        resultado = cliente.consultar("AAGJ7703195R4")
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.OK
    assert len(resultado.registros) == 1
    registro = resultado.registros[0]
    assert registro.nivel is NivelAcademico.PRIMARIA
    assert registro.calificacion == 7.0
    assert registro.nivel_raw == "Primaria"
    assert registro.detalles["ciclo_escolar"] == "2017"
    assert "Proceso finalizado" in registro.detalles["estatus"]
    assert registro.detalles["coordinacion_zona"] == "LEON NORTE"

    peticiones = servidor.peticiones
    assert [p["metodo"] for p in peticiones[:2]] == ["GET", "POST"]
    assert peticiones[1]["cuerpo"]["UserName"] == ["prueba"]
    assert peticiones[1]["cuerpo"]["__RequestVerificationToken"] == ["token-prueba"]

    busqueda = peticiones[2]
    assert busqueda["ruta"] == "/siga/Actividad/Participantes"
    assert busqueda["query"]["RFE"] == ["AAGJ7703195R4"]
    assert busqueda["query"]["ciclo"] == ["2017"]

    detalle = peticiones[3]
    assert detalle["ruta"].endswith("/Seleccionar/abc123")
    assert detalle["query"]["ciclo"] == ["2017"]
    assert detalle["xhr"] == "XMLHttpRequest"


def test_siga_detalle_sin_nota_conserva_nivel(servidor):
    _rutas_siga(servidor, HTML_SIGA_DETALLE_SIN_NOTA)
    settings = _settings(servidor.base, siga_ciclos=("2017",))
    sesiones = _sesiones(settings)
    try:
        cliente = SigaClient(settings, sesiones.get("siga"), CredencialesFijas())
        resultado = cliente.consultar("AAGJ7703195R4")
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.OK
    registro = resultado.registros[0]
    assert registro.nivel is NivelAcademico.PRIMARIA
    assert registro.calificacion is None
    assert registro.calificacion_raw == ""


def test_siga_detalle_sin_nivel_ni_nota_conserva_registro(servidor):
    _rutas_siga(servidor, HTML_SIGA_DETALLE_SIN_NIVEL)
    settings = _settings(servidor.base, siga_ciclos=("2017",))
    sesiones = _sesiones(settings)
    try:
        cliente = SigaClient(settings, sesiones.get("siga"), CredencialesFijas())
        resultado = cliente.consultar("AAGJ7703195R4")
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.OK
    registro = resultado.registros[0]
    assert registro.nivel is NivelAcademico.DESCONOCIDO
    assert registro.calificacion is None
    assert registro.detalles["estatus"] == "Proceso sin concluir"


def test_siga_sin_coincidencia_es_no_encontrado(servidor):
    _rutas_siga(servidor)
    servidor.rutas["/siga/Actividad/Participantes"] = (
        200,
        "<html><table><tr><td></td><td>OTRO</td></tr></table></html>",
    )
    settings = _settings(servidor.base, siga_ciclos=("2017",))
    sesiones = _sesiones(settings)
    try:
        cliente = SigaClient(settings, sesiones.get("siga"), CredencialesFijas())
        resultado = cliente.consultar("AAGJ7703195R4")
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.NO_ENCONTRADO


def test_siged_sin_registro(servidor):
    servidor.rutas["/siged"] = (200, HTML_SIN_REGISTRO)
    settings = _settings(servidor.base)
    sesiones = _sesiones(settings)
    try:
        cliente = SigedClient(settings, sesiones.get("siged"))
        resultado = cliente.consultar("AAGJ7703195R4")
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.NO_ENCONTRADO


def test_siged_json_consulta_por_curp(servidor):
    curp = "AETJ051221HGTSPRA8"
    servidor.rutas[f"/siged/alumno/schoolCyclesByStudent/curp={curp}"] = (
        200,
        JSON_SIGED,
    )

    settings = _settings(servidor.base, siged_format="json")
    sesiones = _sesiones(settings)
    try:
        cliente = SigedClient(settings, sesiones.get("siged"))
        resultado = cliente.consultar(curp)
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.OK
    assert len(resultado.registros) == 1
    registro = resultado.registros[0]
    assert registro.nivel is NivelAcademico.SECUNDARIA
    assert registro.calificacion == 8.4
    assert registro.detalles["ciclo_escolar"] == "2023-2024"
    assert registro.detalles["grado"] == "3"

    peticion = servidor.peticiones[-1]
    assert peticion["metodo"] == "GET"
    assert peticion["ruta"].endswith(f"/curp={curp}")


def test_siged_json_sin_alumnos(servidor):
    curp = "AETJ051221HGTSPRA8"
    servidor.rutas[f"/siged/alumno/schoolCyclesByStudent/curp={curp}"] = (200, "{}")

    settings = _settings(servidor.base, siged_format="json")
    sesiones = _sesiones(settings)
    try:
        cliente = SigedClient(settings, sesiones.get("siged"))
        resultado = cliente.consultar(curp)
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.NO_ENCONTRADO


def test_login_rechazado_se_reporta_sin_excepcion(servidor):
    servidor.rutas["/sasa"] = (200, HTML_LOGIN)
    servidor.rutas[("/siga/Account/Login", "GET")] = (200, HTML_SIGA_LOGIN)
    # El POST vuelve a devolver el formulario: credenciales rechazadas.
    servidor.rutas[("/siga/Account/Login", "POST")] = (200, HTML_SIGA_LOGIN)

    settings = _settings(servidor.base, siga_ciclos=("2017",))
    sesiones = _sesiones(settings)
    try:
        sasa = SasaClient(settings, sesiones.get("sasa"))
        login_sasa = sasa.consultar("AAGJ7703195R4")

        siga = SigaClient(settings, sesiones.get("siga"), CredencialesFijas())
        login_siga = siga.consultar("AAGJ7703195R4")
    finally:
        sesiones.close_all()

    assert login_sasa.estado is EstadoConsulta.ERROR
    assert "AuthenticationError" in (login_sasa.error or "")
    assert login_siga.estado is EstadoConsulta.ERROR
    assert "AuthenticationError" in (login_siga.error or "")


def test_siga_404_en_busqueda_es_no_encontrado(servidor):
    servidor.rutas[("/siga/Account/Login", "GET")] = (200, HTML_SIGA_LOGIN)
    servidor.rutas[("/siga/Account/Login", "POST")] = (200, "<html>ok</html>")
    servidor.rutas["/siga/Actividad/Participantes"] = (404, "No encontrado")

    settings = _settings(servidor.base, siga_ciclos=("2017",))
    sesiones = _sesiones(settings)
    try:
        siga = SigaClient(settings, sesiones.get("siga"), CredencialesFijas())
        resultado = siga.consultar("AAGJ7703195R4")
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.NO_ENCONTRADO


def test_error_http_se_reporta_como_error(servidor):
    servidor.rutas["/sasa"] = (500, "falla")
    settings = _settings(servidor.base)
    sesiones = _sesiones(settings)
    try:
        cliente = SasaClient(settings, sesiones.get("sasa"))
        resultado = cliente.consultar("AAGJ7703195R4")
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.ERROR
    assert "PlatformError" in (resultado.error or "")


def test_pipeline_extremo_a_extremo_con_servidor_local(servidor, tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")

    servidor.rutas["/sasa"] = (200, HTML_SASA)
    _rutas_siga(servidor)
    servidor.rutas["/siged"] = (200, HTML_SIN_REGISTRO)

    ruta = tmp_path / "entrada.xlsx"
    pd.DataFrame({"RFE": ["AAGJ7703195R4"]}).to_excel(ruta, index=False)

    data = tmp_path / "data"
    settings = _settings(
        servidor.base,
        siga_ciclos=("2017",),
        data_dir=data,
        input_dir=data / "input",
        output_dir=data / "output",
        state_dir=data / "state",
        logs_dir=data / "logs",
        consent_file=data / "state" / "consentimiento.json",
        privacy_salt_file=data / "state" / ".privacy_salt",
    )
    settings.ensure_dirs()

    sesiones = _sesiones(settings)
    pipeline = Pipeline(
        settings,
        PrivacyManager(settings),
        sesiones,
        crear_clientes(settings, sesiones, CredencialesFijas()),
    )
    try:
        resultado = pipeline.run(
            ruta, accept_privacy=True, operator="prueba-integracion"
        )
    finally:
        pipeline.close()

    assert resultado.categorias == {"BASURA": 1}
    assert resultado.output_dir is not None
    assert (resultado.output_dir / "BASURA.xlsx").exists()

    rutas = [p["ruta"] for p in servidor.peticiones]
    assert "/sasa" in rutas
    assert any(ruta.startswith("/siga/") for ruta in rutas)
    # BASURA se resuelve sin SIGED: no debe consultarse on-demand.
    assert "/siged" not in rutas


def test_plataforma_sin_url_no_hace_peticiones(servidor):
    settings = _settings(servidor.base, siga_url="")
    sesiones = _sesiones(settings)
    try:
        cliente = SigaClient(settings, sesiones.get("siga"))
        resultado = cliente.consultar("AAGJ7703195R4")
    finally:
        sesiones.close_all()

    assert resultado.estado is EstadoConsulta.NO_CONFIGURADO
    assert resultado.fuente is Fuente.SIGA
    assert servidor.peticiones == []
