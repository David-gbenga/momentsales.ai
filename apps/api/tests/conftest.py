"""Test setup: a dedicated Postgres database and Redis DB, migrated fresh per session.

Needs `docker compose up -d postgres redis` locally (CI uses service containers).
Environment is set before the app is imported so every cached setting uses test values.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

_DEFAULT_DB = "postgresql+psycopg://app_rw:change-me-rw@localhost:5432/momentsales_test"
_DEFAULT_MIG = "postgresql+psycopg://app_owner:change-me-owner@localhost:5432/momentsales_test"


def _load_dotenv_value(key: str) -> str | None:
    env_file = Path(__file__).resolve().parents[3] / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


TEST_DB = (
    os.environ.get("TEST_DATABASE_URL") or _load_dotenv_value("TEST_DATABASE_URL") or _DEFAULT_DB
)
TEST_MIG = (
    os.environ.get("TEST_MIGRATION_DATABASE_URL")
    or _load_dotenv_value("TEST_MIGRATION_DATABASE_URL")
    or _DEFAULT_MIG
)
TEST_REDIS = (
    os.environ.get("TEST_REDIS_URL")
    or _load_dotenv_value("TEST_REDIS_URL")
    or "redis://localhost:6379/15"
)

os.environ.update(
    {
        "ENV": "test",
        "DATABASE_URL": TEST_DB,
        "MIGRATION_DATABASE_URL": TEST_MIG,
        "REDIS_URL": TEST_REDIS,
        "PROVIDER_MODE": "mock",
        "RATE_LIMIT_ENABLED": "false",
        "COOKIE_SECURE": "false",
        "WEB_BASE_URL": "http://test",
        "LOG_LEVEL": "warning",
        "JOBS_MODE": "inline",
        "STORAGE_LOCAL_PATH": tempfile.mkdtemp(prefix="momentsales-test-storage-"),
    }
)

import httpx  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from momentsales.core.config import get_settings  # noqa: E402
from momentsales.core.redis import get_redis  # noqa: E402
from momentsales.main import create_app  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    return cfg


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> Iterator[None]:
    os.environ["ALEMBIC_DATABASE_URL"] = TEST_MIG
    cfg = _alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(scope="session")
async def app(migrated_db: None) -> AsyncIterator[object]:
    get_settings.cache_clear()
    application = create_app(get_settings())
    await get_redis(TEST_REDIS).flushdb()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    # psycopg async cannot use Windows' default Proactor loop.
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined,no-any-return]
    return asyncio.DefaultEventLoopPolicy()
