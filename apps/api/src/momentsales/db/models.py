"""All ORM models for Phase 0 (one place so Alembic and tests see every table)."""

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
    Enum,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from momentsales.db.base import Base, Timestamps, UUIDPk, utcnow


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


class Role(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    ANALYST = "analyst"
    ACCOUNTANT = "accountant"


class UserStatus(enum.StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class WorkspaceStatus(enum.StrEnum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class MembershipStatus(enum.StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class TouchCategory(enum.StrEnum):
    RESEARCH_EDIT = "research_edit"
    LISTING_EDIT = "listing_edit"
    PRICING = "pricing"
    FULFILMENT = "fulfilment"
    CUSTOMER_SERVICE = "customer_service"
    AGENT_OVERRIDE = "agent_override"
    TEAM_AND_PERMISSIONS = "team_and_permissions"


# --- Platform tables (no workspace_id) -----------------------------------------------


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_platform_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    status: Mapped[UserStatus] = mapped_column(
        _enum(UserStatus, "user_status"),
        default=UserStatus.ACTIVE,
        server_default="active",
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MagicLinkToken(UUIDPk, Base):
    __tablename__ = "magic_link_tokens"

    email: Mapped[str] = mapped_column(CITEXT, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


class RefreshToken(UUIDPk, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


class FeatureFlag(Timestamps, Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    default_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ProviderConfig(Timestamps, Base):
    __tablename__ = "provider_configs"

    category: Mapped[str] = mapped_column(String(50), primary_key=True)
    primary: Mapped[str] = mapped_column(String(50), nullable=False)
    secondary: Mapped[str | None] = mapped_column(String(50))
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    failover: Mapped[str] = mapped_column(String(10), nullable=False)

    __table_args__ = (
        CheckConstraint("mode IN ('mock','live')", name="mode"),
        CheckConstraint("failover IN ('auto','manual')", name="failover"),
    )


class AuditChainHead(Base):
    """Latest hash per audit chain ("platform" or a workspace id).

    Holds only hashes, no tenant data, so it needs no RLS. Locked with
    SELECT ... FOR UPDATE to serialise appends to one chain.
    """

    __tablename__ = "audit_chain_heads"

    chain_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    last_seq: Mapped[int | None] = mapped_column(BigInteger)
    last_hash: Mapped[str | None] = mapped_column(String(64))


class WaitlistSignup(UUIDPk, Timestamps, Base):
    __tablename__ = "waitlist_signups"

    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    consent_text_version: Mapped[str] = mapped_column(String(50), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(100))
    ip_hash: Mapped[str | None] = mapped_column(String(64))


# --- Tenant tables (workspace_id + RLS) ----------------------------------------------


class Workspace(UUIDPk, Timestamps, Base):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    tax_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    status: Mapped[WorkspaceStatus] = mapped_column(
        _enum(WorkspaceStatus, "workspace_status"),
        default=WorkspaceStatus.ACTIVE,
        server_default="active",
        nullable=False,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        CheckConstraint("country ~ '^[A-Z]{2}$'", name="country_iso2"),
        CheckConstraint("base_currency ~ '^[A-Z]{3}$'", name="currency_iso3"),
        CheckConstraint("slug ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'", name="slug_format"),
    )


class Membership(UUIDPk, Timestamps, Base):
    __tablename__ = "memberships"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "workspace_role"), nullable=False)
    status: Mapped[MembershipStatus] = mapped_column(
        _enum(MembershipStatus, "membership_status"),
        default=MembershipStatus.ACTIVE,
        server_default="active",
        nullable=False,
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_memberships_ws_user"),)


class Invitation(UUIDPk, Timestamps, Base):
    __tablename__ = "invitations"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    role: Mapped[Role] = mapped_column(_enum(Role, "workspace_role"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class FeatureFlagOverride(Timestamps, Base):
    __tablename__ = "feature_flag_overrides"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    flag_key: Mapped[str] = mapped_column(
        ForeignKey("feature_flags.key", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    set_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Touch(UUIDPk, Base):
    """A human intervention (FR-7 /touch). Append-only."""

    __tablename__ = "touches"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    category: Mapped[TouchCategory] = mapped_column(
        _enum(TouchCategory, "touch_category"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    object_type: Mapped[str | None] = mapped_column(String(50))
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )

    __table_args__ = (CheckConstraint("minutes >= 0", name="minutes_non_negative"),)


class AuditLog(UUIDPk, Base):
    """Append-only, hash-chained audit log (ADR-0008).

    One chain per workspace; workspace_id NULL is the platform chain.
    """

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(100))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
