"""Platform-admin view of provider configuration (read-only in Phase 0)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from momentsales.deps import Registry
from momentsales.tenancy.deps import DbSession, Principal, require_platform_admin

router = APIRouter(prefix="/admin", tags=["admin"])


class ProviderOut(BaseModel):
    category: str
    primary: str | None
    secondary: str | None
    mode: Literal["mock", "live"]
    failover: Literal["auto", "manual"]
    source: Literal["env", "db"]
    built: list[str]


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(
    session: DbSession,
    registry: Registry,
    _: Annotated[Principal, Depends(require_platform_admin)],
) -> list[ProviderOut]:
    return [ProviderOut.model_validate(p.as_dict()) for p in await registry.describe_all(session)]
