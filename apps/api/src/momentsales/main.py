"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from momentsales.core.config import Settings, get_settings
from momentsales.core.db import get_engine
from momentsales.core.errors import install_error_handlers
from momentsales.core.logging import configure_logging, get_logger
from momentsales.core.redis import get_redis
from momentsales.modules import devtools
from momentsales.modules.auth.router import router as auth_router
from momentsales.modules.flags.router import router as flags_router
from momentsales.modules.ledger.router import router as ledger_router
from momentsales.modules.providers.registry import ProviderRegistry
from momentsales.modules.providers.router import router as providers_router
from momentsales.modules.receipts.router import files_router
from momentsales.modules.receipts.router import router as receipts_router
from momentsales.modules.waitlist.router import router as waitlist_router
from momentsales.modules.workspaces.router import router as workspaces_router

API_PREFIX = "/api/v1"
# Every feature router, mounted under API_PREFIX (tests walk this list).
ROUTERS: tuple[APIRouter, ...] = (
    waitlist_router,
    auth_router,
    workspaces_router,
    flags_router,
    providers_router,
    ledger_router,
    receipts_router,
    files_router,
)
log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=not settings.is_local)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        http = httpx.AsyncClient(headers={"User-Agent": "momentsales-api/0.1"})
        app.state.settings = settings
        app.state.redis = get_redis(settings.redis_url)
        app.state.registry = ProviderRegistry(settings, app.state.redis, http)
        log.info("api_started", env=settings.env.value, provider_mode=settings.provider_mode.value)
        try:
            yield
        finally:
            await http.aclose()

    app = FastAPI(
        title="momentsales.ai API",
        version="0.1.0",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs" if settings.is_local else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    install_error_handlers(app)

    api = APIRouter(prefix=API_PREFIX)

    @api.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/ready", tags=["ops"], response_model=None)
    async def ready() -> dict[str, str] | JSONResponse:
        try:
            async with get_engine(settings.database_url).connect() as conn:
                await conn.execute(text("SELECT 1"))
            await get_redis(settings.redis_url).ping()
        except Exception as exc:
            log.warning("not_ready", error=str(exc))
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    for router in ROUTERS:
        api.include_router(router)
    if settings.dev_tools_enabled:
        api.include_router(devtools.router)
    app.include_router(api)
    return app


app = create_app()
