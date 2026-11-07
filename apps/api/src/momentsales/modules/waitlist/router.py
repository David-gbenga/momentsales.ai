"""Landing-page waitlist with double opt-in (UK GDPR / PECR: explicit, unbundled consent)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from momentsales.adapters.email.base import EmailMessage
from momentsales.core.errors import ProblemError
from momentsales.core.ratelimit import limit
from momentsales.core.security import hash_ip, hash_token, new_opaque_token, sign, unsign
from momentsales.db.models import WaitlistSignup
from momentsales.deps import AppSettings, Registry, send_email
from momentsales.tenancy.deps import DbSession, client_ip

router = APIRouter(prefix="/waitlist", tags=["waitlist"])

CONSENT_VERSION = "waitlist-v1"
CONSENT_TEXT = (
    "Email me about the momentsales.ai launch and early access. I can unsubscribe at any time."
)
UNSUBSCRIBE_TTL_SECONDS = 60 * 60 * 24 * 365 * 2


class ConsentOut(BaseModel):
    version: str
    text: str


class SignupIn(BaseModel):
    email: EmailStr
    consent: bool
    consent_version: str = CONSENT_VERSION
    source: str | None = Field(default=None, max_length=100)


class StatusOut(BaseModel):
    status: Literal["check_your_email", "confirmed", "unsubscribed"]


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=2000)


@router.get("/consent", response_model=ConsentOut)
async def consent_text() -> ConsentOut:
    return ConsentOut(version=CONSENT_VERSION, text=CONSENT_TEXT)


@router.post(
    "",
    response_model=StatusOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(limit("waitlist", 5, 60))],
)
async def join(
    body: SignupIn, request: Request, session: DbSession, settings: AppSettings, registry: Registry
) -> StatusOut:
    if not body.consent:
        raise ProblemError(422, "consent_required", "Consent required", "Tick the box to join.")
    if body.consent_version != CONSENT_VERSION:
        raise ProblemError(422, "consent_outdated", "Consent text changed", "Reload the page.")
    now = datetime.now(UTC)
    row = await session.scalar(select(WaitlistSignup).where(WaitlistSignup.email == body.email))
    if row is not None and row.confirmed_at and not row.unsubscribed_at:
        return StatusOut(status="check_your_email")  # already in; reveal nothing
    token = new_opaque_token()
    if row is None:
        row = WaitlistSignup(email=body.email)
        session.add(row)
    row.consent_text_version = CONSENT_VERSION
    row.consented_at = now
    row.confirm_token_hash = hash_token(token)
    row.confirmed_at = None
    row.unsubscribed_at = None
    row.source = body.source
    row.ip_hash = hash_ip(client_ip(request), settings)
    await session.flush()

    base = settings.web_base_url.rstrip("/")
    confirm_url = f"{base}/waitlist/confirm?token={token}"
    unsub = sign({"k": "unsub", "id": str(row.id)}, ttl_seconds=UNSUBSCRIBE_TTL_SECONDS)
    unsub_url = f"{base}/waitlist/unsubscribe?token={unsub}"
    await send_email(
        registry,
        session,
        EmailMessage(
            to=body.email,
            subject="Confirm your place on the momentsales.ai waitlist",
            text=(
                "Thanks for joining the momentsales.ai waitlist.\n\n"
                f"Confirm your email: {confirm_url}\n\n"
                f"Didn't ask for this? Ignore this email, or unsubscribe: {unsub_url}\n"
            ),
            idempotency_key=f"waitlist-confirm-{row.confirm_token_hash}",
            tags={"category": "waitlist"},
        ),
    )
    return StatusOut(status="check_your_email")


@router.post(
    "/confirm", response_model=StatusOut, dependencies=[Depends(limit("wl_confirm", 20, 60))]
)
async def confirm(body: TokenIn, session: DbSession) -> StatusOut:
    row = await session.scalar(
        select(WaitlistSignup).where(WaitlistSignup.confirm_token_hash == hash_token(body.token))
    )
    if row is None:
        raise ProblemError(400, "invalid_token", "Link not valid", "This link is invalid or used.")
    row.confirmed_at = datetime.now(UTC)
    row.confirm_token_hash = None
    return StatusOut(status="confirmed")


@router.post("/unsubscribe", response_model=StatusOut)
async def unsubscribe(body: TokenIn, session: DbSession) -> StatusOut:
    data = unsign(body.token)
    if not data or data.get("k") != "unsub":
        raise ProblemError(400, "invalid_token", "Link not valid", "This link is invalid.")
    row = await session.get(WaitlistSignup, uuid.UUID(str(data["id"])))
    if row is not None and row.unsubscribed_at is None:
        row.unsubscribed_at = datetime.now(UTC)
    return StatusOut(status="unsubscribed")
