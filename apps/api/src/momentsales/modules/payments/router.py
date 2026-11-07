"""Stripe endpoints: Connect onboarding, test checkouts, and the signed webhook receivers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from momentsales.core.jobs import schedule
from momentsales.deps import AppSettings, Registry
from momentsales.modules.ledger import service as ledger
from momentsales.modules.ledger.idempotency import run_idempotent
from momentsales.modules.ledger.schemas import SaleItemIn
from momentsales.modules.payments import service
from momentsales.modules.payments.jobs import PROCESS_JOB
from momentsales.tenancy.deps import DbSession, WorkspaceContext, require
from momentsales.tenancy.permissions import Action

router = APIRouter(tags=["payments"])
webhooks_router = APIRouter(tags=["webhooks"])


class StripeStatusOut(BaseModel):
    connected: bool
    status: str | None = None
    account_id: str | None = None
    charges_enabled: bool = False
    payouts_enabled: bool = False
    details_submitted: bool = False
    requirements: dict[str, Any] = {}
    last_sync_at: datetime | None = None
    test_mode: bool


class ConnectOut(BaseModel):
    url: str


class TestCheckoutIn(BaseModel):
    items: list[SaleItemIn] = Field(min_length=1, max_length=20)


class TestCheckoutOut(BaseModel):
    id: uuid.UUID
    url: str
    status: str


def _status(integration: Any, test_mode: bool) -> StripeStatusOut:
    if integration is None:
        return StripeStatusOut(connected=False, test_mode=test_mode)
    return StripeStatusOut(
        connected=True,
        status=integration.status.value,
        account_id=integration.external_id,
        charges_enabled=integration.charges_enabled,
        payouts_enabled=integration.payouts_enabled,
        details_submitted=integration.details_submitted,
        requirements=integration.requirements,
        last_sync_at=integration.last_sync_at,
        test_mode=test_mode,
    )


@router.get("/integrations/stripe", response_model=StripeStatusOut)
async def stripe_status(
    session: DbSession,
    settings: AppSettings,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.WORKSPACE_READ))],
) -> StripeStatusOut:
    return _status(await service.get_integration(session), settings.stripe_test_mode)


@router.post("/integrations/stripe/connect", response_model=ConnectOut)
async def stripe_connect(
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.INTEGRATIONS_PAYMENTS_MANAGE))],
) -> ConnectOut:
    """`/stripe connect` (Owner only): returns a single-use Stripe onboarding link."""
    url = await service.start_onboarding(
        session,
        await registry.payments(session),
        settings,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        email=ctx.principal.user.email,
    )
    return ConnectOut(url=url)


@router.post("/integrations/stripe/refresh", response_model=StripeStatusOut)
async def stripe_refresh(
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.INTEGRATIONS_PAYMENTS_MANAGE))],
) -> StripeStatusOut:
    integration = await service.refresh_account(session, await registry.payments(session))
    return _status(integration, settings.stripe_test_mode)


@router.post("/checkout/test-sessions", status_code=201, response_model=TestCheckoutOut)
async def create_test_checkout(
    body: TestCheckoutIn,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.CHECKOUT_TEST))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """Test mode only: a real Checkout Session (on the seller's account) for free-text items,
    so the checkout -> webhook -> order -> ledger pipeline can be exercised before the
    storefront exists (Phase 3 reuses it)."""

    async def handler() -> tuple[int, dict[str, object]]:
        checkout = await service.create_test_checkout(
            session,
            await registry.payments(session),
            settings,
            workspace_id=ctx.workspace_id,
            actor_user_id=ctx.user_id,
            items=[
                ledger.ItemIn(i.description, i.quantity, i.unit_amount_minor, i.product_key)
                for i in body.items
            ],
        )
        assert checkout.url is not None
        return 201, {"id": str(checkout.id), "url": checkout.url, "status": checkout.status.value}

    return await run_idempotent(
        session,
        workspace_id=ctx.workspace_id,
        key=idempotency_key,
        body=body.model_dump(mode="json"),
        handler=handler,
    )


async def _receive(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    background: BackgroundTasks,
    signature: str | None,
    secret: str,
) -> dict[str, object]:
    payload = await request.body()
    event_id, is_new = await service.ingest(
        session,
        await registry.payments(session),
        payload=payload,
        signature=signature,
        secret=secret,
    )
    if is_new:
        schedule(background, settings, PROCESS_JOB, {"event_id": event_id})
    return {"received": True, "duplicate": not is_new}


@webhooks_router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    background: BackgroundTasks,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, object]:
    """Platform-account events ("Your account" endpoint)."""
    return await _receive(
        request,
        session,
        settings,
        registry,
        background,
        stripe_signature,
        settings.stripe_webhook_secret.get_secret_value(),
    )


@webhooks_router.post("/webhooks/stripe-connect")
async def stripe_connect_webhook(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    background: BackgroundTasks,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, object]:
    """Connected-account events: direct-charge payments, refunds, payouts, account updates."""
    return await _receive(
        request,
        session,
        settings,
        registry,
        background,
        stripe_signature,
        settings.stripe_connect_webhook_secret.get_secret_value(),
    )
