"""Workspace, member and invitation endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select

from momentsales.adapters.email.base import EmailMessage
from momentsales.core.errors import ProblemError
from momentsales.core.security import hash_ip
from momentsales.db.models import Invitation, Membership, MembershipStatus, Role, User, Workspace
from momentsales.deps import AppSettings, Flags, Registry, send_email
from momentsales.modules.entitlements.service import get_entitlements
from momentsales.modules.workspaces import service
from momentsales.tenancy.deps import (
    CurrentPrincipal,
    DbSession,
    WorkspaceContext,
    client_ip,
    require,
    require_path,
)
from momentsales.tenancy.permissions import Action

router = APIRouter(tags=["workspaces"])


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    country: str = Field(default="GB", pattern=r"^[A-Z]{2}$")
    legal_name: str | None = Field(default=None, max_length=200)


class WorkspacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    legal_name: str | None = Field(default=None, max_length=200)
    tax_ids: dict[str, str] | None = None


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    legal_name: str | None
    country: str
    base_currency: str
    tax_ids: dict[str, Any]
    status: str
    created_at: datetime


class MyWorkspaceOut(WorkspaceOut):
    role: str


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    name: str | None
    role: Role
    joined_at: datetime


class RoleChangeIn(BaseModel):
    role: Role


class InvitationIn(BaseModel):
    email: EmailStr
    role: Role


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime


class AcceptIn(BaseModel):
    token: str = Field(min_length=40, max_length=200)


class AcceptOut(BaseModel):
    workspace_id: uuid.UUID
    role: Role


def _ws_out(w: Workspace) -> WorkspaceOut:
    return WorkspaceOut(
        id=w.id,
        name=w.name,
        slug=w.slug,
        legal_name=w.legal_name,
        country=w.country,
        base_currency=w.base_currency,
        tax_ids=w.tax_ids,
        status=w.status.value,
        created_at=w.created_at,
    )


# --- Workspaces ----------------------------------------------------------------------


@router.post("/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceIn,
    request: Request,
    principal: CurrentPrincipal,
    session: DbSession,
    flags: Flags,
    settings: AppSettings,
) -> WorkspaceOut:
    workspace = await service.create_workspace(
        session,
        user=principal.user,
        name=body.name,
        country=body.country,
        enabled_countries=await flags.enabled_countries(session),
        legal_name=body.legal_name,
        ip_hash=hash_ip(client_ip(request), settings),
    )
    return _ws_out(workspace)


@router.get("/workspaces", response_model=list[MyWorkspaceOut])
async def list_my_workspaces(
    principal: CurrentPrincipal, session: DbSession
) -> list[MyWorkspaceOut]:
    rows = (
        await session.execute(
            select(Workspace, Membership.role)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(
                Membership.user_id == principal.user_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
            .order_by(Workspace.name)
        )
    ).all()
    return [MyWorkspaceOut(**_ws_out(w).model_dump(), role=role.value) for w, role in rows]


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require_path(Action.WORKSPACE_READ))],
) -> WorkspaceOut:
    w = await session.get(Workspace, ctx.workspace_id)
    if w is None:
        raise ProblemError(404, "workspace_not_found", "Workspace not found")
    return _ws_out(w)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def patch_workspace(
    body: WorkspacePatch,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    ctx: Annotated[WorkspaceContext, Depends(require_path(Action.WORKSPACE_UPDATE))],
) -> WorkspaceOut:
    w = await session.get(Workspace, ctx.workspace_id)
    if w is None:
        raise ProblemError(404, "workspace_not_found", "Workspace not found")
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        changes.pop("name")
    await service.update_workspace(
        session,
        w,
        actor_id=ctx.user_id,
        changes=changes,
        ip_hash=hash_ip(client_ip(request), settings),
    )
    return _ws_out(w)


# --- Members (workspace from X-Workspace-Id) -----------------------------------------


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.MEMBERS_READ))],
) -> list[MemberOut]:
    rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.workspace_id == ctx.workspace_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
            .order_by(Membership.created_at)
        )
    ).all()
    return [
        MemberOut(
            id=m.id, user_id=u.id, email=u.email, name=u.name, role=m.role, joined_at=m.created_at
        )
        for m, u in rows
    ]


@router.patch("/members/{membership_id}", response_model=MemberOut)
async def change_member_role(
    membership_id: uuid.UUID,
    body: RoleChangeIn,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.MEMBERS_CHANGE_ROLE))],
) -> MemberOut:
    m = await service.get_member(session, ctx.workspace_id, membership_id)
    await service.change_role(
        session,
        workspace_id=ctx.workspace_id,
        actor_id=ctx.user_id,
        membership=m,
        new_role=body.role,
        ip_hash=hash_ip(client_ip(request), settings),
    )
    u = await session.get(User, m.user_id)
    assert u is not None
    return MemberOut(
        id=m.id, user_id=u.id, email=u.email, name=u.name, role=m.role, joined_at=m.created_at
    )


@router.delete("/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    membership_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.MEMBERS_REMOVE))],
) -> Response:
    m = await service.get_member(session, ctx.workspace_id, membership_id)
    await service.remove_member(
        session,
        workspace_id=ctx.workspace_id,
        actor_id=ctx.user_id,
        membership=m,
        ip_hash=hash_ip(client_ip(request), settings),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Invitations ---------------------------------------------------------------------


@router.get("/invitations", response_model=list[InvitationOut])
async def list_invitations(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.MEMBERS_READ))],
) -> list[InvitationOut]:
    rows = await session.scalars(
        select(Invitation)
        .where(
            Invitation.workspace_id == ctx.workspace_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .order_by(Invitation.created_at.desc())
    )
    return [InvitationOut.model_validate(r) for r in rows]


@router.post("/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
async def invite(
    body: InvitationIn,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.MEMBERS_INVITE))],
) -> InvitationOut:
    inv, token = await service.create_invitation(
        session,
        settings,
        get_entitlements(),
        workspace_id=ctx.workspace_id,
        actor_id=ctx.user_id,
        email=str(body.email),
        role=body.role,
        ip_hash=hash_ip(client_ip(request), settings),
    )
    ws = await session.get(Workspace, ctx.workspace_id)
    assert ws is not None
    link = f"{settings.web_base_url.rstrip('/')}/invite/accept?token={token}"
    await send_email(
        registry,
        session,
        EmailMessage(
            to=inv.email,
            subject=f"You're invited to {ws.name} on momentsales.ai",
            text=(
                f"{ctx.principal.user.email} invited you to join {ws.name} as "
                f"{inv.role.value}.\n\nAccept: {link}\n\n"
                f"The invitation expires in {settings.invitation_ttl_days} days.\n"
            ),
            idempotency_key=f"invite-{inv.id}",
            tags={"category": "invitation"},
        ),
    )
    return InvitationOut.model_validate(inv)


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.MEMBERS_INVITE))],
) -> Response:
    await service.revoke_invitation(
        session,
        workspace_id=ctx.workspace_id,
        actor_id=ctx.user_id,
        invitation_id=invitation_id,
        ip_hash=hash_ip(client_ip(request), settings),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invitations/accept", response_model=AcceptOut)
async def accept_invitation(
    body: AcceptIn,
    request: Request,
    principal: CurrentPrincipal,
    session: DbSession,
    settings: AppSettings,
) -> AcceptOut:
    m = await service.accept_invitation(
        session,
        user=principal.user,
        token=body.token,
        ip_hash=hash_ip(client_ip(request), settings),
    )
    return AcceptOut(workspace_id=m.workspace_id, role=m.role)
