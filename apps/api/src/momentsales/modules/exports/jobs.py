"""Audit-pack job (runs in the worker; inline in tests)."""

from __future__ import annotations

import uuid
from typing import Any

from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.jobs import job
from momentsales.core.logging import get_logger
from momentsales.core.runtime import runtime
from momentsales.db.ledger import Export, JobStatus
from momentsales.modules.exports import service

log = get_logger(__name__)
EXPORT_JOB = "exports.build"


@job(EXPORT_JOB)
async def build_export(payload: dict[str, Any]) -> None:
    workspace_id = uuid.UUID(payload["workspace_id"])
    export_id = uuid.UUID(payload["export_id"])
    async with runtime() as rt:
        try:
            async with get_sessionmaker()() as session, session.begin():
                await set_tenant_context(session, workspace_id=workspace_id)
                export = await session.get(Export, export_id, with_for_update=True)
                if export is None:
                    return
                await service.build(session, rt.registry.storage(), export)
        except Exception as exc:
            async with get_sessionmaker()() as session, session.begin():
                await set_tenant_context(session, workspace_id=workspace_id)
                export = await session.get(Export, export_id)
                if export is not None and export.status is not JobStatus.COMPLETED:
                    export.status = JobStatus.FAILED
                    export.error = "The audit pack couldn't be built. Try again."
            log.error("export_failed", export_id=str(export_id), error=str(exc))
            if not isinstance(exc, service.PdfUnavailable):
                raise
