FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
# WeasyPrint (audit-pack PDF) needs Pango and HarfBuzz; DejaVu gives it a font.
# Apply Debian security updates first: the base image can lag behind fixed CVEs (Trivy gate).
# HEIC receipts: libheif's decoder tool with only the LGPL libde265 plugin (ADR-0014). The GPL
# x265 encoder plugin must never be installed; the build fails if it is.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core libheif-examples libheif-plugin-libde265 \
 && if dpkg -s libheif-plugin-x265 >/dev/null 2>&1; then echo 'GPL x265 plugin must not be installed' >&2; exit 1; fi \
 && "$(command -v heif-dec || command -v heif-convert)" --list-decoders | grep -q libde265 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace --package momentsales-worker
COPY apps/api apps/api
COPY apps/worker apps/worker
RUN uv sync --frozen --no-dev --package momentsales-worker \
 && useradd --system --uid 10001 app \
 && mkdir -p /data/storage && chown app /data/storage
USER app
CMD ["celery", "-A", "momentsales_worker.celery_app", "worker", "--loglevel=INFO"]
