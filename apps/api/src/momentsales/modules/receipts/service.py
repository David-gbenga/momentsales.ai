"""Receipts (FR-7 /receipt): upload, OCR pre-fill, human confirmation.

Originals are stored unaltered with their SHA-256. OCR output only pre-fills the confirm
form; nothing reaches the ledger until a person confirms.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.ocr.base import OcrError
from momentsales.adapters.storage.base import StoragePort, key_belongs_to, object_key
from momentsales.core.errors import ProblemError, not_found
from momentsales.core.logging import get_logger
from momentsales.db.ledger import OcrStatus, Receipt
from momentsales.modules.audit import service as audit
from momentsales.modules.providers.registry import (
    FailoverOcr,
    ProviderConfigError,
    ProviderRegistry,
)

log = get_logger(__name__)

HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1")
EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/pdf": ".pdf",
    "image/heic": ".heic",
}
MIME_BY_EXTENSION = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "pdf": "application/pdf",
    "heic": "image/heic",
    "csv": "text/csv",
    "zip": "application/zip",
    "jsonl": "application/x-ndjson",
    "json": "application/json",
}
# HEIC is accepted and stored, but OCR needs a JPEG conversion that is pending approval of
# the conversion library (ADR-0014); until then HEIC receipts are typed in by hand.
OCR_MIME = frozenset({"image/jpeg", "image/png", "application/pdf"})


def detect_mime(content: bytes) -> str | None:
    """Identify the file from its first bytes (never trust the client's content type)."""
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in HEIC_BRANDS:
        return "image/heic"
    return None


def safe_name(name: str | None) -> str:
    cleaned = "".join(c for c in (name or "") if c.isalnum() or c in "._- ").strip()
    return (cleaned or "receipt")[:255]


async def upload(
    session: AsyncSession,
    storage: StoragePort,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    content: bytes,
    filename: str | None,
    max_bytes: int,
) -> Receipt:
    if not content:
        raise ProblemError(422, "empty_file", "Empty file", "The file is empty.")
    if len(content) > max_bytes:
        raise ProblemError(
            413,
            "file_too_large",
            "File too large",
            f"Receipts must be {max_bytes // 1_048_576} MB or less.",
        )
    mime = detect_mime(content)
    if mime is None:
        raise ProblemError(
            415, "unsupported_file", "Unsupported file", "Upload a PDF, JPG, PNG or HEIC file."
        )
    receipt_id = uuid.uuid4()
    key = object_key(workspace_id, "receipts", receipt_id, EXTENSIONS[mime])
    sha256 = hashlib.sha256(content).hexdigest()
    receipt = Receipt(
        id=receipt_id,
        workspace_id=workspace_id,
        file_key=key,
        sha256=sha256,
        mime=mime,
        size_bytes=len(content),
        original_name=safe_name(filename),
        ocr_status=OcrStatus.PENDING if mime in OCR_MIME else OcrStatus.SKIPPED,
        uploaded_by=actor_user_id,
    )
    session.add(receipt)
    await session.flush()
    # Stored after the row is staged; a failed put rolls the row back with the request.
    await storage.put(key, content, mime)
    await audit.record(
        session,
        action="receipt.uploaded",
        object_type="receipt",
        object_id=receipt.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"sha256": sha256, "mime": mime, "size_bytes": len(content)},
    )
    return receipt


async def run_ocr(
    session: AsyncSession, storage: StoragePort, ocr: FailoverOcr | None, receipt: Receipt
) -> None:
    """Fill `ocr_json`; idempotent (does nothing once OCR finished)."""
    if receipt.ocr_status is not OcrStatus.PENDING:
        return
    if ocr is None:
        receipt.ocr_status = OcrStatus.FAILED
        receipt.ocr_error = "No OCR provider is configured; enter the details by hand."
        return
    if not key_belongs_to(receipt.file_key, receipt.workspace_id):
        raise RuntimeError("receipt key outside its workspace")
    content = await storage.get(receipt.file_key)
    if hashlib.sha256(content).hexdigest() != receipt.sha256:
        receipt.ocr_status = OcrStatus.FAILED
        receipt.ocr_error = "Stored file does not match its recorded hash."
        log.error("receipt_hash_mismatch", receipt_id=str(receipt.id))
        return
    try:
        result = await ocr.extract(content, receipt.mime)
    except OcrError as exc:
        receipt.ocr_status = OcrStatus.FAILED
        receipt.ocr_provider = exc.provider
        receipt.ocr_error = (
            "We couldn't read this receipt automatically; enter the details by hand."
        )
        log.warning("ocr_failed", receipt_id=str(receipt.id), error=str(exc))
        return
    receipt.ocr_status = OcrStatus.DONE
    receipt.ocr_provider = result.provider
    receipt.ocr_json = result.as_json()
    receipt.ocr_error = None


async def get_receipt(session: AsyncSession, receipt_id: uuid.UUID) -> Receipt:
    receipt = await session.get(Receipt, receipt_id)
    if receipt is None:
        raise not_found("receipt")
    return receipt


async def mark_confirmed(session: AsyncSession, receipt: Receipt, actor_user_id: uuid.UUID) -> None:
    receipt.confirmed_by = actor_user_id
    receipt.confirmed_at = datetime.now(UTC)


async def pending_receipts(session: AsyncSession, older_than: datetime) -> list[Receipt]:
    rows = await session.scalars(
        select(Receipt)
        .where(Receipt.ocr_status == OcrStatus.PENDING, Receipt.created_at < older_than)
        .limit(100)
    )
    return list(rows.all())


async def ocr_for(registry: ProviderRegistry, session: AsyncSession) -> FailoverOcr | None:
    try:
        return await registry.ocr(session)
    except ProviderConfigError as exc:
        log.warning("ocr_unconfigured", error=str(exc))
        return None
