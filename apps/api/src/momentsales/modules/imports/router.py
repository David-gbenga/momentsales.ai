"""CSV import endpoints: `POST /spends/import-csv` and `POST /sales/import-csv` upload and
preview; `POST /imports/{id}/confirm` applies the user's column mapping."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from momentsales.adapters.storage.base import StorageError
from momentsales.core.errors import ProblemError, forbidden
from momentsales.db.ledger import CsvImport, ImportKind, SpendCategory
from momentsales.deps import Registry
from momentsales.modules.imports import service
from momentsales.modules.ledger.schemas import ProductKey
from momentsales.tenancy.deps import DbSession, WorkspaceContext, require
from momentsales.tenancy.permissions import Action, is_allowed

router = APIRouter(tags=["imports"])


class PreviewOut(BaseModel):
    import_id: uuid.UUID
    kind: ImportKind
    filename: str
    rows_total: int
    headers: list[str]
    sample: list[dict[str, str]]
    suggested_mapping: dict[str, str]
    fields: list[str]
    required: list[str]


class DefaultsIn(BaseModel):
    currency: str = Field(default="GBP", pattern=r"^[A-Z]{3}$")
    category: SpendCategory | None = None
    vendor: str | None = Field(default=None, max_length=255)
    product_key: ProductKey = None
    payment_method: str = Field(default="card", max_length=50)
    channel: str = Field(default="CSV import", max_length=50)


class ConfirmIn(BaseModel):
    mapping: dict[str, str]
    defaults: DefaultsIn = DefaultsIn()


class ImportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ImportKind
    filename: str
    status: str
    rows_total: int
    rows_imported: int
    rows_skipped: int
    errors: list[dict[str, Any]]


async def _preview(
    kind: ImportKind,
    file: UploadFile,
    session: DbSession,
    registry: Registry,
    ctx: WorkspaceContext,
) -> PreviewOut:
    content = await file.read(service.MAX_BYTES + 1)
    try:
        record, headers, sample, mapping = await service.create_preview(
            session,
            registry.storage(),
            workspace_id=ctx.workspace_id,
            actor_user_id=ctx.user_id,
            kind=kind,
            filename=file.filename or "import.csv",
            content=content,
        )
    except StorageError as exc:
        raise ProblemError(503, "storage_unavailable", "Upload failed", "Try again.") from exc
    fields = service.SPEND_FIELDS if kind is ImportKind.SPENDS else service.SALE_FIELDS
    return PreviewOut(
        import_id=record.id,
        kind=kind,
        filename=record.filename,
        rows_total=record.rows_total,
        headers=headers,
        sample=sample,
        suggested_mapping=mapping,
        fields=list(fields),
        required=list(service.REQUIRED[kind]),
    )


@router.post("/spends/import-csv", status_code=201, response_model=PreviewOut)
async def preview_spends(
    file: UploadFile,
    session: DbSession,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_WRITE))],
) -> PreviewOut:
    """Upload a spends CSV (e.g. an ad platform's export) and get a column-mapping preview."""
    return await _preview(ImportKind.SPENDS, file, session, registry, ctx)


@router.post("/sales/import-csv", status_code=201, response_model=PreviewOut)
async def preview_sales(
    file: UploadFile,
    session: DbSession,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.SALES_RECORD))],
) -> PreviewOut:
    """Upload sales made on another channel (J7) and get a column-mapping preview."""
    return await _preview(ImportKind.SALES, file, session, registry, ctx)


@router.post("/imports/{import_id}/confirm", response_model=ImportOut)
async def confirm_import(
    import_id: uuid.UUID,
    body: ConfirmIn,
    session: DbSession,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_WRITE))],
) -> ImportOut:
    record = await session.get(CsvImport, import_id)
    sales = record is not None and record.kind is ImportKind.SALES
    if sales and not is_allowed(ctx.role, Action.SALES_RECORD):
        raise forbidden(Action.SALES_RECORD.value)
    d = body.defaults
    result = await service.confirm(
        session,
        registry.storage(),
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        import_id=import_id,
        mapping=body.mapping,
        defaults=service.Defaults(
            currency=d.currency,
            category=d.category,
            vendor=d.vendor,
            product_key=d.product_key,
            payment_method=d.payment_method,
            channel=d.channel,
        ),
    )
    return ImportOut.model_validate(result)


@router.get("/imports/{import_id}", response_model=ImportOut)
async def get_import(
    import_id: uuid.UUID,
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_READ))],
) -> ImportOut:
    record = await session.get(CsvImport, import_id)
    if record is None:
        from momentsales.core.errors import not_found

        raise not_found("import")
    return ImportOut.model_validate(record)
