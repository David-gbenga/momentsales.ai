"""Feature flags and the country list (UK only at launch)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from momentsales.deps import Flags
from momentsales.modules.flags.service import COUNTRY_CURRENCY, COUNTRY_NAMES
from momentsales.tenancy.deps import DbSession, WorkspaceContext, require
from momentsales.tenancy.permissions import Action, export_matrix

router = APIRouter(tags=["flags"])


class FlagsOut(BaseModel):
    flags: dict[str, bool]


class CountryOut(BaseModel):
    code: str
    name: str
    currency: str


@router.get("/flags", response_model=FlagsOut)
async def effective_flags(
    session: DbSession,
    flags: Flags,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.FLAGS_READ))],
) -> FlagsOut:
    return FlagsOut(flags=await flags.effective(session, ctx.workspace_id))


@router.get("/countries", response_model=list[CountryOut])
async def countries(session: DbSession, flags: Flags) -> list[CountryOut]:
    """Countries a seller can pick. Only enabled country flags are returned (AC-35)."""
    return [
        CountryOut(code=c, name=COUNTRY_NAMES.get(c, c), currency=COUNTRY_CURRENCY.get(c, ""))
        for c in await flags.enabled_countries(session)
    ]


@router.get("/permissions", response_model=dict[str, list[str]])
async def permissions() -> dict[str, list[str]]:
    """The role → action matrix the API enforces (for UI gating only)."""
    return export_matrix()
