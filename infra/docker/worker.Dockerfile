FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
# WeasyPrint (audit-pack PDF) needs Pango and HarfBuzz; DejaVu gives it a font.
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace --package momentsales-worker
COPY apps/api apps/api
COPY apps/worker apps/worker
RUN uv sync --frozen --no-dev --package momentsales-worker \
 && useradd --system --uid 10001 app \n && mkdir -p /data/storage && chown app /data/storage
USER app
CMD ["celery", "-A", "momentsales_worker.celery_app", "worker", "--loglevel=INFO"]
