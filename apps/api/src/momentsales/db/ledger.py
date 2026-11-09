"""Phase 1 models: payments integration, orders, ledger, spends, receipts, imports, exports.

Money is integer minor units + ISO currency (ADR-0010). Append-only tables are enforced in
migration 0002 (grants, policies and triggers), not just here.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from momentsales.db.base import Base, Timestamps, UUIDPk, utcnow
from momentsales.db.models import _enum

PRODUCT_KEY_CHECK = "product_key IS NULL OR product_key ~ '^[a-z0-9][a-z0-9-]{0,63}$'"


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


def _workspace_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)


class IntegrationType(enum.StrEnum):
    STRIPE = "stripe"
    AVASAM = "avasam"  # a seller's own Avasam keys (ADR-0020)


class IntegrationStatus(enum.StrEnum):
    PENDING = "pending"
    RESTRICTED = "restricted"
    ACTIVE = "active"
    DISCONNECTED = "disconnected"


class OrderChannel(enum.StrEnum):
    STRIPE = "stripe"
    MANUAL = "manual"
    CSV = "csv"


class OrderStatus(enum.StrEnum):
    PENDING = "pending"
    PAID = "paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


class CheckoutStatus(enum.StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"


class LedgerType(enum.StrEnum):
    SALE = "sale"
    FEE = "fee"
    REFUND = "refund"
    PAYOUT = "payout"
    SPEND = "spend"
    ADJUSTMENT = "adjustment"


class SpendCategory(enum.StrEnum):
    STOCK = "stock"
    ADS = "ads"
    SOFTWARE = "software"
    SHIPPING = "shipping"
    FEES = "fees"
    OTHER = "other"


class SpendSource(enum.StrEnum):
    MANUAL = "manual"
    CSV_IMPORT = "csv_import"
    RECEIPT = "receipt"


class OcrStatus(enum.StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ImportKind(enum.StrEnum):
    SPENDS = "spends"
    SALES = "sales"


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# --- Payments integration --------------------------------------------------------------


class Integration(UUIDPk, Timestamps, Base):
    """A workspace's connection to an external account (Stripe in Phase 1)."""

    __tablename__ = "integrations"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    type: Mapped[IntegrationType] = mapped_column(
        _enum(IntegrationType, "integration_type"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    status: Mapped[IntegrationStatus] = mapped_column(
        _enum(IntegrationStatus, "integration_status"), nullable=False
    )
    charges_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payouts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details_submitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requirements: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    livemode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    connected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Seller API keys, Fernet-encrypted (ADR-0020). Never returned by the API.
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("workspace_id", "type"),)


class WebhookEvent(UUIDPk, Base):
    """Every verified inbound webhook, stored before processing (ADR-0011).

    `workspace_id` is NULL until the event is matched to a workspace; only platform context
    (the webhook receiver and the worker) can see unmatched rows.
    """

    __tablename__ = "webhook_events"

    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    account: Mapped[str | None] = mapped_column(String(100))
    livemode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = _created_at()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(30))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class IdempotencyKey(UUIDPk, Base):
    __tablename__ = "idempotency_keys"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (UniqueConstraint("workspace_id", "key"),)


# --- Orders ------------------------------------------------------------------------------


class Order(UUIDPk, Timestamps, Base):
    __tablename__ = "orders"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    channel: Mapped[OrderChannel] = mapped_column(
        _enum(OrderChannel, "order_channel"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(_enum(OrderStatus, "order_status"), nullable=False)
    customer_email: Mapped[str | None] = mapped_column(String(320))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fees_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    refunded_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Platform fee (ADR-0017): part of fees_minor, from Stripe's fee_details; refunded in
    # proportion to refunds through the application fee `platform_fee_id`.
    platform_fee_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    platform_fee_refunded_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    platform_fee_id: Mapped[str | None] = mapped_column(String(100))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    charge_id: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("workspace_id", "channel", "external_id"),
        CheckConstraint("gross_minor >= 0", name="gross_non_negative"),
        CheckConstraint("refunded_minor >= 0", name="refunded_non_negative"),
        CheckConstraint(
            "platform_fee_minor >= 0 AND platform_fee_refunded_minor BETWEEN 0 AND "
            "platform_fee_minor",
            name="platform_fee_refund_range",
        ),
        Index("ix_orders_workspace_paid_at", "workspace_id", "paid_at"),
    )


class OrderItem(UUIDPk, Base):
    __tablename__ = "order_items"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    product_key: Mapped[str | None] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_amount_minor >= 0", name="unit_amount_non_negative"),
        CheckConstraint(PRODUCT_KEY_CHECK, name="product_key_format"),
    )


