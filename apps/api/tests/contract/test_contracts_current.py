"""Committed contracts must match the code (OpenAPI spec, permission matrix, schema)."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from momentsales.core.config import get_settings
from momentsales.db import models  # noqa: F401
from momentsales.db.base import Base
from momentsales.tools.export_contracts import build


def test_committed_contracts_are_current() -> None:
    for path, content in build().items():
        assert path.exists(), f"{path} missing: run python -m momentsales.tools.export_contracts"
        assert path.read_text(encoding="utf-8") == content, (
            f"{path.name} is stale: run python -m momentsales.tools.export_contracts"
        )


def test_openapi_uses_problem_errors_and_versioned_paths() -> None:
    import json

    spec = json.loads(next(iter(build().values())))
    assert all(p.startswith("/api/v1/") for p in spec["paths"])
    assert not any(p.startswith("/api/v1/dev/") for p in spec["paths"])


def test_models_match_migrations() -> None:
    engine = create_engine(get_settings().migration_database_url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], f"models and migrations differ: {diff}"
