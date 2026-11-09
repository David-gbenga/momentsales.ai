"""Report endpoints (FR-4, ADR-0021): the Top 10, CSV and PDF exports, share links and the
public read-only view."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from momentsales.core.errors import ProblemError
from momentsales.core.ratelimit import limit
from momentsales.db.research import Report, ReportItem, ReportShare
from momentsales.deps import AppSettings
from momentsales.modules.exports.service import PdfUnavailable, render_pdf
from momentsales.modules.reports import render, service
from momentsales.tenancy.deps import DbSession, WorkspaceContext, require
from momentsales.tenancy.permissions import Action

router = APIRouter(tags=["reports"])


class ReportItemOut(BaseModel):
    rank: int
    keyword: str
    category: str | None
    score: int
    score_breakdown: dict[str, Any]
    why: str
    why_source: str
    image_url: str | None
    best_offer: dict[str, Any] | None
    offers: list[dict[str, Any]]
    sizes: list[str]
    trend: dict[str, Any] | None
    market: dict[str, Any] | None
    supplier: dict[str, Any] | None
    profit: dict[str, Any] | None
    compliance: dict[str, Any]
    flags: list[str]
    fetched_at: datetime | None


class ReportOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    generated_at: datetime
    summary: dict[str, Any]
    excluded: list[dict[str, Any]]
    items: list[ReportItemOut]


class PublicReportOut(BaseModel):
    generated_at: datetime
    summary: dict[str, Any]
    excluded: list[dict[str, Any]]
    items: list[ReportItemOut]


class ReportListItem(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    generated_at: datetime
    item_count: int
    top: str | None
    mock: bool


class ShareIn(BaseModel):
    days: int | None = Field(default=None, ge=1, le=365)


class ShareOut(BaseModel):
    id: uuid.UUID
    expires_at: datetime
    revoked_at: datetime | None
    last_viewed_at: datetime | None
    created_at: datetime
    active: bool


class ShareCreatedOut(ShareOut):
    url: str  # shown once: only the token's hash is stored


def _item(i: ReportItem) -> ReportItemOut:
    return ReportItemOut.model_validate(i, from_attributes=True)


def _share(share: ReportShare) -> ShareOut:
    now = datetime.now(share.expires_at.tzinfo)
    return ShareOut(
        id=share.id,
        expires_at=share.expires_at,
        revoked_at=share.revoked_at,
        last_viewed_at=share.last_viewed_at,
        created_at=share.created_at,
        active=share.revoked_at is None and share.expires_at > now,
    )


async def _full(session: Any, report: Report) -> ReportOut:
    items = await service.items_of(session, report.id)
    return ReportOut(
        id=report.id,
        run_id=report.run_id,
        generated_at=report.generated_at,
        summary=report.summary,
        excluded=report.excluded,
        items=[_item(i) for i in items],
    )


ReadCtx = Annotated[WorkspaceContext, Depends(require(Action.REPORTS_READ))]
ExportCtx = Annotated[WorkspaceContext, Depends(require(Action.REPORTS_EXPORT))]
ShareCtx = Annotated[WorkspaceContext, Depends(require(Action.REPORTS_SHARE))]


@router.get("/reports", response_model=list[ReportListItem])
async def list_reports(session: DbSession, ctx: ReadCtx) -> list[ReportListItem]:
    return [
        ReportListItem(
            id=r.id,
            run_id=r.run_id,
            generated_at=r.generated_at,
            item_count=n,
            top=top,
            mock=bool(r.summary.get("mock")),
        )
        for r, n, top in await service.list_reports(session)
    ]


@router.get("/reports/{report_id}", response_model=ReportOut)
async def get_report(report_id: uuid.UUID, session: DbSession, ctx: ReadCtx) -> ReportOut:
    return await _full(session, await service.get_report(session, report_id))


@router.get("/reports/{report_id}/export.csv", response_class=Response)
async def export_csv(report_id: uuid.UUID, session: DbSession, ctx: ExportCtx) -> Response:
    report = await service.get_report(session, report_id)
    body = render.to_csv(await service.items_of(session, report.id))
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="top10-{report.id}.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/reports/{report_id}/export.pdf", response_class=Response)
async def export_pdf(report_id: uuid.UUID, session: DbSession, ctx: ExportCtx) -> Response:
    report = await service.get_report(session, report_id)
    html = render.to_html(report, await service.items_of(session, report.id))
    try:
        pdf = await anyio.to_thread.run_sync(render_pdf, html)
    except PdfUnavailable as exc:
        raise ProblemError(
            503, "pdf_unavailable", "PDF export is not available", "Download the CSV instead."
        ) from exc
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="top10-{report.id}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/reports/{report_id}/shares", response_model=list[ShareOut])
async def list_shares(report_id: uuid.UUID, session: DbSession, ctx: ShareCtx) -> list[ShareOut]:
    await service.get_report(session, report_id)
    return [_share(s) for s in await service.list_shares(session, report_id)]


@router.post("/reports/{report_id}/shares", status_code=201, response_model=ShareCreatedOut)
async def create_share(
    report_id: uuid.UUID,
    body: ShareIn,
    session: DbSession,
    settings: AppSettings,
    ctx: ShareCtx,
) -> ShareCreatedOut:
    report = await service.get_report(session, report_id)
    share, token = await service.create_share(
        session, settings, report=report, days=body.days, actor_user_id=ctx.user_id
    )
    url = f"{settings.web_base_url.rstrip('/')}/r/{token}"
    return ShareCreatedOut(**_share(share).model_dump(), url=url)


@router.delete("/reports/{report_id}/shares/{share_id}", status_code=204)
async def revoke_share(
    report_id: uuid.UUID, share_id: uuid.UUID, session: DbSession, ctx: ShareCtx
) -> Response:
    await service.revoke_share(
        session, report_id=report_id, share_id=share_id, actor_user_id=ctx.user_id
    )
    return Response(status_code=204)


@router.get(
    "/public/reports/{token}",
    response_model=PublicReportOut,
    dependencies=[Depends(limit("public_report", 60, 60))],
)
async def public_report(token: str, session: DbSession, response: Response) -> PublicReportOut:
    """A shared report, read-only, without run cost or workspace details."""
    report, items = await service.open_shared(session, token)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return PublicReportOut(
        generated_at=report.generated_at,
        summary=service.public_summary(report.summary),
        excluded=report.excluded,
        items=[_item(i) for i in items],
    )