class CheckoutSession(UUIDPk, Timestamps, Base):
    """A test-mode checkout created from the dashboard (Phase 1) or storefront (Phase 3)."""

    __tablename__ = "checkout_sessions"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    provider_session_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    status: Mapped[CheckoutStatus] = mapped_column(
        _enum(CheckoutStatus, "checkout_status"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    # Platform fee requested at checkout (ADR-0017); the ledger uses Stripe's figure.
    application_fee_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (
        CheckConstraint("application_fee_minor >= 0", name="application_fee_non_negative"),
    )


# --- Ledger ------------------------------------------------------------------------------


class Spend(UUIDPk, Base):
    """A business expense (FR-7 /spend). Append-only; corrections via SpendVoid."""

    __tablename__ = "spends"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    base_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spent_on: Mapped[date] = mapped_column(Date, nullable=False)
    vendor: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[SpendCategory] = mapped_column(
        _enum(SpendCategory, "spend_category"), nullable=False
    )
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False)
    product_key: Mapped[str | None] = mapped_column(String(64))
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[SpendSource] = mapped_column(_enum(SpendSource, "spend_source"), nullable=False)
    import_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("csv_imports.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("base_amount_minor > 0", name="base_amount_positive"),
        CheckConstraint("tax_minor >= 0 AND tax_minor <= amount_minor", name="tax_in_range"),
        CheckConstraint("category <> 'ads' OR product_key IS NOT NULL", name="ads_need_product"),
        CheckConstraint(PRODUCT_KEY_CHECK, name="product_key_format"),
        Index("ix_spends_workspace_spent_on", "workspace_id", "spent_on"),
    )


class SpendVoid(UUIDPk, Base):
    __tablename__ = "spend_voids"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    spend_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spends.id"), nullable=False, unique=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    voided_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Receipt(UUIDPk, Base):
    """An uploaded receipt. The stored file never changes; sha256 is recorded at upload."""

    __tablename__ = "receipts"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    file_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ocr_status: Mapped[OcrStatus] = mapped_column(_enum(OcrStatus, "ocr_status"), nullable=False)
    ocr_provider: Mapped[str | None] = mapped_column(String(30))
    ocr_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    ocr_error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (Index("ix_receipts_workspace_sha256", "workspace_id", "sha256"),)


class SpendReceipt(UUIDPk, Base):
    """Links one receipt to a spend. Append-only; a spend with no row is 'missing receipt'."""

    __tablename__ = "spend_receipts"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    spend_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spends.id"), nullable=False, unique=True
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id"), nullable=False)
    linked_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class LedgerEntry(UUIDPk, Base):
    """Append-only, signed money movements (ADR-0010)."""

    __tablename__ = "ledger_entries"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    type: Mapped[LedgerType] = mapped_column(_enum(LedgerType, "ledger_type"), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"), index=True)
    spend_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("spends.id"), index=True)
    product_key: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ledger_entries.id"))
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("workspace_id", "source", "source_ref"),
        CheckConstraint("amount_minor <> 0", name="amount_non_zero"),
        CheckConstraint(PRODUCT_KEY_CHECK, name="product_key_format"),
        Index("ix_ledger_entries_workspace_occurred_on", "workspace_id", "occurred_on"),
    )


# --- Imports and exports -------------------------------------------------------------------


class CsvImport(UUIDPk, Timestamps, Base):
    __tablename__ = "csv_imports"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    kind: Mapped[ImportKind] = mapped_column(_enum(ImportKind, "import_kind"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    __table_args__ = (UniqueConstraint("workspace_id", "kind", "sha256"),)


class Export(UUIDPk, Timestamps, Base):
    """An audit pack (FR-8 / AC-10)."""

    __tablename__ = "exports"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), nullable=False)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    audit_chain_ok: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("period_end >= period_start", name="period_order"),)


APPEND_ONLY_TABLES = frozenset({"ledger_entries", "spends", "spend_voids", "spend_receipts"})
