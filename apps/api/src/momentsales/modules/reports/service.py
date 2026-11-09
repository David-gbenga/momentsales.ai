"""Reports (FR-4, ADR-0021): reading the Top 10, share links and the public view.

Share links are opaque tokens stored only as SHA-256. The public view finds the link by hash
in platform context, then reads the report in that workspace's own RLS context. It leaves
out the run cost and anything about the workspace.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.config import Settings
from momentsales.core.db import set_tenant_context
from momentsales.core.errors import ProblemError
from momentsales.core.security import hash_token, new_opaque_token
from momentsales.db.research import Report, ReportItem, ReportShare
from momentsales.modules.audit import service as audit

PUBLIC_SUMMARY_KEYS = ("as_of", "season", "method", "sources", "mock", "item_count")


def not_found() -> ProblemError:
    return ProblemError(404, "not_found", "Report not found")


async def get_report(session: AsyncSession, report_id: uuid.UUID) -> Report:
    report = await session.get(Report, report_id)
    if report is None:
        raise not_found()
    return report


async def items_of(session: AsyncSession, report_id: uuid.UUID) -> list[ReportItem]:
    return list(
        await session.scalars(
            select(ReportItem).where(ReportItem.report_id == report_id).order_by(ReportItem.rank)
        )
    )


async def list_reports(
    session: AsyncSession, limit: int = 50
) -> list[tuple[Report, int, str | None]]:
    counts = (
        select(ReportItem.report_id, func.count().label("n"))
        .group_by(ReportItem.report_id)
        .subquery()
    )
    top = select(ReportItem.report_id, ReportItem.keyword).where(ReportItem.rank == 1).subquery()
    rows = await session.execute(
        select(Report, counts.c.n, top.c.keyword)
        .outerjoin(counts, counts.c.report_id == Report.id)
        .outerjoin(top, top.c.report_id == Report.id)
        .order_by(Report.generated_at.desc())
        .limit(limit)
    )
    return [(r, int(n or 0), kw) for r, n, kw in rows.all()]


def public_summary(summary: dict[str, Any]) -> dict[str, Any]:
    out = {k: summary.get(k) for k in PUBLIC_SUMMARY_KEYS}
    params = dict(summary.get("params") or {})
    params.pop("audience", None)
    out["params"] = params
    return out


async def create_share(
    session: AsyncSession,
    settings: Settings,
    *,
    report: Report,
    days: int | None,
    actor_user_id: uuid.UUID,
) -> tuple[ReportShare, str]:
    days = days or settings.report_share_default_days
    if not 1 <= days <= settings.report_share_max_days:
        raise ProblemError(
            422,
            "invalid_share_days",
            "Invalid expiry",
            f"Choose between 1 and {settings.report_share_max_days} days.",
        )
    token = new_opaque_token()
    share = ReportShare(
        workspace_id=report.workspace_id,
        report_id=report.id,
        token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(days=days),
        created_by=actor_user_id,
    )
    session.add(share)
    await session.flush()
    await audit.record(
        session,
        action="report.share_created",
        object_type="report_share",
        object_id=share.id,
        workspace_id=report.workspace_id,
        actor_user_id=actor_user_id,
        after={"report_id": str(report.id), "expires_at": share.expires_at.isoformat()},
    )
    return share, token


async def list_shares(session: AsyncSession, report_id: uuid.UUID) -> list[ReportShare]:
    return list(
        await session.scalars(
            select(ReportShare)
            .where(ReportShare.report_id == report_id)
            .order_by(ReportShare.created_at.desc())
        )
    )


async def revoke_share(
    session: AsyncSession,
    *,
    report_id: uuid.UUID,
    share_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> None:
    share = await session.get(ReportShare, share_id)
    if share is None or share.report_id != report_id:
        raise ProblemError(404, "not_found", "Share link not found")
    if share.revoked_at is None:
        share.revoked_at = datetime.now(UTC)
        await session.flush()
        await audit.record(
            session,
            action="report.share_revoked",
            object_type="report_share",
            object_id=share.id,
            workspace_id=share.workspace_id,
            actor_user_id=actor_user_id,
        )


async def open_shared(session: AsyncSession, token: str) -> tuple[Report, list[ReportItem]]:
    """The report behind a valid link. Any problem looks the same: 404."""
    if not token or len(token) > 200:
        raise not_found()
    await set_tenant_context(session, platform=True)
    share = await session.scalar(
        select(ReportShare).where(ReportShare.token_hash == hash_token(token))
    )
    now = datetime.now(UTC)
    if share is None or share.revoked_at is not None or share.expires_at <= now:
        raise not_found()
    await set_tenant_context(session, workspace_id=share.workspace_id)
    report = await session.get(Report, share.report_id)
    if report is None:
        raise not_found()
    share.last_viewed_at = now
    return report, await items_of(session, report.id)
