"""Ledger endpoints: /spends, /touches, /sales, /orders, /ledger, /pnl (FR-7).

Money writes accept an `Idempotency-Key` header (ADR-0010).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from momentsales.core.errors import ProblemError, not_found
from momentsales.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, page, paginate
from momentsales.db.ledger import (
    LedgerEntry,
    LedgerType,
    Order,
    OrderItem,
    Spend,
    SpendReceipt,
    SpendVoid,
)
from momentsales.db.models import Touch
from momentsales.modules.ledger import service
from momentsales.modules.ledger.idempotency import run_idempotent
from momentsales.modules.ledger.money import BASE_CURRENCY
from momentsales.modules.ledger.schemas import (
    LedgerEntryOut,
    LedgerPage,
    LinkReceiptIn,
    OrderItemOut,
    OrderOut,
    OrderPage,
    PnlLineOut,
    PnlOut,
    SaleCreate,
    SalesBucket,
    SalesSummaryOut,
    SpendCreate,
    SpendOut,
    SpendPage,
    TouchCreate,
    TouchOut,
    TouchPage,
    TouchSummary,
    VoidIn,
)
from momentsales.tenancy.deps import DbSession, WorkspaceContext, require
from momentsales.tenancy.permissions import Action, is_allowed

router = APIRouter(tags=["ledger"])

IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
MAX_RANGE_DAYS = 400


async def _range(session: DbSession, start: date | None, end: date | None) -> tuple[date, date]:
    """Defaults to the current UK month to date."""
    end = end or await service.uk_date(session, datetime.now(UTC))
    start = start or end.replace(day=1)
    if start > end:
        raise ProblemError(422, "invalid_range", "Invalid date range", "start must be <= end.")
    if (end - start) > timedelta(days=MAX_RANGE_DAYS):
        raise ProblemError(422, "range_too_long", "Range too long", "Use 400 days or fewer.")
    return start, end


async def _spend_out(session: DbSession, spends: list[Spend]) -> list[SpendOut]:
    ids = [s.id for s in spends]
    receipts = (
        dict(
            (
                await session.execute(
                    select(SpendReceipt.spend_id, SpendReceipt.receipt_id).where(
                        SpendReceipt.spend_id.in_(ids)
                    )
                )
            ).all()
        )
        if ids
        else {}
    )
    voided = (
        set(
            (
                await session.scalars(select(SpendVoid.spend_id).where(SpendVoid.spend_id.in_(ids)))
            ).all()
        )
        if ids
        else set()
    )
    out = []
    for s in spends:
        item = SpendOut.model_validate(s)
        item.receipt_id = receipts.get(s.id)
        item.voided = s.id in voided
        out.append(item)
    return out


# --- Spends ----------------------------------------------------------------------------


@router.post("/spends", status_code=201, response_model=SpendOut)
async def create_spend(
    body: SpendCreate,
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_WRITE))],
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    async def handler() -> tuple[int, dict[str, object]]:
        spend = await service.create_spend(
            session,
            workspace_id=ctx.workspace_id,
            actor_user_id=ctx.user_id,
            data=service.SpendIn(
                amount_minor=body.amount_minor,
                currency=body.currency,
                base_amount_minor=body.base_amount_minor,
                tax_minor=body.tax_minor,
                spent_on=body.spent_on,
                vendor=body.vendor,
                category=body.category,
                payment_method=body.payment_method,
                product_key=body.product_key,
                order_id=body.order_id,
                note=body.note,
                receipt_id=body.receipt_id,
            ),
        )
        (out,) = await _spend_out(session, [spend])
        return 201, out.model_dump(mode="json")

    return await run_idempotent(
        session,
        workspace_id=ctx.workspace_id,
        key=idempotency_key,
        body=body.model_dump(mode="json"),
        handler=handler,
    )


@router.get("/spends", response_model=SpendPage)
async def list_spends(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_READ))],
    cursor: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    missing_receipt: bool = False,
) -> SpendPage:
    stmt = select(Spend)
    if missing_receipt:
        stmt = stmt.where(service.missing_receipt_filter())
    rows = list((await session.scalars(paginate(stmt, Spend.id, cursor, limit))).all())
    items, next_cursor = page(rows, limit)
    return SpendPage(
        items=await _spend_out(session, items),
        next_cursor=next_cursor,
        missing_receipts=await service.missing_receipts_count(session),
    )


@router.post("/spends/{spend_id}/void", status_code=201)
async def void_spend(
    spend_id: uuid.UUID,
    body: VoidIn,
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_WRITE))],
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    async def handler() -> tuple[int, dict[str, object]]:
        void = await service.void_spend(
            session,
            workspace_id=ctx.workspace_id,
            actor_user_id=ctx.user_id,
            spend_id=spend_id,
            reason=body.reason,
        )
        return 201, {"spend_id": str(void.spend_id), "voided_at": void.created_at.isoformat()}

    return await run_idempotent(
        session,
        workspace_id=ctx.workspace_id,
        key=idempotency_key,
        body={"spend_id": str(spend_id), **body.model_dump(mode="json")},
        handler=handler,
    )


@router.post("/spends/{spend_id}/receipt", status_code=201)
async def link_receipt(
    spend_id: uuid.UUID,
    body: LinkReceiptIn,
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.RECEIPTS_UPLOAD))],
) -> dict[str, str]:
    await service.link_receipt(
        session,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        spend_id=spend_id,
        receipt_id=body.receipt_id,
    )
    return {"spend_id": str(spend_id), "receipt_id": str(body.receipt_id)}


# --- Touches ---------------------------------------------------------------------------


@router.post("/touches", status_code=201, response_model=TouchOut)
async def create_touch(
    body: TouchCreate,
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.TOUCHES_WRITE))],
) -> TouchOut:
    touch = await service.record_touch(
        session,
        workspace_id=ctx.workspace_id,
        actor_user_id=ctx.user_id,
        category=body.category,
        description=body.description,
        minutes=body.minutes,
        object_type=body.object_type,
        object_id=body.object_id,
        note=body.note,
    )
    return TouchOut.model_validate(touch)


@router.get("/touches", response_model=TouchPage)
async def list_touches(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_READ))],
    cursor: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    auto: bool | None = None,
) -> TouchPage:
    stmt = select(Touch)
    if auto is not None:
        stmt = stmt.where(Touch.auto.is_(auto))
    rows = list((await session.scalars(paginate(stmt, Touch.id, cursor, limit))).all())
    items, next_cursor = page(rows, limit)
    return TouchPage(
        items=[TouchOut.model_validate(t) for t in items],
        next_cursor=next_cursor,
        summary=TouchSummary(**await service.touch_summary(session)),
    )


# --- Sales and orders ------------------------------------------------------------------


def _order_out(order: Order, items: list[OrderItem]) -> OrderOut:
    out = OrderOut(
        id=order.id,
        channel=order.channel.value,
        external_id=order.external_id,
        status=order.status.value,
        customer_email=order.customer_email,
        currency=order.currency,
        gross_minor=order.gross_minor,
        tax_minor=order.tax_minor,
        fees_minor=order.fees_minor,
        refunded_minor=order.refunded_minor,
        net_minor=order.gross_minor - order.fees_minor - order.refunded_minor,
        paid_at=order.paid_at,
        created_at=order.created_at,
        note=order.note,
        items=[OrderItemOut.model_validate(i) for i in items],
    )
    return out


@router.post("/sales", status_code=201, response_model=OrderOut)
async def record_sale(
    body: SaleCreate,
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.SALES_RECORD))],
    idempotency_key: IdemKey = None,
) -> JSONResponse:
    async def handler() -> tuple[int, dict[str, object]]:
        order = await service.record_manual_sale(
            session,
            workspace_id=ctx.workspace_id,
            actor_user_id=ctx.user_id,
            channel_label=body.channel,
            sold_on=body.sold_on,
            items=[
                service.ItemIn(i.description, i.quantity, i.unit_amount_minor, i.product_key)
                for i in body.items
            ],
            order_ref=body.order_ref,
            note=body.note,
        )
        items = list(
            (await session.scalars(select(OrderItem).where(OrderItem.order_id == order.id))).all()
        )
        await session.refresh(order)
        return 201, _order_out(order, items).model_dump(mode="json")

    return await run_idempotent(
        session,
        workspace_id=ctx.workspace_id,
        key=idempotency_key,
        body=body.model_dump(mode="json"),
        handler=handler,
    )


@router.get("/orders", response_model=OrderPage)
async def list_orders(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.ORDERS_READ))],
    cursor: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    channel: str | None = None,
) -> OrderPage:
    stmt = select(Order)
    if channel:
        stmt = stmt.where(Order.channel == channel)
    rows = list((await session.scalars(paginate(stmt, Order.id, cursor, limit))).all())
    orders, next_cursor = page(rows, limit)
    ids = [o.id for o in orders]
    items: dict[uuid.UUID, list[OrderItem]] = {i: [] for i in ids}
    if ids:
        for item in (
            await session.scalars(select(OrderItem).where(OrderItem.order_id.in_(ids)))
        ).all():
            items[item.order_id].append(item)
    return OrderPage(items=[_order_out(o, items[o.id]) for o in orders], next_cursor=next_cursor)


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: uuid.UUID,
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.ORDERS_READ))],
) -> OrderOut:
    order = await session.get(Order, order_id)
    if order is None:
        raise not_found("order")
    items = list(
        (await session.scalars(select(OrderItem).where(OrderItem.order_id == order_id))).all()
    )
    return _order_out(order, items)


@router.get("/sales/summary", response_model=SalesSummaryOut)
async def sales_summary(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.ORDERS_READ))],
    period: Literal["day", "week", "month"] = "day",
    start: date | None = None,
    end: date | None = None,
    product_key: str | None = None,
) -> SalesSummaryOut:
    start, end = await _range(session, start, end)
    buckets = await service.sales_summary(
        session, start, end, period=period, product_key=product_key
    )
    totals = {
        k: sum(int(b[k]) for b in buckets)
        for k in ("orders", "gross_minor", "fees_minor", "refunds_minor", "net_minor")
    }
    return SalesSummaryOut(
        period=period,
        start=start,
        end=end,
        currency=BASE_CURRENCY,
        buckets=[SalesBucket(**b) for b in buckets],
        totals=SalesBucket(period_start=start.isoformat(), **totals),
    )


# --- Ledger views ----------------------------------------------------------------------


@router.get("/ledger", response_model=LedgerPage)
async def list_ledger(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_READ))],
    cursor: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    type: LedgerType | None = None,
    start: date | None = None,
    end: date | None = None,
) -> LedgerPage:
    stmt = select(LedgerEntry)
    if type is not None:
        stmt = stmt.where(LedgerEntry.type == type)
    if not is_allowed(ctx.role, Action.PAYOUTS_READ):
        stmt = stmt.where(LedgerEntry.type != LedgerType.PAYOUT)
    if start:
        stmt = stmt.where(LedgerEntry.occurred_on >= start)
    if end:
        stmt = stmt.where(LedgerEntry.occurred_on <= end)
    rows = list((await session.scalars(paginate(stmt, LedgerEntry.id, cursor, limit))).all())
    items, next_cursor = page(rows, limit)
    return LedgerPage(
        items=[LedgerEntryOut.model_validate(e) for e in items], next_cursor=next_cursor
    )


@router.get("/pnl", response_model=PnlOut)
async def get_pnl(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_READ))],
    start: date | None = None,
    end: date | None = None,
    group_by: Literal["product_key"] | None = None,
) -> PnlOut:
    start, end = await _range(session, start, end)
    total, lines = await service.pnl(session, start, end, by_product=group_by == "product_key")
    return PnlOut(
        start=start,
        end=end,
        currency=BASE_CURRENCY,
        total=PnlLineOut(**total.as_dict()),
        by_product=[PnlLineOut(**line.as_dict()) for line in lines] if group_by else None,
    )


@router.get("/product-keys", response_model=list[str])
async def product_keys(
    session: DbSession,
    ctx: Annotated[WorkspaceContext, Depends(require(Action.LEDGER_READ))],
) -> list[str]:
    """Keys already used, for autocomplete (ADR-0013)."""
    keys = await session.scalars(
        select(LedgerEntry.product_key)
        .where(LedgerEntry.product_key.is_not(None))
        .group_by(LedgerEntry.product_key)
        .order_by(func.max(LedgerEntry.created_at).desc())
        .limit(200)
    )
    return [k for k in keys.all() if k]
