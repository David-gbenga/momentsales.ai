"""Supplier endpoints (ADR-0020): Platform-admin verification evidence and a seller's own
Avasam connection. Avasam keys are write-only: no response ever includes them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, HttpUrl

from momentsales.db.ledger import Integration
from momentsales.db.research import VerificationCheck
from momentsales.deps import AppSettings, Registry
from momentsales.modules.suppliers import service
from momentsales.tenancy.deps import (
    DbSession,
    Principal,
    WorkspaceContext,
    require,
    require_platform_admin,
)
from momentsales.tenancy.permissions import Action

router = APIRouter(tags=["suppliers"])


class SupplierRefOut(BaseModel):
    provider: str
    name: str
    warehouse_countries: list[str]
    returns_policy_url: str | None


class VerificationIn(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    check: VerificationCheck
    passed: bool
    evidence_url: HttpUrl
    evidence_note: str | None = Field(default=None, max_length=2000)
    verified_at: datetime | None = None


class VerificationOut(BaseModel):
    id: uuid.UUID
    provider: str
    check: VerificationCheck
    passed: bool
    evidence_url: str
    evidence_note: str | None
    verified_at: datetime
    verified_by: uuid.UUID | None


class AvasamConnectIn(BaseModel):
    consumer_key: str = Field(min_length=1, max_length=200)
    secret_key: str = Field(min_length=1, max_length=200)


class AvasamStatusOut(BaseModel):
    connected: bool
    status: Literal["active", "disconnected"] | None = None
    connected_at: datetime | None = None
    mode: Literal["mock", "live"] | None = None


AdminDep = Annotated[Principal, Depends(require_platform_admin)]


@router.get("/admin/suppliers", response_model=list[SupplierRefOut])
async def admin_suppliers(session: DbSession, _: AdminDep) -> list[SupplierRefOut]:
    return [
        SupplierRefOut.model_validate(s, from_attributes=True)
        for s in await service.list_suppliers(session)
    ]


@router.get("/admin/supplier-verifications", response_model=list[VerificationOut])
async def admin_verifications(
    session: DbSession, _: AdminDep, provider: str | None = None
) -> list[VerificationOut]:
    rows = await service.list_verifications(session, provider)
    return [VerificationOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/admin/supplier-verifications", status_code=201, response_model=VerificationOut)
async def admin_record_verification(
    body: VerificationIn, session: DbSession, principal: AdminDep
) -> VerificationOut:
    """Record evidence for one check of the Verified UK supplier rule (append-only)."""
    row = await service.record_verification(
        session,
        provider=body.provider,
        check=body.check,
        passed=body.passed,
        evidence_url=str(body.evidence_url),
        evidence_note=body.evidence_note,
        verified_at=body.verified_at,
        actor_user_id=principal.user_id,
    )
    return VerificationOut.model_validate(row, from_attributes=True)


def _avasam_status(row: Integration | None) -> AvasamStatusOut:
    if row is None:
        return AvasamStatusOut(connected=False)
    status = row.status.value
    return AvasamStatusOut(
        connected=status == "active",
        status="active" if status == "active" else "disconnected",
        connected_at=row.last_sync_at,
        mode="live" if row.livemode else "mock",
    )


@router.get("/integrations/avasam", response_model=AvasamStatusOut)
async def avasam_status(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.WORKSPACE_READ))],
) -> AvasamStatusOut:
    return _avasam_status(await service.get_avasam(session))


@router.post("/integrations/avasam/connect", response_model=AvasamStatusOut)
async def avasam_connect(
    body: AvasamConnectIn,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.INTEGRATIONS_SUPPLIERS_MANAGE))],
) -> AvasamStatusOut:
    """Connect the workspace's own Avasam seller account (Owner or Admin)."""
    row = await service.connect_avasam(
        session,
        registry,
        settings,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        consumer_key=body.consumer_key.strip(),
        secret_key=body.secret_key.strip(),
    )
    return _avasam_status(row)


@router.delete("/integrations/avasam", status_code=204)
async def avasam_disconnect(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.INTEGRATIONS_SUPPLIERS_MANAGE))],
) -> Response:
    await service.disconnect_avasam(
        session, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id
    )
    return Response(status_code=204)
