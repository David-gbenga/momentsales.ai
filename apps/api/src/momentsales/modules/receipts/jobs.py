"""Receipt OCR job (runs in the worker; inline in tests)."""

from __future__ import annotations

import uuid
from typing import Any

from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.jobs import job
from momentsales.core.runtime import runtime
from momentsales.db.ledger import Receipt
from momentsales.modules.receipts import service

OCR_JOB = "receipts.ocr"


@job(OCR_JOB)
async def ocr_receipt(payload: dict[str, Any]) -> None:
    workspace_id = uuid.UUID(payload["workspace_id"])
    receipt_id = uuid.UUID(payload["receipt_id"])
    async with runtime() as rt, get_sessionmaker()() as session, session.begin():
        await set_tenant_context(session, workspace_id=workspace_id)
        receipt = await session.get(Receipt, receipt_id, with_for_update=True)
        if receipt is None:
            return
        ocr = await service.ocr_for(rt.registry, session)
        await service.run_ocr(session, rt.registry.storage(), ocr, receipt)
