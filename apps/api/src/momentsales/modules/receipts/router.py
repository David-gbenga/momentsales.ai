"""Receipt endpoints and the signed download route for local storage."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select

from momentsales.adapters.storage.base import StorageError, key_belongs_to
from momentsales.core.errors import ProblemError
from momentsales.core.jobs import schedule
from momentsales.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, page, paginate
from momentsales.core.security import unsign
from momentsales.db.ledger import OcrStatus, Receipt, SpendReceipt, SpendSource
from momentsales.deps import AppSettings, Registry
from momentsales.modules.ledger import service as ledger
from momentsales.modules.ledger.idempotency import run_idempotent
from momentsales.modules.ledger.schemas import SpendCreate
from momentsales.modules.receipts import service
from momentsales.modules.receipts.jobs import OCR_JOB
from momentsales.tenancy.deps import DbSession, WorkspaceContext, require
from momentsales.tenancy.permissions import Action

router = APIRouter(tags=["receipts"])
files_router = APIRouter(tags=["files"])


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sha256: str
    mime: str
    size_bytes: int
    original_name: str
    ocr_status: OcrStatus
    ocr_provider: str | None
    ocr: dict[str, Any] | None = None
    ocr_error: str | None
    confirmed_at: datetime | None
    created_at: datetime
    spend_id: uuid.UUID | None = None
    download_url: str | None = None


class ReceiptPage(BaseModel):
    items: list[ReceiptOut]
    next_cursor: str | None


class ReceiptConfirm(BaseModel):
    """Either the user-checked spend details (creates the spend) or an existing spend."""

    spend: SpendCreate | None = None
    spend_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one(self) -> ReceiptConfirm:
        if (self.spend is None) == (self.spend_id is None):
            raise ValueError("send either spend or spend_id")
        return self


def _out(receipt: Receipt, spend_id: uuid.UUID | None = None, url: str | None = None) -> ReceiptOut:
    out = ReceiptOut.model_validate(receipt)
    if receipt.ocr_json:
        out.ocr = {k: v for k, v in receipt.ocr_json.items() if k != "raw"}
    out.spend_id = spend_id
    out.download_url = url
    return out


@router.post("/receipts", status_code=201, response_model=ReceiptOut)
async def upload_receipt(
    file: UploadFile,
    background: BackgroundTasks,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.RECEIPTS_UPLOAD))],
) -> ReceiptOut:
    content = await file.read(settings.receipt_max_bytes + 1)
    try:
        receipt = await service.upload(
            session,
            registry.storage(),
            workspace_id=ctx.workspace_id,
            actor_user_id=ctx.user_id,
            content=content,
            filename=file.filename,
            max_bytes=settings.receipt_max_bytes,
        )
    except StorageError as exc:
        raise ProblemError(
            503, "storage_unavailable", "Upload failed", "Please try again in a minute."
        ) from exc
    if receipt.ocr_status is OcrStatus.PENDING:
        schedule(
            background,
            settings,
            OCR_JOB,
            {"workspace_id": str(ctx.workspace_id), "receipt_id": str(receipt.id)},
        )
    return _out(receipt)


@router.get("/receipts", response_model=ReceiptPage)
async def list_receipts(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_READ))],
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> ReceiptPage:
    limit = max(1, min(limit, MAX_LIMIT))
    rows = list((await session.scalars(paginate(select(Receipt), Receipt.id, cursor, limit))).all())
    items, next_cursor = page(rows, limit)
    links = dict(
        (
            await session.execute(
                select(SpendReceipt.receipt_id, SpendReceipt.spend_id).where(
                    SpendReceipt.receipt_id.in_([r.id for r in items])
                )
            )
        ).all()
    )
    return ReceiptPage(items=[_out(r, links.get(r.id)) for r in items], next_cursor=next_cursor)


@router.get("/receipts/{receipt_id}", response_model=ReceiptOut)
async def get_receipt(
    receipt_id: uuid.UUID,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_READ))],
) -> ReceiptOut:
    receipt = await service.get_receipt(session, receipt_id)
    spend_id = await session.scalar(
        select(SpendReceipt.spend_id).where(SpendReceipt.receipt_id == receipt_id)
    )
    url = await registry.storage().signed_url(
        receipt.file_key,
        workspace_id=ctx.workspace_id,
        filename=receipt.original_name,
        ttl_seconds=settings.signed_url_ttl_seconds,
    )
    return _out(receipt, spend_id, url)


@router.post("/receipts/{receipt_id}/confirm", status_code=201)
async def confirm_receipt(
    receipt_id: uuid.UUID,
    body: ReceiptConfirm,
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_WRITE))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """The human check (FR-7): saves the confirmed values as a spend, or attaches the
    receipt to an existing spend. Either way the spend's 'missing receipt' flag clears."""

    async def handler() -> tuple[int, dict[str, object]]:
        receipt = await service.get_receipt(session, receipt_id)
        if await session.scalar(
            select(SpendReceipt.id).where(SpendReceipt.receipt_id == receipt_id)
        ):
            raise ProblemError(
                409, "receipt_already_used", "Already attached", "This receipt is on a spend."
            )
        if body.spend is not None:
            s = body.spend
            spend = await ledger.create_spend(
                session,
                workspace_id=ctx.workspace_id,
                actor_user_id=ctx.user_id,
                data=ledger.SpendIn(
                    amount_minor=s.amount_minor,
                    currency=s.currency,
                    base_amount_minor=s.base_amount_minor,
                    tax_minor=s.tax_minor,
                    spent_on=s.spent_on,
                    vendor=s.vendor,
                    category=s.category,
                    payment_method=s.payment_method,
                    product_key=s.product_key,
                    order_id=s.order_id,
                    note=s.note,
                    source=SpendSource.RECEIPT,
                    receipt_id=receipt_id,
                    extra_audit={
                        "receipt_sha256": receipt.sha256,
                        "ocr_provider": receipt.ocr_provider,
                    },
                ),
            )
            spend_id = spend.id
        else:
            assert body.spend_id is not None
            await ledger.link_receipt(
                session,
                workspace_id=ctx.workspace_id,
                actor_user_id=ctx.user_id,
                spend_id=body.spend_id,
                receipt_id=receipt_id,
            )
            spend_id = body.spend_id
        await service.mark_confirmed(session, receipt, ctx.user_id)
        return 201, {"receipt_id": str(receipt_id), "spend_id": str(spend_id)}

    return await run_idempotent(
        session,
        workspace_id=ctx.workspace_id,
        key=idempotency_key,
        body={"receipt_id": str(receipt_id), **body.model_dump(mode="json")},
        handler=handler,
    )


@files_router.get("/files/{token}", include_in_schema=False)
async def download(token: str, registry: Registry) -> Response:
    """Local-storage downloads. The short-lived signed token is the permission (ADR-0012)."""
    claims = unsign(token)
    if not claims or not isinstance(claims.get("k"), str):
        raise ProblemError(404, "not_found", "Not found", "This link has expired.")
    key, workspace = claims["k"], claims.get("w", "")
    try:
        if not key_belongs_to(key, uuid.UUID(workspace)):
            raise ValueError
        content = await registry.storage().get(key)
    except (ValueError, StorageError) as exc:
        raise ProblemError(404, "not_found", "Not found", "This link has expired.") from exc
    filename = str(claims.get("f") or "download")
    mime = service.MIME_BY_EXTENSION.get(key.rsplit(".", 1)[-1], "application/octet-stream")
    return Response(
        content,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{service.safe_name(filename)}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
