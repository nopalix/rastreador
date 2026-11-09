FROM python:3.12-slim

LABEL maintainer="Equipo RFE"
LABEL description="Rastreador RFE SASA/SIGA/SIGED con reglas de negocio y checkpoints"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    TZ=America/Mexico_City

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data/input /app/data/output /app/data/state /app/data/logs \
    && chown -R app:app /app \
    && chmod 700 /app/data /app/data/input /app/data/output /app/data/state /app/data/logs

USER app

ENTRYPOINT ["python", "-m", "rfe_rastreador"]
CMD ["doctor"]
