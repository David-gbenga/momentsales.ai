"""Request dependencies: who is calling, and in which workspace (ADR-0004).

Every tenant route depends on `require(Action.X)`. That:
1. authenticates the bearer token,
2. reads the workspace from the `X-Workspace-Id` header (or the path),
3. sets the RLS context for the transaction,
4. checks an active membership (no membership → 404, so existence is not leaked),
5. checks the role against the permission matrix (→ 403),
6. for writes, checks the workspace has a trialing or active subscription (→ 403).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Header, Path, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.db import get_db, set_tenant_context
from momentsales.core.errors import ProblemError, forbidden, unauthorized
from momentsales.core.security import decode_access_token
from momentsales.db.models import Membership, MembershipStatus, Role, User, UserStatus
from momentsales.tenancy.permissions import Action, is_allowed

DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]

# Every workspace-scoped dependency built by require()/require_path(); the isolation
# tests use this to find every tenant route automatically.
TENANT_DEPENDENCIES: set[Callable[..., Awaitable[WorkspaceContext]]] = set()


@dataclass(frozen=True, slots=True)
class Principal:
    user: User
    session_family: uuid.UUID
    ip: str | None

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    principal: Principal
    workspace_id: uuid.UUID
    role: Role
    membership_id: uuid.UUID

    @property
    def user_id(self) -> uuid.UUID:
        return self.principal.user_id


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def current_principal(
    request: Request,
    session: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized()
    try:
        claims = decode_access_token(authorization[7:].strip())
    except jwt.PyJWTError as exc:
        raise unauthorized("Your session has expired. Sign in again.") from exc
    await set_tenant_context(session, user_id=claims.user_id)
    user = await session.get(User, claims.user_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        raise unauthorized()
    return Principal(user=user, session_family=claims.session_family, ip=client_ip(request))


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def enter_workspace(
    session: AsyncSession, principal: Principal, workspace_id: uuid.UUID
) -> WorkspaceContext:
    await set_tenant_context(session, user_id=principal.user_id, workspace_id=workspace_id)
    membership = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == principal.user_id,
            Membership.status == MembershipStatus.ACTIVE,
        )
    )
    if membership is None:
        raise ProblemError(
            404, "workspace_not_found", "Workspace not found", "No such workspace for you."
        )
    return WorkspaceContext(
        principal=principal,
        workspace_id=workspace_id,
        role=membership.role,
        membership_id=membership.id,
    )


def _parse_workspace_id(raw: str | None) -> uuid.UUID:
    if not raw:
        raise ProblemError(
            400, "workspace_required", "Workspace required", "Send the X-Workspace-Id header."
        )
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ProblemError(
            404, "workspace_not_found", "Workspace not found", "No such workspace for you."
        ) from exc


async def _gate(session: AsyncSession, ctx: WorkspaceContext, action: Action) -> None:
    """Subscription access gate for writes (ADR-0016): 403 without an active plan."""
    from momentsales.modules.billing.entitlements import GATED_ACTIONS, ensure_writable

    if action in GATED_ACTIONS:
        await ensure_writable(session, ctx.workspace_id)


def require(action: Action) -> Callable[..., Awaitable[WorkspaceContext]]:
    """Dependency factory for routes scoped by the X-Workspace-Id header."""

    async def _dep(
        session: DbSession,
        principal: CurrentPrincipal,
        x_workspace_id: Annotated[str | None, Header()] = None,
    ) -> WorkspaceContext:
        ctx = await enter_workspace(session, principal, _parse_workspace_id(x_workspace_id))
        if not is_allowed(ctx.role, action):
            raise forbidden(action.value)
        await _gate(session, ctx, action)
        return ctx

    TENANT_DEPENDENCIES.add(_dep)
    return _dep


def require_path(action: Action) -> Callable[..., Awaitable[WorkspaceContext]]:
    """Dependency factory for routes with the workspace id in the path."""

    async def _dep(
        session: DbSession,
        principal: CurrentPrincipal,
        workspace_id: Annotated[str, Path()],
    ) -> WorkspaceContext:
        ctx = await enter_workspace(session, principal, _parse_workspace_id(workspace_id))
        if not is_allowed(ctx.role, action):
            raise forbidden(action.value)
        await _gate(session, ctx, action)
        return ctx

    TENANT_DEPENDENCIES.add(_dep)
    return _dep


async def require_platform_admin(principal: CurrentPrincipal) -> Principal:
    if not principal.user.is_platform_admin:
        raise forbidden("platform.admin")
    return principal
