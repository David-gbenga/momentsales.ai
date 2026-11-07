"""Local-only developer endpoints. Mounted only when ENV is local/test AND PROVIDER_MODE=mock."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, EmailStr, Field

from momentsales.adapters.email.mock import read_outbox
from momentsales.adapters.identity.mock import MockIdentityAdapter
from momentsales.adapters.payments.base import PaymentsError
from momentsales.adapters.payments.mock import MockPaymentsAdapter, sign_payload
from momentsales.core.errors import ProblemError
from momentsales.core.jobs import schedule
from momentsales.deps import AppRedis, AppSettings
from momentsales.modules.payments import service as payments_service
from momentsales.modules.payments.jobs import PROCESS_JOB
from momentsales.tenancy.deps import DbSession

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


# --- Mock Stripe (ADR-0011): dev pages drive the mock and push signed events through the
# real webhook receiver, so processing is the same code as with Stripe test mode.


class MockOnboardIn(BaseModel):
    account: str = Field(pattern=r"^acct_mock_[0-9a-f]{16}$")


class MockPayIn(BaseModel):
    email: EmailStr


class MockRefundIn(BaseModel):
    charge_id: str = Field(pattern=r"^ch_mock_[0-9a-f]{16}$")
    amount_minor: int | None = Field(default=None, gt=0)


class MockPayoutIn(BaseModel):
    account: str = Field(pattern=r"^acct_mock_[0-9a-f]{16}$")
    amount_minor: int = Field(gt=0)
    currency: str = "GBP"


class MockEventOut(BaseModel):
    event_id: str
    redirect_url: str | None = None


def _mock_payments(redis: AppRedis, settings: AppSettings) -> MockPaymentsAdapter:
    return MockPaymentsAdapter(redis, settings.web_base_url)


async def _deliver(
    event: dict[str, object],
    session: DbSession,
    settings: AppSettings,
    payments: MockPaymentsAdapter,
    background: BackgroundTasks,
) -> str:
    payload = json.dumps(event).encode()
    secret = settings.stripe_connect_webhook_secret.get_secret_value()
    event_id, is_new = await payments_service.ingest(
        session, payments, payload=payload, signature=sign_payload(payload, secret), secret=secret
    )
    if is_new:
        schedule(background, settings, PROCESS_JOB, {"event_id": event_id})
    return event_id


def _wrap(exc: PaymentsError) -> ProblemError:
    return ProblemError(400, "mock_stripe_error", "Mock Stripe", str(exc))


@router.get("/mock-stripe/session/{session_id}")
async def mock_stripe_session(
    session_id: str, redis: AppRedis, settings: AppSettings
) -> dict[str, object]:
    data = await _mock_payments(redis, settings).session(session_id)
    if data is None:
        raise ProblemError(404, "not_found", "No such mock checkout")
    return {
        k: data[k] for k in ("id", "status", "currency", "amount_total", "line_items", "cancel_url")
    }


@router.post("/mock-stripe/onboard", response_model=MockEventOut)
async def mock_stripe_onboard(
    body: MockOnboardIn,
    session: DbSession,
    redis: AppRedis,
    settings: AppSettings,
    background: BackgroundTasks,
) -> MockEventOut:
    payments = _mock_payments(redis, settings)
    try:
        event = await payments.complete_onboarding(body.account)
    except PaymentsError as exc:
        raise _wrap(exc) from exc
    event_id = await _deliver(event, session, settings, payments, background)
    return MockEventOut(
        event_id=event_id,
        redirect_url=f"{settings.web_base_url.rstrip('/')}/app/integrations?stripe=return",
    )


@router.post("/mock-stripe/checkout/{session_id}/pay", response_model=MockEventOut)
async def mock_stripe_pay(
    session_id: str,
    body: MockPayIn,
    session: DbSession,
    redis: AppRedis,
    settings: AppSettings,
    background: BackgroundTasks,
) -> MockEventOut:
    payments = _mock_payments(redis, settings)
    try:
        event = await payments.pay(session_id, str(body.email))
    except PaymentsError as exc:
        raise _wrap(exc) from exc
    data = event["data"]["object"]
    event_id = await _deliver(event, session, settings, payments, background)
    return MockEventOut(event_id=event_id, redirect_url=str(data["success_url"]))


@router.post("/mock-stripe/checkout/{session_id}/expire", response_model=MockEventOut)
async def mock_stripe_expire(
    session_id: str,
    session: DbSession,
    redis: AppRedis,
    settings: AppSettings,
    background: BackgroundTasks,
) -> MockEventOut:
    payments = _mock_payments(redis, settings)
    try:
        event = await payments.expire(session_id)
    except PaymentsError as exc:
        raise _wrap(exc) from exc
    return MockEventOut(event_id=await _deliver(event, session, settings, payments, background))


@router.post("/mock-stripe/refund", response_model=MockEventOut)
async def mock_stripe_refund(
    body: MockRefundIn,
    session: DbSession,
    redis: AppRedis,
    settings: AppSettings,
    background: BackgroundTasks,
) -> MockEventOut:
    """Stands in for the seller refunding in their own Stripe Dashboard."""
    payments = _mock_payments(redis, settings)
    try:
        event = await payments.refund(body.charge_id, body.amount_minor)
    except PaymentsError as exc:
        raise _wrap(exc) from exc
    return MockEventOut(event_id=await _deliver(event, session, settings, payments, background))


@router.post("/mock-stripe/payout", response_model=MockEventOut)
async def mock_stripe_payout(
    body: MockPayoutIn,
    session: DbSession,
    redis: AppRedis,
    settings: AppSettings,
    background: BackgroundTasks,
) -> MockEventOut:
    payments = _mock_payments(redis, settings)
    event = await payments.payout(body.account, body.amount_minor, body.currency)
    return MockEventOut(event_id=await _deliver(event, session, settings, payments, background))
