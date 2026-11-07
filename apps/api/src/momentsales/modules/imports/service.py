"""CSV imports for spends (incl. ad-platform exports, FR-13) and outside sales (FR-7 /sale).

Two steps: upload -> preview with a suggested column mapping; confirm -> rows are validated
and written in one transaction (all valid rows, with per-row errors reported). The same file
can't be imported twice (sha256). No ad-platform presets: column names in their exports
weren't confirmed from documentation, so the user maps columns (ADR-0013 notes).
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.ocr.base import parse_amount, parse_date
from momentsales.adapters.storage.base import StoragePort, object_key
from momentsales.core.errors import ProblemError, not_found
from momentsales.db.ledger import CsvImport, ImportKind, JobStatus, SpendCategory, SpendSource
from momentsales.modules.audit import service as audit
from momentsales.modules.ledger import service as ledger
from momentsales.modules.ledger.money import BASE_CURRENCY, CURRENCY_EXPONENTS
from momentsales.modules.ledger.schemas import PRODUCT_KEY_PATTERN

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
PREVIEW_ROWS = 5

SPEND_FIELDS = (
    "date",
    "amount",
    "vendor",
    "category",
    "product_key",
    "currency",
    "base_amount",
    "tax",
    "payment_method",
    "note",
)
SALE_FIELDS = ("date", "amount", "description", "quantity", "product_key", "order_ref", "channel")
REQUIRED = {ImportKind.SPENDS: ("date", "amount"), ImportKind.SALES: ("date", "amount")}
# Header words we recognise when suggesting a mapping (case-insensitive, substring).
HINTS: dict[str, tuple[str, ...]] = {
    "date": ("date", "day", "reporting starts"),
    "amount": ("amount", "spent", "cost", "total", "price"),
    "vendor": ("vendor", "supplier", "merchant", "payee"),
    "category": ("category",),
    "product_key": ("product", "sku"),
    "currency": ("currency",),
    "tax": ("vat", "tax"),
    "note": ("campaign", "note", "description", "memo"),
    "description": ("item", "description", "product name", "title"),
    "quantity": ("qty", "quantity"),
    "order_ref": ("order", "reference", "ref"),
    "channel": ("channel", "platform", "marketplace"),
}


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ProblemError(422, "bad_encoding", "Unreadable file", "Save the CSV as UTF-8 and retry.")


def parse_csv(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    if len(content) > MAX_BYTES:
        raise ProblemError(
            413, "file_too_large", "File too large", "CSV files must be 2 MB or less."
        )
    text = _decode(content)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
    if not headers:
        raise ProblemError(
            422, "no_header", "No header row", "The first row must name the columns."
        )
    rows: list[dict[str, str]] = []
    for row in reader:
        if len(rows) >= MAX_ROWS:
            raise ProblemError(
                422, "too_many_rows", "Too many rows", f"Import at most {MAX_ROWS} rows at a time."
            )
        cleaned = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
        if any(cleaned.values()):
            rows.append(cleaned)
    return headers, rows


def suggest_mapping(kind: ImportKind, headers: list[str]) -> dict[str, str]:
    fields = SPEND_FIELDS if kind is ImportKind.SPENDS else SALE_FIELDS
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for field in fields:
        for header in headers:
            if header in used:
                continue
            if any(hint in header.lower() for hint in HINTS.get(field, ())):
                mapping[field] = header
                used.add(header)
                break
    return mapping


async def create_preview(
    session: AsyncSession,
    storage: StoragePort,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    kind: ImportKind,
    filename: str,
    content: bytes,
) -> tuple[CsvImport, list[str], list[dict[str, str]], dict[str, str]]:
    headers, rows = parse_csv(content)
    sha256 = hashlib.sha256(content).hexdigest()
    if await session.scalar(
        select(CsvImport.id).where(CsvImport.kind == kind, CsvImport.sha256 == sha256)
    ):
        raise ProblemError(
            409, "already_imported", "Already imported", "This exact file was imported before."
        )
    record = CsvImport(
        workspace_id=workspace_id,
        kind=kind,
        filename=filename[:255] or "import.csv",
        sha256=sha256,
        column_mapping={},
        rows_total=len(rows),
        errors=[],
        status=JobStatus.PENDING,
        created_by=actor_user_id,
    )
    session.add(record)
    await session.flush()
    await storage.put(object_key(workspace_id, "imports", record.id, ".csv"), content, "text/csv")
    return record, headers, rows[:PREVIEW_ROWS], suggest_mapping(kind, headers)


@dataclass(frozen=True, slots=True)
class Defaults:
    currency: str = BASE_CURRENCY
    category: SpendCategory | None = None
    vendor: str | None = None
    product_key: str | None = None
    payment_method: str = "card"
    channel: str = "CSV import"


def _cell(row: dict[str, str], mapping: dict[str, str], field: str) -> str:
    column = mapping.get(field)
    return row.get(column, "").strip() if column else ""


def _key(value: str) -> str | None:
    import re

    key = value.strip().lower().replace(" ", "-")
    if not key:
        return None
    if not re.match(PRODUCT_KEY_PATTERN, key):
        raise ValueError(f"product key {value!r} must be letters, digits and dashes")
    return key


def _spend_row(row: dict[str, str], mapping: dict[str, str], d: Defaults) -> ledger.SpendIn:
    currency = (_cell(row, mapping, "currency") or d.currency).upper()
    if currency not in CURRENCY_EXPONENTS:
        raise ValueError(f"unsupported currency {currency!r}")
    spent_on = parse_date(_cell(row, mapping, "date"))
    if spent_on is None:
        raise ValueError("date must be YYYY-MM-DD or DD/MM/YYYY")
    amount = parse_amount(_cell(row, mapping, "amount"), currency)
    if not amount:
        raise ValueError("amount is missing or not a positive number")
    if currency == BASE_CURRENCY:
        base = amount
    else:
        base_value = parse_amount(_cell(row, mapping, "base_amount"), BASE_CURRENCY)
        if not base_value:
            raise ValueError(f"{currency} rows need the GBP amount charged (base_amount column)")
        base = base_value
    category_text = _cell(row, mapping, "category").lower()
    try:
        category = SpendCategory(category_text) if category_text else d.category
    except ValueError as exc:
        raise ValueError(f"unknown category {category_text!r}") from exc
    if category is None:
        raise ValueError("category is missing (map a column or choose a default)")
    vendor = _cell(row, mapping, "vendor") or d.vendor
    if not vendor:
        raise ValueError("vendor is missing (map a column or choose a default)")
    product_key = _key(_cell(row, mapping, "product_key")) or d.product_key
    if category is SpendCategory.ADS and not product_key:
        raise ValueError("ads rows need a product key")
    tax = parse_amount(_cell(row, mapping, "tax"), currency) or 0
    if tax > amount:
        raise ValueError("tax is larger than the amount")
    return ledger.SpendIn(
        amount_minor=amount,
        currency=currency,
        base_amount_minor=base,
        tax_minor=tax,
        spent_on=spent_on,
        vendor=vendor[:255],
        category=category,
        payment_method=(_cell(row, mapping, "payment_method") or d.payment_method)[:50],
        product_key=product_key,
        note=_cell(row, mapping, "note")[:2000] or None,
        source=SpendSource.CSV_IMPORT,
    )


def _sale_row(
    row: dict[str, str], mapping: dict[str, str], d: Defaults
) -> tuple[Any, ledger.ItemIn, str, str | None]:
    sold_on = parse_date(_cell(row, mapping, "date"))
    if sold_on is None:
        raise ValueError("date must be YYYY-MM-DD or DD/MM/YYYY")
    amount = parse_amount(_cell(row, mapping, "amount"), BASE_CURRENCY)
    if not amount:
        raise ValueError("amount is missing or not a positive number")
    quantity_text = _cell(row, mapping, "quantity") or "1"
    if not quantity_text.isdigit() or int(quantity_text) < 1:
        raise ValueError("quantity must be a whole number of at least 1")
    item = ledger.ItemIn(
        description=(_cell(row, mapping, "description") or "Sale")[:255],
        quantity=int(quantity_text),
        unit_amount_minor=amount,
        product_key=_key(_cell(row, mapping, "product_key")) or d.product_key,
    )
    channel = (_cell(row, mapping, "channel") or d.channel)[:50]
    return sold_on, item, channel, _cell(row, mapping, "order_ref") or None


async def confirm(
    session: AsyncSession,
    storage: StoragePort,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    import_id: uuid.UUID,
    mapping: dict[str, str],
    defaults: Defaults,
) -> CsvImport:
    record = await session.get(CsvImport, import_id, with_for_update=True)
    if record is None:
        raise not_found("import")
    if record.status is not JobStatus.PENDING:
        raise ProblemError(409, "import_done", "Already confirmed", "This import has run.")
    fields = SPEND_FIELDS if record.kind is ImportKind.SPENDS else SALE_FIELDS
    unknown = set(mapping) - set(fields)
    if unknown:
        raise ProblemError(422, "unknown_fields", "Unknown fields", ", ".join(sorted(unknown)))
    for field in REQUIRED[record.kind]:
        if field not in mapping:
            raise ProblemError(422, "mapping_incomplete", "Map required columns", f"Map {field!r}.")
    content = await storage.get(object_key(workspace_id, "imports", record.id, ".csv"))
    headers, rows = parse_csv(content)
    missing = [c for c in mapping.values() if c not in headers]
    if missing:
        raise ProblemError(422, "unknown_columns", "Unknown columns", ", ".join(missing))
    errors: list[dict[str, Any]] = []
    imported = 0
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        try:
            if record.kind is ImportKind.SPENDS:
                data = _spend_row(row, mapping, defaults)
                await ledger.create_spend(
                    session,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    data=ledger.SpendIn(**{**_asdict(data), "import_id": record.id}),
                )
            else:
                sold_on, item, channel, ref = _sale_row(row, mapping, defaults)
                from momentsales.db.ledger import OrderChannel

                await ledger.record_manual_sale(
                    session,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    channel_label=channel,
                    sold_on=sold_on,
                    items=[item],
                    order_ref=None,
                    note=f"Imported from {record.filename}, row {number}",
                    channel=OrderChannel.CSV,
                    external_id=f"{record.id}:{number}"
                    if ref is None
                    else f"{record.id}:{ref}:{number}",
                )
            imported += 1
        except (ValueError, ProblemError) as exc:
            detail = exc.detail if isinstance(exc, ProblemError) else str(exc)
            errors.append({"row": number, "error": detail})
    record.column_mapping = mapping
    record.rows_imported = imported
    record.rows_skipped = len(errors)
    record.errors = errors[:200]
    record.status = JobStatus.COMPLETED
    await audit.record(
        session,
        action="import.completed",
        object_type="csv_import",
        object_id=record.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"kind": record.kind.value, "imported": imported, "skipped": len(errors)},
    )
    return record


def _asdict(data: ledger.SpendIn) -> dict[str, Any]:
    from dataclasses import fields

    return {f.name: getattr(data, f.name) for f in fields(data)}
