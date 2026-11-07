"""Workspaces, memberships and invitations.

Every permission change writes an automatic /touch (category team_and_permissions)
and an audit-log entry in the workspace's hash chain.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.config import Settings
from momentsales.core.db import set_tenant_context
from momentsales.core.errors import ProblemError
from momentsales.core.ids import uuid7
from momentsales.core.security import hash_token, new_opaque_token
from momentsales.db.models import (
    Invitation,
    Membership,
    MembershipStatus,
    Role,
    TouchCategory,
    User,
    Workspace,
)
from momentsales.modules.audit import service as audit
from momentsales.modules.entitlements.service import EntitlementService, Limit
from momentsales.modules.flags.service import COUNTRY_CURRENCY, COUNTRY_NAMES
from momentsales.modules.touches.service import record_auto_touch


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40].strip("-")
    return slug or "shop"


def workspace_snapshot(w: Workspace) -> dict[str, Any]:
    return {
        "name": w.name,
        "legal_name": w.legal_name,
        "country": w.country,
        "base_currency": w.base_currency,
        "tax_ids": w.tax_ids,
    }


async def _log_permission_change(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    description: str,
    object_type: str,
    object_id: uuid.UUID,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    ip_hash: str | None,
) -> None:
    await record_auto_touch(
        session,
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        category=TouchCategory.TEAM_AND_PERMISSIONS,
        description=description,
        object_type=object_type,
        object_id=object_id,
    )
    await audit.record(
        session,
        action=action,
        object_type=object_type,
        object_id=object_id,
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        before=before,
        after=after,
        ip_hash=ip_hash,
    )


# --- Workspaces ----------------------------------------------------------------------


async def create_workspace(
    session: AsyncSession,
    *,
    user: User,
    name: str,
    country: str,
    enabled_countries: list[str],
    legal_name: str | None,
    ip_hash: str | None,
) -> Workspace:
    if country not in enabled_countries:
        raise ProblemError(
            422,
            "country_not_available",
            "Country not available yet",
            f"{COUNTRY_NAMES.get(country, country)} is not available yet. "
            "momentsales.ai is UK-only at launch.",
            available=enabled_countries,
        )
    workspace_id = uuid7()
    await set_tenant_context(session, user_id=user.id, workspace_id=workspace_id)
    base = slugify(name)
    workspace: Workspace | None = None
    for attempt in range(5):
        slug = base if attempt == 0 else f"{base[:33]}-{secrets.token_hex(3)}"
        candidate = Workspace(
            id=workspace_id,
            name=name.strip(),
            slug=slug,
            legal_name=legal_name,
            country=country,
            base_currency=COUNTRY_CURRENCY[country],
            created_by=user.id,
        )
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
        except IntegrityError:
            # Slug taken (possibly by a workspace RLS hides from us): the savepoint
            # rollback discards the pending row; try a suffixed slug.
            if candidate in session:
                session.expunge(candidate)
            continue
        workspace = candidate
        break
    if workspace is None:
        raise ProblemError(409, "slug_unavailable", "Choose a different name")
    membership = Membership(workspace_id=workspace_id, user_id=user.id, role=Role.OWNER)
    session.add(membership)
    await session.flush()
    await audit.record(
        session,
        action="workspace.created",
        object_type="workspace",
        object_id=workspace_id,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        after=workspace_snapshot(workspace),
        ip_hash=ip_hash,
    )
    await _log_permission_change(
        session,
        workspace_id=workspace_id,
        actor_id=user.id,
        action="membership.created",
        description=f"Created workspace; {user.email} is Owner",
        object_type="membership",
        object_id=membership.id,
        before=None,
        after={"user_id": str(user.id), "role": Role.OWNER.value},
        ip_hash=ip_hash,
    )
    return workspace


async def update_workspace(
    session: AsyncSession,
    workspace: Workspace,
    *,
    actor_id: uuid.UUID,
    changes: dict[str, Any],
    ip_hash: str | None,
) -> Workspace:
    before = workspace_snapshot(workspace)
    for key, value in changes.items():
        setattr(workspace, key, value)
    await session.flush()
    after = workspace_snapshot(workspace)
    if before != after:
        await audit.record(
            session,
            action="workspace.updated",
            object_type="workspace",
            object_id=workspace.id,
            workspace_id=workspace.id,
            actor_user_id=actor_id,
            before=before,
            after=after,
            ip_hash=ip_hash,
        )
    return workspace


# --- Members -------------------------------------------------------------------------


async def _active_owner_count(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.role == Role.OWNER,
            Membership.status == MembershipStatus.ACTIVE,
        )
    )
    return int(count or 0)


async def get_member(
    session: AsyncSession, workspace_id: uuid.UUID, membership_id: uuid.UUID
) -> Membership:
    m = await session.scalar(
        select(Membership)
        .where(
            Membership.id == membership_id,
            Membership.workspace_id == workspace_id,
            Membership.status == MembershipStatus.ACTIVE,
        )
        .with_for_update()
    )
    if m is None:
        raise ProblemError(404, "member_not_found", "Member not found")
    return m


async def change_role(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    membership: Membership,
    new_role: Role,
    ip_hash: str | None,
) -> Membership:
    if membership.role == new_role:
        return membership
    if membership.role is Role.OWNER and await _active_owner_count(session, workspace_id) <= 1:
        raise ProblemError(
            409, "last_owner", "Keep at least one Owner", "Make someone else Owner first."
        )
    old = membership.role
    membership.role = new_role
    await session.flush()
    await _log_permission_change(
        session,
        workspace_id=workspace_id,
        actor_id=actor_id,
        action="membership.role_changed",
        description=f"Changed role from {old.value} to {new_role.value}",
        object_type="membership",
        object_id=membership.id,
        before={"role": old.value},
        after={"role": new_role.value},
        ip_hash=ip_hash,
    )
    return membership


async def remove_member(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    membership: Membership,
    ip_hash: str | None,
) -> None:
    if membership.role is Role.OWNER and await _active_owner_count(session, workspace_id) <= 1:
        raise ProblemError(409, "last_owner", "Keep at least one Owner")
    membership.status = MembershipStatus.REMOVED
    await session.flush()
    await _log_permission_change(
        session,
        workspace_id=workspace_id,
        actor_id=actor_id,
        action="membership.removed",
        description=f"Removed a {membership.role.value} from the workspace",
        object_type="membership",
        object_id=membership.id,
        before={"status": "active", "role": membership.role.value},
        after={"status": "removed"},
        ip_hash=ip_hash,
    )


# --- Invitations ---------------------------------------------------------------------


def _split_invite_token(token: str) -> tuple[uuid.UUID, str] | None:
    try:
        ws, secret = token.split(".", 1)
        return uuid.UUID(ws), secret
    except ValueError:
        return None


async def create_invitation(
    session: AsyncSession,
    settings: Settings,
    entitlements: EntitlementService,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    email: str,
    role: Role,
    ip_hash: str | None,
) -> tuple[Invitation, str]:
    if role is Role.OWNER:
        raise ProblemError(
            422, "owner_invite_not_allowed", "Invite as Admin, Analyst or Accountant"
        )
    await entitlements.check(session, workspace_id, Limit.SEATS)
    existing_member = await session.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.status == MembershipStatus.ACTIVE,
            User.email == email,
        )
    )
    if existing_member is not None:
        raise ProblemError(409, "already_member", "Already a member")
    now = datetime.now(UTC)
    pending = await session.scalars(
        select(Invitation).where(
            Invitation.workspace_id == workspace_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    )
    for p in pending:
        p.revoked_at = now
    secret = new_opaque_token()
    token = f"{workspace_id}.{secret}"
    invitation = Invitation(
        workspace_id=workspace_id,
        email=email.lower(),
        role=role,
        token_hash=hash_token(token),
        expires_at=now + timedelta(days=settings.invitation_ttl_days),
        invited_by=actor_id,
    )
    session.add(invitation)
    await session.flush()
    await _log_permission_change(
        session,
        workspace_id=workspace_id,
        actor_id=actor_id,
        action="invitation.created",
        description=f"Invited {email} as {role.value}",
        object_type="invitation",
        object_id=invitation.id,
        before=None,
        after={"email": email.lower(), "role": role.value},
        ip_hash=ip_hash,
    )
    return invitation, token


async def revoke_invitation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID,
    invitation_id: uuid.UUID,
    ip_hash: str | None,
) -> None:
    inv = await session.scalar(
        select(Invitation).where(
            Invitation.id == invitation_id, Invitation.workspace_id == workspace_id
        )
    )
    if inv is None or inv.accepted_at or inv.revoked_at:
        raise ProblemError(404, "invitation_not_found", "Invitation not found")
    inv.revoked_at = datetime.now(UTC)
    await _log_permission_change(
        session,
        workspace_id=workspace_id,
        actor_id=actor_id,
        action="invitation.revoked",
        description=f"Revoked invitation for {inv.email}",
        object_type="invitation",
        object_id=inv.id,
        before={"status": "pending"},
        after={"status": "revoked"},
        ip_hash=ip_hash,
    )


async def accept_invitation(
    session: AsyncSession, *, user: User, token: str, ip_hash: str | None
) -> Membership:
    invalid = ProblemError(
        400, "invalid_invitation", "Invitation not valid", "It may have expired or been revoked."
    )
    parsed = _split_invite_token(token)
    if parsed is None:
        raise invalid
    workspace_id, _ = parsed
    # The token proves the right to look inside this workspace's invitations only.
    await set_tenant_context(session, user_id=user.id, workspace_id=workspace_id)
    inv = await session.scalar(
        select(Invitation)
        .where(Invitation.token_hash == hash_token(token), Invitation.workspace_id == workspace_id)
        .with_for_update()
    )
    now = datetime.now(UTC)
    if inv is None or inv.accepted_at or inv.revoked_at or inv.expires_at <= now:
        raise invalid
    if inv.email.lower() != user.email.lower():
        raise ProblemError(
            403,
            "invitation_email_mismatch",
            "Wrong account",
            "Sign in with the email address the invitation was sent to.",
        )
    membership = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user.id
        )
    )
    if membership is None:
        membership = Membership(
            workspace_id=workspace_id, user_id=user.id, role=inv.role, invited_by=inv.invited_by
        )
        session.add(membership)
    else:
        membership.role = inv.role
        membership.status = MembershipStatus.ACTIVE
        membership.invited_by = inv.invited_by
    inv.accepted_at = now
    await session.flush()
    await _log_permission_change(
        session,
        workspace_id=workspace_id,
        actor_id=user.id,
        action="invitation.accepted",
        description=f"{user.email} joined as {inv.role.value}",
        object_type="membership",
        object_id=membership.id,
        before=None,
        after={"user_id": str(user.id), "role": inv.role.value},
        ip_hash=ip_hash,
    )
    return membership
