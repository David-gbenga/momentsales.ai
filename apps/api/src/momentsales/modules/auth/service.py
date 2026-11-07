"""Authentication service (ADR-0003): magic links, OIDC sign-in, refresh rotation, TOTP."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.identity.base import VerifiedIdentity
from momentsales.core.config import Settings
from momentsales.core.ids import uuid7
from momentsales.core.security import (
    hash_token,
    issue_access_token,
    new_opaque_token,
)
from momentsales.db.models import MagicLinkToken, RefreshToken, User, UserStatus
from momentsales.modules.audit import service as audit


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user: User
    access_token: str
    refresh_token: str
    family_id: uuid.UUID


class RefreshOutcome:
    OK = "ok"
    INVALID = "invalid"
    REUSED = "reused"
    EXPIRED = "expired"


async def create_magic_link(
    session: AsyncSession, settings: Settings, email: str, ip_hash: str | None
) -> str:
    token = new_opaque_token()
    session.add(
        MagicLinkToken(
            email=email,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.magic_link_ttl_minutes),
            request_ip_hash=ip_hash,
        )
    )
    await session.flush()
    return token


async def consume_magic_link(session: AsyncSession, token: str) -> User | None:
    """Single use: returns the (possibly new) user, or None if invalid/expired/used."""
    now = datetime.now(UTC)
    row = await session.scalar(
        select(MagicLinkToken)
        .where(MagicLinkToken.token_hash == hash_token(token))
        .with_for_update()
    )
    if row is None or row.consumed_at is not None or row.expires_at <= now:
        return None
    row.consumed_at = now
    user = await session.scalar(select(User).where(User.email == row.email))
    if user is None:
        user = User(email=row.email.lower())
        session.add(user)
    if user.email_verified_at is None:
        user.email_verified_at = now
    await session.flush()
    return user


async def user_from_identity(session: AsyncSession, identity: VerifiedIdentity) -> User | None:
    """Find or create the user for a verified OIDC identity.

    Links to an existing email account only when the provider verified the email.
    """
    user = await session.scalar(select(User).where(User.google_sub == identity.subject))
    if user is not None:
        return user
    if not identity.email_verified:
        return None
    now = datetime.now(UTC)
    user = await session.scalar(select(User).where(User.email == identity.email))
    if user is None:
        user = User(email=identity.email.lower(), name=identity.name)
        session.add(user)
    user.google_sub = identity.subject
    user.email_verified_at = user.email_verified_at or now
    if not user.name and identity.name:
        user.name = identity.name
    await session.flush()
    return user


async def issue_session(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
    user_agent: str | None,
    ip_hash: str | None,
    method: str,
) -> IssuedSession:
    if user.status is not UserStatus.ACTIVE:
        raise PermissionError("user disabled")
    family = family_id or uuid7()
    refresh = new_opaque_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family,
            token_hash=hash_token(refresh),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
            user_agent=(user_agent or "")[:500] or None,
            ip_hash=ip_hash,
        )
    )
    if family_id is None:  # a new sign-in, not a rotation
        user.last_login_at = datetime.now(UTC)
        await audit.record(
            session,
            action="auth.sign_in",
            object_type="user",
            object_id=user.id,
            workspace_id=None,
            actor_user_id=user.id,
            after={"method": method},
            ip_hash=ip_hash,
        )
    await session.flush()
    access = issue_access_token(
        user.id, family, platform_admin=user.is_platform_admin, settings=settings
    )
    return IssuedSession(user=user, access_token=access, refresh_token=refresh, family_id=family)


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def rotate_refresh(
    session: AsyncSession,
    settings: Settings,
    token: str,
    *,
    user_agent: str | None,
    ip_hash: str | None,
) -> tuple[str, IssuedSession | None]:
    """Exchange a refresh token for a new pair.

    Reuse of an already-rotated token revokes the whole family (likely theft). The
    caller must commit the revocation even though it answers 401.
    """
    now = datetime.now(UTC)
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(token)).with_for_update()
    )
    if row is None:
        return RefreshOutcome.INVALID, None
    if row.revoked_at is not None:
        await revoke_family(session, row.family_id)
        return RefreshOutcome.REUSED, None
    if row.expires_at <= now:
        return RefreshOutcome.EXPIRED, None
    user = await session.get(User, row.user_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        await revoke_family(session, row.family_id)
        return RefreshOutcome.INVALID, None
    issued = await issue_session(
        session,
        settings,
        user,
        family_id=row.family_id,
        user_agent=user_agent,
        ip_hash=ip_hash,
        method="refresh",
    )
    new_row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(issued.refresh_token))
    )
    row.revoked_at = now
    row.replaced_by_id = new_row.id if new_row else None
    return RefreshOutcome.OK, issued
