"""Phase 2 research models (FR-1, FR-2, FR-12, ADR-0018).

`season_moments` is global reference data (no tenant data, platform writable). Everything
else is tenant data with RLS forced (migration 0005). Money is integer minor units + ISO
currency. Every fact shown to a user traces to a `source_snapshots` row (AC-3).
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
    Index,
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


class RunMode(enum.StrEnum):
    SEASONAL = "seasonal"
    AUDIENCE = "audience"  # Later roadmap (L1); rejected while module.audience_research is off


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"  # stopped at the cost cap or a degraded step; results clearly marked
    FAILED = "failed"


TERMINAL_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED})


class StepStatus(enum.StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    DEGRADED = "degraded"  # finished without some data; the run continues
    FAILED = "failed"


class SeasonMoment(UUIDPk, Timestamps, Base):
    """A retail moment for a country. `rule` is evaluated per year (season.py):
    `fixed:MM-DD`, `easter:+N`, `nth_weekday:MONTH:WEEKDAY:N[:+DAYS]` (weekday 0 = Monday,
    N = -1 for the last one)."""

    __tablename__ = "season_moments"

    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    rule: Mapped[str] = mapped_column(String(60), nullable=False)
    category_hints: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("country", "key", name="uq_season_moments_country_key"),)


class ResearchRun(UUIDPk, Timestamps, Base):
    __tablename__ = "research_runs"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    mode: Mapped[RunMode] = mapped_column(_enum(RunMode, "run_mode"), nullable=False)
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "run_status"), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    region: Mapped[str | None] = mapped_column(String(40))
    # Request parameters as sent: category, price band, season override, audience use.
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    window_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Season agent output (detected or overridden season, moments in the window).
    season: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # Demand intelligence (ADR-0019): trend candidates with market data, merged by agents.
    demand: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # Rounded up from cost_micros (GBP micros, 1,000,000 = £1); the API shows minor units.
    cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Spend per provider in micros (FR-15 cost per run by provider).
    cost_by_provider: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    cost_cap_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Run-metering key (entitlements.consume_run / refund_run) and optional client key.
    consumption_key: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    run_refunded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_research_runs_idem"),
        CheckConstraint("cost_minor >= 0 AND cost_cap_minor >= 0", name="cost_non_negative"),
        CheckConstraint("window_weeks BETWEEN 2 AND 12", name="window_range"),
    )


class AgentStep(UUIDPk, Base):
    """One agent's work in a run. Unique per (run, agent) so a retried job resumes."""

    __tablename__ = "agent_steps"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent: Mapped[str] = mapped_column(String(40), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus, "step_status"), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("run_id", "agent", name="uq_agent_steps_run_agent"),)


class SourceSnapshot(UUIDPk, Base):
    """A raw provider response with its URL and fetch time (AC-3). Kept 90 days."""

    __tablename__ = "source_snapshots"

    workspace_id: Mapped[uuid.UUID] = _workspace_fk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class CategorySeedTerm(UUIDPk, Timestamps, Base):
    """Global reference data (ADR-0019): starting search terms per category, used only to
    choose candidates for Google Trends. No fact shown to a user comes from this table."""

    __tablename__ = "category_seed_terms"

    country: Mapped[str] = mapped_column(String(2), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    term: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # order in group
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("country", "category", "term", name="uq_category_seed_terms"),
        Index("ix_category_seed_terms_lookup", "country", "category"),
    )


class MarketDataCache(UUIDPk, Base):
    """Shared, non-personal market data (FR-12, ADR-0019). Global on purpose: no
    workspace, run or user columns, so nothing here can identify who asked. Append-only;
    the newest row within the cache age wins."""

    __tablename__ = "market_data_cache"

    data_type: Mapped[str] = mapped_column(String(40), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    iso_week: Mapped[str] = mapped_column(String(8), nullable=False)  # e.g. 2026-W45
    query_key: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    query: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("ix_market_data_cache_lookup", "data_type", "country", "query_key", "fetched_at"),
    )


class AudienceProfile(UUIDPk, Timestamps, Base):
    """'Describe your audience' preferences (FR-12). No personal data."""

    __tablename__ = "audience_profiles"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, unique=True
    )
    age_range: Mapped[str | None] = mapped_column(String(20))
    gender_focus: Mapped[str | None] = mapped_column(String(20))
    interests: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    budget_band: Mapped[str | None] = mapped_column(String(20))
    style: Mapped[str | None] = mapped_column(String(60))
    updated_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class VerificationCheck(enum.StrEnum):
    """Checks 1-4 of the Verified UK supplier rule (FR-11). Check 5 (margin) is computed
    per product during research."""

    DROPSHIP_NO_MOQ = "dropship_no_moq"
    AUTO_STOCK_COST = "auto_stock_cost"
    TRACKED_UK = "tracked_uk"
    UK_RETURNS = "uk_returns"


class Supplier(UUIDPk, Timestamps, Base):
    """A dropship supplier platform (FR-11, ADR-0020). Global reference data in 2c;
    workspace-specific suppliers (manual) arrive with listings."""

    __tablename__ = "suppliers"

    provider: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    warehouse_countries: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    returns_policy_url: Mapped[str | None] = mapped_column(String(2000))
    notes: Mapped[str | None] = mapped_column(Text)


class SupplierVerification(UUIDPk, Base):
    """Evidence for one check of the Verified UK supplier rule, recorded by a Platform
    admin (ADR-0020). Global and append-only: the newest row per (provider, check) wins;
    with no row the check fails."""

    __tablename__ = "supplier_verifications"

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    check: Mapped[VerificationCheck] = mapped_column(
        _enum(VerificationCheck, "verification_check"), nullable=False
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    evidence_note: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("ix_supplier_verifications_lookup", "provider", "check", "verified_at"),
    )
