"""`/export` (FR-7): request an audit pack for a period, then download its files."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from momentsales.core.errors import not_found
from momentsales.core.jobs import schedule
from momentsales.db.ledger import Export, JobStatus
from momentsales.deps import AppSettings, Registry
from momentsales.modules.audit import service as audit
from momentsales.modules.exports.jobs import EXPORT_JOB
from momentsales.tenancy.deps import DbSession, WorkspaceContext, require
from momentsales.tenancy.permissions import Action

router = APIRouter(tags=["exports"])
MAX_PERIOD = timedelta(days=400)


class ExportIn(BaseModel):
    period_start: date
    period_end: date

    @model_validator(mode="after")
    def _check(self) -> ExportIn:
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        if self.period_end - self.period_start > MAX_PERIOD:
            raise ValueError("an audit pack covers at most 400 days")
        return self


class ExportFileOut(BaseModel):
    name: str
    sha256: str
    bytes: int
    url: str | None = None


class ExportOut(BaseModel):
    id: uuid.UUID
    period_start: date
    period_end: date
    status: JobStatus
    audit_chain_ok: bool | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    files: list[ExportFileOut] = Field(default_factory=list)


async def _out(export: Export, registry: Registry | None = None, ttl: int = 300) -> ExportOut:
    files = []
    for f in export.files:
        url = None
        if registry is not None and export.status is JobStatus.COMPLETED:
            url = await registry.storage().signed_url(
                f["key"], workspace_id=export.workspace_id, filename=f["name"], ttl_seconds=ttl
            )
        files.append(ExportFileOut(name=f["name"], sha256=f["sha256"], bytes=f["bytes"], url=url))
    return ExportOut(
        id=export.id,
        period_start=export.period_start,
        period_end=export.period_end,
        status=export.status,
        audit_chain_ok=export.audit_chain_ok,
        error=export.error,
        created_at=export.created_at,
        completed_at=export.completed_at,
        files=files,
    )


@router.post("/exports", status_code=202, response_model=ExportOut)
async def request_export(
    body: ExportIn,
    background: BackgroundTasks,
    session: DbSession,
    settings: AppSettings,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_EXPORT))],
) -> ExportOut:
    export = Export(
        workspace_id=ctx.workspace_id,
        period_start=body.period_start,
        period_end=body.period_end,
        status=JobStatus.PENDING,
        files=[],
        requested_by=ctx.user_id,
    )
    session.add(export)
    await session.flush()
    await audit.record(
        session,
        action="export.requested",
        object_type="export",
        object_id=export.id,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        after={
            "period_start": body.period_start.isoformat(),
            "period_end": body.period_end.isoformat(),
        },
    )
    schedule(
        background,
        settings,
        EXPORT_JOB,
        {"workspace_id": str(ctx.workspace_id), "export_id": str(export.id)},
    )
    await session.refresh(export)
    return await _out(export)


@router.get("/exports", response_model=list[ExportOut])
async def list_exports(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_EXPORT))],
) -> list[ExportOut]:
    rows = (await session.scalars(select(Export).order_by(Export.id.desc()).limit(50))).all()
    return [await _out(e) for e in rows]


@router.get("/exports/{export_id}", response_model=ExportOut)
async def get_export(
    export_id: uuid.UUID,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_EXPORT))],
) -> ExportOut:
    """Status, and 5-minute download links once the pack is ready."""
    export = await session.get(Export, export_id)
    if export is None:
        raise not_found("export")
    return await _out(export, registry, settings.signed_url_ttl_seconds)
