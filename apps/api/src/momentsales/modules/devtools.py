"""Local-only developer endpoints. Mounted only when ENV is local/test AND PROVIDER_MODE=mock."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field

from momentsales.adapters.email.mock import read_outbox
from momentsales.adapters.identity.mock import MockIdentityAdapter
from momentsales.core.errors import ProblemError
from momentsales.deps import AppRedis, AppSettings

router = APIRouter(prefix="/dev", tags=["dev"])


class MockAuthorizeIn(BaseModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=200)
    state: str
    nonce: str
    redirect_uri: str


class RedirectOut(BaseModel):
    redirect_url: str


@router.get("/outbox")
async def outbox(redis: AppRedis, limit: int = 50) -> list[dict[str, object]]:
    """Emails captured by the mock email adapter, newest first."""
    return await read_outbox(redis, min(max(limit, 1), 200))


@router.post("/mock-google/authorize", response_model=RedirectOut)
async def mock_google_authorize(body: MockAuthorizeIn, settings: AppSettings) -> RedirectOut:
    """Backs the /dev/mock-google consent page: returns where to send the browser next."""
    if body.redirect_uri != settings.google_redirect_uri:
        raise ProblemError(400, "bad_redirect_uri", "redirect_uri does not match")
    code = MockIdentityAdapter(settings).issue_code(str(body.email), body.nonce, body.name)
    return RedirectOut(
        redirect_url=f"{body.redirect_uri}?{urlencode({'code': code, 'state': body.state})}"
    )
