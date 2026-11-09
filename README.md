# Rastreador RFE — SASA / SIGA / SIGED

Pipeline en Python para procesar un Excel de RFE, consultar plataformas
académicas y clasificar cada registro con reglas de negocio estrictas. Está
pensado para ejecutarse en un contenedor Docker sobre Ubuntu Server, con
persistencia de estado y reanudación ante interrupciones.

## Reglas de negocio

El orden es obligatorio: una regla de prioridad menor nunca puede reclasificar
un registro que ya cayó en una regla superior.

| Prioridad | Categoría | Condición | Efecto |
|---:|---|---|---|
| 1 | `BASURA` | Calificación ≥ 6 y nivel `AVANZADO` o `SECUNDARIA` en SASA, o `SECUNDARIA` en SIGA. | Excluir del contacto. |
| 2 | `CONTINUIDAD` | SIGA `PRIMARIA` aprobada (≥ 6) **y** SASA `INICIAL`/`INTERMEDIO` aprobado (≥ 6). | Equivalencia primaria aprobada. |
| 3 | `NO_ACREDITADO` | `0 < calificación < 6`. Se evalúa la calificación **antes** del nivel, para cubrir SIGA sin nivel asignado. | Requiere seguimiento. |
| 4 | `NO_PRESENTADO` | Nivel asignado y calificación vacía o `0`. | Requiere seguimiento. |

Fallbacks explícitos:

- `REVISION_MANUAL`: hay datos, pero no encajan en ninguna regla (por ejemplo,
  una calificación mayor a 10 o un nivel desconocido).
- `SIN_DATOS`: las plataformas no devolvieron registros.
- `ERROR`: todas las consultas con datos fallaron.

La lógica pura vive en `src/rfe_rastreador/rules.py` y no depende de red, Excel
ni Docker. Así se puede probar con casos sintéticos y auditar el orden exacto de
evaluación.

## Estructura modular

```
rastreador/
├── src/rfe_rastreador/
│   ├── cli.py              # Comandos run / consent / doctor / purge
│   ├── config.py           # Configuración por variables de entorno
│   ├── credentials.py      # Credenciales por plataforma, sin hardcodear
│   ├── http_session.py     # requests.Session + retries + delays aleatorios
│   ├── checkpoint.py       # JSONL append-only + estado atómico + lock
│   ├── privacy.py          # Consentimiento, HMAC, máscaras y retención
│   ├── excel_io.py         # Lectura, validación y salidas por categoría
│   ├── models.py           # Entidades del dominio
│   ├── normalization.py    # Nivel, calificación y RFE
│   ├── rules.py            # Reglas 1-4 y fallbacks
│   ├── pipeline.py         # Orquestador reanudable
│   ├── serialization.py    # JSON de evaluaciones
│   └── platforms/
│       ├── parsing.py      # Parser HTML tolerante (pares y tablas)
│       ├── base.py         # Contrato común
│       ├── sasa.py         # QuickSASA (MEV)
│       ├── siga.py         # SIGA (INEA Figuras: login, búsqueda y detalle)
│       ├── siged.py        # SIGED (API JSON por CURP, con fallback HTML)
│       └── factory.py      # Construcción de clientes
├── tests/                  # Reglas, parser, checkpoints, privacidad y Excel
├── data/                   # Entradas, salidas, estado y logs (volumen)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pyproject.toml
└── .env.example
```

## Inicio rápido con Docker

```bash
cp .env.example .env
# Edita .env y coloca el Excel en data/input/entrada.xls

docker compose build
docker compose run --rm rastreador doctor
docker compose run --rm rastreador run \
  --input /app/data/input/entrada.xls \
  --accept-privacy \
  --operator "tu.nombre"
```

El contenedor corre como usuario no-root (`app`, UID 10001) y el volumen
`./data` conserva salidas y checkpoints entre ejecuciones.

## Uso local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

python -m rfe_rastreador doctor
python -m rfe_rastreador run --input data/input/entrada.xls --accept-privacy
```

Si prefieres no instalar el paquete, ejecuta con `PYTHONPATH=src python -m
rfe_rastreador ...` desde la raíz del proyecto.

Para pruebas rápidas:

```bash
python -m rfe_rastreador run --input data/input/entrada.xls --limit 5 --accept-privacy
python -m rfe_rastreador run --input data/input/entrada.xls --dry-run --accept-privacy
```

## Consentimiento y privacidad

Antes de consultar plataformas se exige un consentimiento versionado. Se puede
otorgar de tres formas:

```bash
# 1. Bandera explícita en la corrida
python -m rfe_rastreador run ... --accept-privacy --operator "responsable"

