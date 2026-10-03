# ---------------------------------------------------------------------------
# DisciplineOS API image.  Build context = ./api
#   docker build -f docker/api.Dockerfile -t dsos-api ./api
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# libpq is needed by psycopg; curl for the healthcheck/debugging.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates libpq5 tini \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY . .

# Non-root runtime user (owns nothing but the app; no writes to disk needed)
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /srv
USER appuser

EXPOSE 8000

# ---------------------------------------------------------------------------
# dev: bind-mounted source, reload, used by docker-compose.dev.yml
# ---------------------------------------------------------------------------
FROM base AS dev
USER root
RUN pip install watchfiles
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
# prod: migrations run on start (idempotent), then uvicorn
# ---------------------------------------------------------------------------
FROM base AS prod
ENTRYPOINT ["/usr/bin/tini", "--", "/srv/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "2"]
