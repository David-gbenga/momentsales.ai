"""Phase 1B SaaS billing models (FR-10, ADR-0015, ADR-0016).

`plans`, `plan_prices` and `addon_products` are global (no tenant data, platform-admin
writable). Everything else is tenant data with RLS forced (migration 0003). Money is integer
minor units + ISO currency. SaaS invoices are kept apart from the seller's own ledger.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from momentsales.db.base import Base, Timestamps, UUIDPk
from momentsales.db.ledger import _created_at, _workspace_fk
from momentsales.db.models import _enum

CURRENCY_CHECK = "currency ~ '^[A-Z]{3}$'"


class BillingInterval(enum.StrEnum):
    MONTH = "month"
    YEAR = "year"


class SubscriptionStatus(enum.StrEnum):
    INCOMPLETE = "incomplete"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    PAUSED = "paused"
    CANCELED = "canceled"


class AddonKind(enum.StrEnum):
    RUN_PACK = "run_pack"
    SINGLE_REPORT = "single_report"


class PurchaseStatus(enum.StrEnum):
    OPEN = "open"
    PAID = "paid"
    EXPIRED = "expired"


class RunPool(enum.StrEnum):
    PLAN = "plan"
    PACK = "pack"
    SINGLE = "single"


# --- Global catalogue ------------------------------------------------------------------


class Plan(UUIDPk, Timestamps, Base):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # NULL = unlimited
    research_runs: Mapped[int | None] = mapped_column(Integer)
    live_listings: Mapped[int | None] = mapped_column(Integer)
    seats: Mapped[int | None] = mapped_column(Integer)
    annual_bonus_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    platform_fee_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    stripe_product_id: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (
        CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,29}$'", name="code_format"),
        CheckConstraint("platform_fee_bps BETWEEN 0 AND 5000", name="platform_fee_bps_range"),
        CheckConstraint(
            "(research_runs IS NULL OR research_runs >= 0)"
            " AND (live_listings IS NULL OR live_listings >= 0)"
            " AND (seats IS NULL OR seats >= 1) AND annual_bonus_runs >= 0",
            name="limits_valid",
        ),
    )


class PlanPrice(UUIDPk, Timestamps, Base):
    __tablename__ = "plan_prices"

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    interval: Mapped[BillingInterval] = mapped_column(
        _enum(BillingInterval, "billing_interval"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    stripe_price_id: Mapped[str | None] = mapped_column(String(100), unique=True)

    __table_args__ = (
        UniqueConstraint("plan_id", "currency", "interval", name="uq_plan_prices_plan_currency"),
        CheckConstraint(CURRENCY_CHECK, name="currency_iso3"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
    )


class AddonProduct(UUIDPk, Timestamps, Base):
    __tablename__ = "addon_products"

    kind: Mapped[AddonKind] = mapped_column(_enum(AddonKind, "addon_kind"), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    runs: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    stripe_product_id: Mapped[str | None] = mapped_column(String(100))
    stripe_price_id: Mapped[str | None] = mapped_column(String(100), unique=True)

    __table_args__ = (
        UniqueConstraint("kind", "currency", name="uq_addon_products_kind_currency"),
        CheckConstraint(CURRENCY_CHECK, name="currency_iso3"),
        CheckConstraint("amount_minor > 0 AND runs > 0", name="amount_runs_positive"),
    )


# --- Tenant data -----------------------------------------------------------------------


class Subscription(UUIDPk, Timestamps, Base):
    """The workspace's billing account and copy of its Stripe subscription (ADR-0016).
    One row per workspace."""

    __tablename__ = "subscriptions"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, unique=True
    )
    # NULL until a plan is chosen: the row also holds the Stripe customer for one-off
    # purchases (pay-per-report) made before subscribing.
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    interval: Mapped[BillingInterval | None] = mapped_column(
        _enum(BillingInterval, "billing_interval")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        _enum(SubscriptionStatus, "subscription_status"), nullable=False
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100), index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    trial_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    past_due_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pause_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_reminders_sent: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Stripe `event.created` of the last applied event: older events never regress state.
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint(CURRENCY_CHECK, name="currency_iso3"),)


class UsageCounter(UUIDPk, Timestamps, Base):
    __tablename__ = "usage_counters"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    research_runs_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("workspace_id", "period_start", name="uq_usage_counters_period"),
        CheckConstraint("research_runs_used >= 0", name="runs_nonnegative"),
    )


class RunCredit(UUIDPk, Timestamps, Base):
    """Run balances that don't reset each period: packs and single reports (ADR-0015)."""

    __tablename__ = "run_credits"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, unique=True
    )
    pack_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    single_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("pack_runs >= 0 AND single_runs >= 0", name="credits_nonnegative"),
    )


class RunConsumption(UUIDPk, Base):
    """One row per consumed run; makes consume_run/refund_run idempotent."""

    __tablename__ = "run_consumptions"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    pool: Mapped[RunPool] = mapped_column(_enum(RunPool, "run_pool"), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("workspace_id", "key", name="uq_run_consumptions_key"),)


class AddonPurchase(UUIDPk, Timestamps, Base):
    __tablename__ = "addon_purchases"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    addon_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("addon_products.id"), nullable=False)
    kind: Mapped[AddonKind] = mapped_column(_enum(AddonKind, "addon_kind"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    runs: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[PurchaseStatus] = mapped_column(
        _enum(PurchaseStatus, "purchase_status"), nullable=False
    )
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        CheckConstraint(CURRENCY_CHECK, name="currency_iso3"),
        CheckConstraint("quantity > 0 AND runs > 0 AND amount_minor > 0", name="positive"),
    )


class SaasInvoice(UUIDPk, Timestamps, Base):
    """What the seller pays momentsales.ai (not part of the seller's ledger)."""

    __tablename__ = "saas_invoices"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    stripe_invoice_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    number: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    billing_reason: Mapped[str | None] = mapped_column(String(50))
    hosted_invoice_url: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint(CURRENCY_CHECK, name="currency_iso3"),)


class TrialExtension(UUIDPk, Base):
    __tablename__ = "trial_extensions"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    applied_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (CheckConstraint("days BETWEEN 1 AND 60", name="days_range"),)


class CancelSurvey(UUIDPk, Base):
    __tablename__ = "cancel_surveys"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    offer_made: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            "reason IN ('no_sales','too_expensive','missing_feature','seasonal_break','other')",
            name="reason_valid",
        ),
        CheckConstraint("offer_made IN ('pause','discount','none')", name="offer_valid"),
        CheckConstraint(
            "outcome IN ('canceled','accepted_pause','accepted_discount')", name="outcome_valid"
        ),
    )


class SubscriptionPause(UUIDPk, Base):
    __tablename__ = "subscription_pauses"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resumes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (CheckConstraint("months BETWEEN 1 AND 3", name="months_range"),)
