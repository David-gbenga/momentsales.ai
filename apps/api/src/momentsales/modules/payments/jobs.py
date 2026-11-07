"""Webhook processing job and the sweepers that re-queue stuck work (ADR-0011)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.jobs import enqueue_now, job
from momentsales.core.logging import get_logger
from momentsales.core.runtime import runtime
from momentsales.db.ledger import OcrStatus, Receipt, WebhookEvent

log = get_logger(__name__)

PROCESS_JOB = "payments.process_webhook"
MAX_ATTEMPTS = 10


@job(PROCESS_JOB)
async def process_webhook(payload: dict[str, Any]) -> None:
    from momentsales.modules.payments import service

    event_id = str(payload["event_id"])
    async with runtime() as rt:
        try:
            async with get_sessionmaker()() as session, session.begin():
                payments = await rt.registry.payments(session)
                outcome = await service.process_event(session, payments, event_id)
            log.info("stripe_event_processed", event_id=event_id, outcome=outcome)
        except Exception as exc:
            # The event stays unprocessed; record why, and let the sweeper retry it.
            async with get_sessionmaker()() as session, session.begin():
                await set_tenant_context(session, platform=True)
                row = await session.scalar(
                    select(WebhookEvent).where(WebhookEvent.event_id == event_id)
                )
                if row is not None:
                    row.attempts += 1
                    row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            log.error("stripe_event_failed", event_id=event_id, error=str(exc))
            raise


async def sweep() -> dict[str, int]:
    """Re-queue webhook events and receipt OCR that should have finished by now.
    Run by Celery Beat every minute; the jobs themselves are idempotent."""
    from momentsales.modules.receipts.jobs import OCR_JOB

    cutoff = datetime.now(UTC) - timedelta(minutes=1)
    async with runtime() as rt, get_sessionmaker()() as session, session.begin():
        await set_tenant_context(session, platform=True)
        events = (
            await session.scalars(
                select(WebhookEvent.event_id)
                .where(
                    WebhookEvent.processed_at.is_(None),
                    WebhookEvent.received_at < cutoff,
                    WebhookEvent.attempts < MAX_ATTEMPTS,
                )
                .limit(200)
            )
        ).all()
        receipts = (
            await session.execute(
                select(Receipt.workspace_id, Receipt.id)
                .where(Receipt.ocr_status == OcrStatus.PENDING, Receipt.created_at < cutoff)
                .limit(200)
            )
        ).all()
    for event_id in events:
        enqueue_now(rt.settings, PROCESS_JOB, {"event_id": event_id})
    for workspace_id, receipt_id in receipts:
        enqueue_now(
            rt.settings, OCR_JOB, {"workspace_id": str(workspace_id), "receipt_id": str(receipt_id)}
        )
    return {"webhooks": len(events), "receipts": len(receipts)}