# 2. Comando dedicado
python -m rfe_rastreador consent --grant --operator "responsable"

# 3. Terminal interactiva (escribir ACEPTO)
```

El consentimiento se guarda en `data/state/consentimiento.json`. Para revocarlo:

```bash
python -m rfe_rastreador consent --revoke --operator "responsable"
```

Medidas implementadas:

- Los checkpoints guardan un HMAC-SHA256 del RFE, no el RFE en claro
  (`CHECKPOINT_HASH_IDENTIFIERS=true`).
- Los logs no imprimen nombre, teléfono ni RFE completo.
- Las salidas y checkpoints se crean con permisos `0600`/`0700`.
- `purge --days N` elimina corridas antiguas sin tocar el consentimiento ni el
  salt de privacidad.

> Los archivos Excel de salida sí contienen los datos originales y la evidencia
> académica, porque son el entregable operativo. Trátalos como información
> confidencial y defínelos fuera del control de versiones.

## Checkpoints y reanudación

Cada archivo de entrada genera un `run_id` con su hash SHA-256. Si el Excel
cambia, se crea un checkpoint nuevo y no se mezclan resultados.

- `data/state/<run_id>/resultados.jsonl`: una línea por RFE procesado.
- `data/state/<run_id>/estado.json`: contadores y metadatos.
- `data/state/<run_id>/.lock`: evita dos procesos simultáneos.

Reanudar es el comportamiento por defecto:

```bash
python -m rfe_rastreador run --input data/input/entrada.xls --accept-privacy
```

Para reprocesar todo desde cero:

```bash
python -m rfe_rastreador run --input data/input/entrada.xls --no-resume --accept-privacy
```

## Salidas

Se genera una carpeta `data/output/<run_id>/` con:

- `BASURA.xlsx`
- `CONTINUIDAD.xlsx`
- `NO_ACREDITADO.xlsx`
- `NO_PRESENTADO.xlsx`
- `REVISION_MANUAL.xlsx`, `SIN_DATOS.xlsx`, `ERROR.xlsx` (si aplican)
- `TODOS.xlsx`
- `RESUMEN.xlsx`
- `INVALIDOS.xlsx` (RFE con longitud/formato no válido)

Cada fila conserva las columnas originales y agrega `Categoria`, `Regla`,
`Motivo`, `Revisar_Manual`, columnas por plataforma y `Evidencia` en JSON.

## Configuración de plataformas

| Variable | Default | Descripción |
|---|---|---|
| `SASA_URL` | `http://168.255.120.98/quickSASA/BuscarRFE` | Endpoint público de QuickSASA. |
| `SASA_ESTADO` | `gto` | Clave de estado para QuickSASA. |
| `SIGA_URL` | vacío | Base de SIGA (Figuras). `.env.example` trae la URL real. Requiere credenciales. |
| `SIGA_CICLOS` | `2016,2017` | Ciclos escolares que se consultan por RFE. |
| `SIGA_RELOGIN_CADA` | `500` | Renueva la sesión tras N peticiones. |
| `SIGED_URL` | vacío | Base de la API JSON. `.env.example` trae la API pública de SIGED. |
| `SIGED_FORMAT` | `json` | `json` para la API real o `html` para una instancia con página. |
| `SIGED_MODE` | `on_demand` | `always`, `on_demand` o `never`. |
| `SIGED_VERIFY_TLS` | `true` | Verificación TLS. Solo desactívala en una red confiable. |
| `DELAY_MIN` / `DELAY_MAX` | `1.0` / `2.5` | Pausa aleatoria antes de cada petición. |
| `MAX_RETRIES` | `3` | Reintentos con backoff exponencial. |
| `REQUEST_TIMEOUT` | `20` | Timeout por petición, en segundos. |

SASA se implementó reutilizando el endpoint y los parámetros del proyecto
QuickSASA existente.

SIGED se integra con su API pública de consulta del alumnado:

```text
GET https://api.siged.sep.gob.mx/CoreServices/servicios
    /alumno/schoolCyclesByStudent/curp=<CURP>
```

Esa API **requiere CURP, no RFE**. El pipeline busca automáticamente una CURP
en la fila: primero la llave principal si ya es CURP, luego una columna llamada
`CURP` y, por último, cualquier valor con estructura de CURP. Si el Excel solo
trae RFE, SIGED se omite y se conserva el resultado de SASA/SIGA.

SIGA se integra con el módulo **Figuras** de INEA y requiere usuario y
contraseña (`SIGA_USER`, `SIGA_PASSWORD`). El cliente:

1. obtiene el token antiforgery de `/Account/Login` e inicia sesión;
2. busca el RFE en `/Actividad/Participantes` para cada ciclo de `SIGA_CICLOS`;
3. abre `/Actividad/Participantes/Seleccionar/{token}?ciclo=YYYY` con
   `X-Requested-With: XMLHttpRequest`;
4. lee el **Nivel** y la **calificación** del bloque `jumbotron` del detalle.

Sin `SIGA_URL` o sin credenciales, SIGA queda `NO_CONFIGURADO` y la regla de
continuidad no podrá confirmarse. La pantalla
`/Actividad/EducandoDatos/Ver/{token}` **no** se usa porque no incluye la nota.

> Antes de usar SIGED de forma masiva, confirma que tu operación está autorizada
> y revisa los términos de uso del servicio. El consentimiento de privacidad del
> pipeline es obligatorio y las consultas se hacen solo para los casos que lo
> necesitan (`on_demand`).

Si al consultar SIGED aparece `SSLCertVerificationError: unable to get local
issuer certificate`, el servidor no está enviando la cadena TLS completa.
Instala el certificado intermedio en el contenedor o, solo dentro de una red
confiable, define `SIGED_VERIFY_TLS=false`.

## Filtro por zona

El Excel puede contener más zonas de las que se desean procesar. El filtro se
aplica **antes** de validar y deduplicar, así que los conteos, checkpoints y
salidas corresponden únicamente a las zonas seleccionadas.

| Variable | Default | Descripción |
|---|---|---|
| `RFE_ZONE_COLUMN` | `CVE ZONA` | Columna que contiene la zona. |
| `RFE_ZONAS` | vacío | Zonas a procesar, separadas por comas. Vacío = todas. |

Ejemplo para consultar solo las zonas 1101 y 1111:

```bash
RFE_ZONAS=1101,1111
```

También se puede pasar por línea de comandos en una corrida puntual:

```bash
python -m rfe_rastreador run --input data/input/ParaConsulta.xlsx \
  --zonas 1101,1111 --limit 5
```

El `run_id` incluye las zonas (por ejemplo `zonas-1101-1111`), de modo que una
corrida filtrada usa un checkpoint y una carpeta de salida distintos de una
corrida sin filtro.

## Permisos de archivos

| Variable | Default | Descripción |
|---|---|---|
| `RFE_DIR_MODE` | `700` | Permisos octales de las carpetas creadas por el proceso. |
| `RFE_FILE_MODE` | `600` | Permisos octales de checkpoints, consentimiento y salidas. |

En Docker con el volumen `./data` compartido con el usuario del host, usa
`RFE_DIR_MODE=2770` (setgid) y `RFE_FILE_MODE=660`. Además, `docker-compose.yml`
ejecuta el contenedor con `${RFE_UID:-10001}:${RFE_GID:-1000}`: el GID debe
coincidir con el grupo del host para que los archivos nuevos nazcan legibles
para ese grupo, incluso en volúmenes que no heredan el bit setgid.

## Pruebas

```bash
pytest
```

Las pruebas cubren:

- prioridad exacta de las cuatro reglas;
- evaluación de calificación antes del nivel en `NO_ACREDITADO`;
- calificación vacía vs. cero en `NO_PRESENTADO`;
- parseo de HTML tipo SASA y tablas multinivel tipo SIGA;
- integración de los tres clientes contra un servidor HTTP local (método,
  parámetros, 404, login y errores 5xx), sin tocar los sitios reales;
- contrato JSON de SIGED y extracción automática de CURP desde la fila;
- deduplicación y separación de RFE inválidos;
- reanudación, línea corrupta y bloqueo de checkpoint;
- consentimiento, HMAC, máscaras y purga por retención.

## Próximos pasos sugeridos

1. Validar el build y la corrida completa con `docker compose` en el Ubuntu
   Server destino.
2. Resolver la cadena TLS de SIGED (el servidor no envía el intermedio) o
   mantenerlo desactivado hasta contar con un bundle de confianza.
3. Definir la política de retención y el responsable del tratamiento antes de
   ejecutar el pipeline con datos reales.
4. Contrastar una muestra de clasificaciones con revisión humana antes de
   generalizar la corrida.
