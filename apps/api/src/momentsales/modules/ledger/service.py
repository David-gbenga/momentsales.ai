"""Ledger service: the only code that writes `ledger_entries` (ADR-0010).

Every write is in the caller's transaction and tenant context. Ledger inserts are
idempotent on (workspace_id, source, source_ref); a duplicate returns None.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, and_, case, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.errors import ProblemError, not_found
from momentsales.core.ids import uuid7
from momentsales.db.ledger import (
    LedgerEntry,
    LedgerType,
    Order,
    OrderChannel,
    OrderItem,
    OrderStatus,
    Receipt,
    Spend,
    SpendCategory,
    SpendReceipt,
    SpendSource,
    SpendVoid,
)
from momentsales.db.models import Touch, TouchCategory
from momentsales.modules.audit import service as audit
from momentsales.modules.ledger.money import BASE_CURRENCY, allocate

UK_TZ = "Europe/London"
# FR-7: P&L = revenue - Stripe fees - refunds - COGS spends - other spends.
# FR-13 shows ad spend as its own line.
COGS_CATEGORIES = frozenset({SpendCategory.STOCK, SpendCategory.SHIPPING})


async def uk_date(session: AsyncSession, moment: datetime) -> date:
    """The UK calendar date of a timestamp (Postgres has the tz database)."""
    value = await session.scalar(select(cast(func.timezone(UK_TZ, moment), Date)))
    assert isinstance(value, date)
    return value


async def post_entry(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    type: LedgerType,
    amount_minor: int,
    currency: str,
    occurred_on: date,
    source: str,
    source_ref: str,
    description: str,
    order_id: uuid.UUID | None = None,
    spend_id: uuid.UUID | None = None,
    product_key: str | None = None,
    reverses_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Insert one ledger entry; returns its id, or None if this (source, source_ref) exists."""
    if amount_minor == 0:
        return None
    stmt = (
        pg_insert(LedgerEntry)
        .values(
            id=uuid7(),
            workspace_id=workspace_id,
            type=type,
            amount_minor=amount_minor,
            currency=currency,
            occurred_on=occurred_on,
            source=source,
            source_ref=source_ref,
            description=description[:255],
            order_id=order_id,
            spend_id=spend_id,
            product_key=product_key,
            reverses_id=reverses_id,
            created_by=created_by,
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "source", "source_ref"])
        .returning(LedgerEntry.id)
    )
    return await session.scalar(stmt)


# --- Orders and sales ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ItemIn:
    description: str
    quantity: int
    unit_amount_minor: int
    product_key: str | None = None

    @property
    def total(self) -> int:
        return self.quantity * self.unit_amount_minor


async def post_order_sale(
    session: AsyncSession,
    order: Order,
    items: list[OrderItem],
    *,
    source: str,
    ref: str,
    occurred_on: date,
    fee_minor: int,
    platform_fee_minor: int = 0,
    created_by: uuid.UUID | None = None,
) -> int:
    """One `sale` entry per item (so P&L can group by product) and the fees spread across
    items in proportion to their value: Stripe's fee and, apart, momentsales.ai's platform
    fee (ADR-0017). Returns the number of entries written."""
    written = 0
    totals = [i.quantity * i.unit_amount_minor for i in items]
    # Any difference between the order gross and the item totals (e.g. shipping or tax
    # collected by Checkout) is recorded as its own unattributed sale line.
    extra = order.gross_minor - sum(totals)
    fees = allocate(fee_minor, totals) if fee_minor else [0] * len(items)
    pfees = allocate(platform_fee_minor, totals) if platform_fee_minor else [0] * len(items)
    for idx, (item, amount, fee, pfee) in enumerate(zip(items, totals, fees, pfees, strict=True)):
        if await post_entry(
            session,
            workspace_id=order.workspace_id,
            type=LedgerType.SALE,
            amount_minor=amount,
            currency=order.currency,
            occurred_on=occurred_on,
            source=source,
            source_ref=f"{ref}:sale:{idx}",
            description=f"Sale: {item.description}",
            order_id=order.id,
            product_key=item.product_key,
            created_by=created_by,
        ):
            written += 1
        if fee and await post_entry(
            session,
            workspace_id=order.workspace_id,
            type=LedgerType.FEE,
            amount_minor=-fee,
            currency=order.currency,
            occurred_on=occurred_on,
            source=source,
            source_ref=f"{ref}:fee:{idx}",
            description=f"Stripe fee: {item.description}",
            order_id=order.id,
            product_key=item.product_key,
            created_by=created_by,
        ):
            written += 1
        if pfee and await post_entry(
            session,
            workspace_id=order.workspace_id,
            type=LedgerType.FEE,
            amount_minor=-pfee,
            currency=order.currency,
            occurred_on=occurred_on,
            source=source,
            source_ref=f"{ref}:pfee:{idx}",
            description=f"Platform fee: {item.description}",
            order_id=order.id,
            product_key=item.product_key,
            created_by=created_by,
        ):
            written += 1
    if extra > 0 and await post_entry(
        session,
        workspace_id=order.workspace_id,
        type=LedgerType.SALE,
        amount_minor=extra,
        currency=order.currency,
        occurred_on=occurred_on,
        source=source,
        source_ref=f"{ref}:sale:extra",
        description="Sale: shipping and other charges",
        order_id=order.id,
        created_by=created_by,
    ):
        written += 1
    return written


async def post_order_refund(
    session: AsyncSession,
    order: Order,
    items: list[OrderItem],
    *,
    source: str,
    ref: str,
    amount_minor: int,
    occurred_on: date,
) -> int:
    """A refund of `amount_minor`, spread across items like the sale."""
    totals = [i.quantity * i.unit_amount_minor for i in items] or [1]
    parts = allocate(amount_minor, totals)
    written = 0
    for idx, part in enumerate(parts):
        item = items[idx] if items else None
        if part and await post_entry(
            session,
            workspace_id=order.workspace_id,
            type=LedgerType.REFUND,
            amount_minor=-part,
            currency=order.currency,
            occurred_on=occurred_on,
            source=source,
            source_ref=f"{ref}:refund:{idx}",
            description=f"Refund: {item.description if item else 'order'}",
            order_id=order.id,
            product_key=item.product_key if item else None,
        ):
            written += 1
    return written


async def post_platform_fee_refund(
    session: AsyncSession,
    order: Order,
    items: list[OrderItem],
    *,
    source: str,
    ref: str,
    amount_minor: int,
    occurred_on: date,
) -> int:
    """Platform fee given back to the seller after a refund (ADR-0017): positive `fee`
    entries, spread across items like the sale."""
    totals = [i.quantity * i.unit_amount_minor for i in items] or [1]
    written = 0
    for idx, part in enumerate(allocate(amount_minor, totals)):
        item = items[idx] if items else None
        if part and await post_entry(
            session,
            workspace_id=order.workspace_id,
            type=LedgerType.FEE,
            amount_minor=part,
            currency=order.currency,
            occurred_on=occurred_on,
            source=source,
            source_ref=f"{ref}:pfee-refund:{idx}",
            description=f"Platform fee refund: {item.description if item else 'order'}",
            order_id=order.id,
            product_key=item.product_key if item else None,
        ):
            written += 1
    return written


async def record_manual_sale(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    channel_label: str,
    sold_on: date,
    items: list[ItemIn],
    order_ref: str | None,
    note: str | None,
    channel: OrderChannel = OrderChannel.MANUAL,
    external_id: str | None = None,
) -> Order:
    """FR-7 /sale: a sale made outside Stripe, in the base currency."""
    gross = sum(i.total for i in items)
    order = Order(
        workspace_id=workspace_id,
        channel=channel,
        external_id=external_id or order_ref or f"manual-{uuid.uuid4()}",
        status=OrderStatus.PAID,
        currency=BASE_CURRENCY,
        gross_minor=gross,
        # Midday UTC is the same calendar day in the UK all year round.
        paid_at=datetime.fromisoformat(f"{sold_on.isoformat()}T12:00:00+00:00"),
        created_by=actor_user_id,
        note=f"{channel_label}: {note}" if note else channel_label,
    )
    session.add(order)
    await session.flush()
    rows = [
        OrderItem(
            workspace_id=workspace_id,
            order_id=order.id,
            description=i.description,
            quantity=i.quantity,
            unit_amount_minor=i.unit_amount_minor,
            product_key=i.product_key,
        )
        for i in items
    ]
    session.add_all(rows)
    await session.flush()
    await post_order_sale(
        session,
        order,
        rows,
        source=channel.value,
        ref=str(order.id),
        occurred_on=sold_on,
        fee_minor=0,
        created_by=actor_user_id,
    )
    await audit.record(
        session,
        action="sale.recorded",
        object_type="order",
        object_id=order.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"channel": channel.value, "gross_minor": gross, "items": len(items)},
    )
    return order


# --- Spends ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpendIn:
    amount_minor: int
    currency: str
    base_amount_minor: int
    tax_minor: int
    spent_on: date
    vendor: str
    category: SpendCategory
    payment_method: str
    product_key: str | None = None
    order_id: uuid.UUID | None = None
    note: str | None = None
    source: SpendSource = SpendSource.MANUAL
    import_id: uuid.UUID | None = None
    receipt_id: uuid.UUID | None = None
    extra_audit: dict[str, Any] = field(default_factory=dict)


async def create_spend(
    session: AsyncSession, *, workspace_id: uuid.UUID, actor_user_id: uuid.UUID, data: SpendIn
) -> Spend:
    if data.category is SpendCategory.ADS and not data.product_key:
        raise ProblemError(
            422,
            "product_required",
            "Product required",
            "Ad spend must name the product it promoted (product key).",
        )
    if data.currency == BASE_CURRENCY and data.base_amount_minor != data.amount_minor:
        raise ProblemError(
            422, "base_amount_mismatch", "Amount mismatch", "GBP spends need no conversion."
        )
    if data.order_id is not None and await session.get(Order, data.order_id) is None:
        raise not_found("order")
    spend = Spend(
        workspace_id=workspace_id,
        amount_minor=data.amount_minor,
        tax_minor=data.tax_minor,
        currency=data.currency,
        base_amount_minor=data.base_amount_minor,
        spent_on=data.spent_on,
        vendor=data.vendor,
        category=data.category,
        payment_method=data.payment_method,
        product_key=data.product_key,
        order_id=data.order_id,
        note=data.note,
        source=data.source,
        import_id=data.import_id,
        created_by=actor_user_id,
    )
    session.add(spend)
    await session.flush()
    await post_entry(
        session,
        workspace_id=workspace_id,
        type=LedgerType.SPEND,
        amount_minor=-data.base_amount_minor,
        currency=BASE_CURRENCY,
        occurred_on=data.spent_on,
        source="spend",
        source_ref=str(spend.id),
        description=f"{data.category.value}: {data.vendor}",
        spend_id=spend.id,
        product_key=data.product_key,
        order_id=data.order_id,
        created_by=actor_user_id,
    )
    if data.receipt_id is not None:
        await link_receipt(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            spend_id=spend.id,
            receipt_id=data.receipt_id,
        )
    await audit.record(
        session,
        action="spend.created",
        object_type="spend",
        object_id=spend.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={
            "amount_minor": data.amount_minor,
            "currency": data.currency,
            "base_amount_minor": data.base_amount_minor,
            "category": data.category.value,
            "vendor": data.vendor,
            "source": data.source.value,
            **data.extra_audit,
        },
    )
    return spend


async def void_spend(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    spend_id: uuid.UUID,
    reason: str,
) -> SpendVoid:
    spend = await session.get(Spend, spend_id)
    if spend is None:
        raise not_found("spend")
    if await session.scalar(select(SpendVoid.id).where(SpendVoid.spend_id == spend_id)):
        raise ProblemError(409, "already_voided", "Already voided", "This spend was voided.")
    original = await session.scalar(
        select(LedgerEntry).where(
            LedgerEntry.source == "spend", LedgerEntry.source_ref == str(spend_id)
        )
    )
    void = SpendVoid(
        workspace_id=workspace_id, spend_id=spend_id, reason=reason, voided_by=actor_user_id
    )
    session.add(void)
    await session.flush()
    await post_entry(
        session,
        workspace_id=workspace_id,
        type=LedgerType.SPEND,
        amount_minor=spend.base_amount_minor,
        currency=BASE_CURRENCY,
        occurred_on=spend.spent_on,
        source="spend_void",
        source_ref=str(spend_id),
        description=f"Void: {spend.category.value}: {spend.vendor}",
        spend_id=spend_id,
        product_key=spend.product_key,
        reverses_id=original.id if original else None,
        created_by=actor_user_id,
    )
    await audit.record(
        session,
        action="spend.voided",
        object_type="spend",
        object_id=spend_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"reason": reason},
    )
    return void


async def link_receipt(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    spend_id: uuid.UUID,
    receipt_id: uuid.UUID,
) -> SpendReceipt:
    if await session.get(Spend, spend_id) is None:
        raise not_found("spend")
    if await session.get(Receipt, receipt_id) is None:
        raise not_found("receipt")
    if await session.scalar(select(SpendReceipt.id).where(SpendReceipt.spend_id == spend_id)):
        raise ProblemError(
            409, "receipt_already_linked", "Receipt already attached", "This spend has a receipt."
        )
    link = SpendReceipt(
        workspace_id=workspace_id,
        spend_id=spend_id,
        receipt_id=receipt_id,
        linked_by=actor_user_id,
    )
    session.add(link)
    await session.flush()
    await audit.record(
        session,
        action="receipt.linked",
        object_type="spend",
        object_id=spend_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"receipt_id": str(receipt_id)},
    )
    return link


# --- Touches ---------------------------------------------------------------------------


async def record_touch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    category: TouchCategory,
    description: str,
    minutes: int,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    note: str | None = None,
) -> Touch:
    touch = Touch(
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        category=category,
        description=f"{description} ({note})" if note else description,
        minutes=minutes,
        object_type=object_type,
        object_id=object_id,
        auto=False,
    )
    session.add(touch)
    await session.flush()
    await audit.record(
        session,
        action="touch.recorded",
        object_type="touch",
        object_id=touch.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"category": category.value, "minutes": minutes},
    )
    return touch


async def touch_summary(
    session: AsyncSession, start: date | None = None, end: date | None = None
) -> dict[str, int]:
    """Human vs AI contribution (FR-7): manual and automatic touches and minutes."""
    day = cast(func.timezone(UK_TZ, Touch.created_at), Date)
    conds = []
    if start:
        conds.append(day >= start)
    if end:
        conds.append(day <= end)
    row = (
        await session.execute(
            select(
                func.count().filter(Touch.auto.is_(False)),
                func.coalesce(func.sum(Touch.minutes).filter(Touch.auto.is_(False)), 0),
                func.count().filter(Touch.auto.is_(True)),
            ).where(*conds)
        )
    ).one()
    return {"manual_count": row[0], "manual_minutes": int(row[1]), "auto_count": row[2]}


# --- Views -----------------------------------------------------------------------------


def missing_receipt_filter() -> Any:
    """Spends with no receipt and not voided."""
    return and_(
        ~select(SpendReceipt.id).where(SpendReceipt.spend_id == Spend.id).exists(),
        ~select(SpendVoid.id).where(SpendVoid.spend_id == Spend.id).exists(),
    )


async def missing_receipts_count(session: AsyncSession) -> int:
    n = await session.scalar(select(func.count(Spend.id)).where(missing_receipt_filter()))
    return int(n or 0)


@dataclass(slots=True)
class PnlLine:
    product_key: str | None
    revenue: int = 0
    fees: int = 0
    refunds: int = 0
    cogs: int = 0
    ads: int = 0
    other_spend: int = 0
    adjustments: int = 0

    @property
    def profit(self) -> int:
        return (
            self.revenue
            + self.fees
            + self.refunds
            + self.cogs
            + self.ads
            + self.other_spend
            + self.adjustments
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_key": self.product_key,
            "revenue_minor": self.revenue,
            "fees_minor": self.fees,
            "refunds_minor": self.refunds,
            "cogs_minor": self.cogs,
            "ads_minor": self.ads,
            "other_spend_minor": self.other_spend,
            "adjustments_minor": self.adjustments,
            "profit_minor": self.profit,
        }


async def pnl(
    session: AsyncSession, start: date, end: date, *, by_product: bool
) -> tuple[PnlLine, list[PnlLine]]:
    """Period P&L in the base currency (payouts excluded: they are cash movements).

    Amounts keep the ledger sign: revenue positive, costs negative, profit = the sum."""
    bucket = case(
        (LedgerEntry.type == LedgerType.SALE, "revenue"),
        (LedgerEntry.type == LedgerType.FEE, "fees"),
        (LedgerEntry.type == LedgerType.REFUND, "refunds"),
        (LedgerEntry.type == LedgerType.ADJUSTMENT, "adjustments"),
        (Spend.category.in_(list(COGS_CATEGORIES)), "cogs"),
        (Spend.category == SpendCategory.ADS, "ads"),
        else_="other_spend",
    )
    keys: list[Any] = [LedgerEntry.product_key] if by_product else []
    rows = (
        await session.execute(
            select(*keys, bucket, func.sum(LedgerEntry.amount_minor))
            .select_from(LedgerEntry)
            .outerjoin(Spend, Spend.id == LedgerEntry.spend_id)
            .where(
                LedgerEntry.occurred_on >= start,
                LedgerEntry.occurred_on <= end,
                LedgerEntry.type != LedgerType.PAYOUT,
                LedgerEntry.currency == BASE_CURRENCY,
            )
            .group_by(*keys, bucket)
        )
    ).all()
    total = PnlLine(None)
    lines: dict[str | None, PnlLine] = {}
    for row in rows:
        key, name, amount = (row[0], row[1], row[2]) if by_product else (None, row[0], row[1])
        value = int(amount or 0)
        setattr(total, name, getattr(total, name) + value)
        if by_product:
            line = lines.setdefault(key, PnlLine(key))
            setattr(line, name, getattr(line, name) + value)
    ordered = sorted(lines.values(), key=lambda line: (line.product_key is None, line.product_key))
    return total, ordered


async def sales_summary(
    session: AsyncSession,
    start: date,
    end: date,
    *,
    period: str,
    product_key: str | None = None,
) -> list[dict[str, Any]]:
    """Sales counters by day/week/month (UK time), from the ledger so refunds and fees
    land in the period they happened."""
    if period not in ("day", "week", "month"):
        raise ValueError(period)
    bucket = func.date_trunc(period, LedgerEntry.occurred_on).label("bucket")
    conds = [
        LedgerEntry.occurred_on >= start,
        LedgerEntry.occurred_on <= end,
        LedgerEntry.type.in_([LedgerType.SALE, LedgerType.FEE, LedgerType.REFUND]),
        LedgerEntry.currency == BASE_CURRENCY,
    ]
    if product_key:
        conds.append(LedgerEntry.product_key == product_key)

    def total(t: LedgerType) -> Any:
        return func.coalesce(func.sum(LedgerEntry.amount_minor).filter(LedgerEntry.type == t), 0)

    rows = (
        await session.execute(
            select(
                bucket,
                func.count(func.distinct(LedgerEntry.order_id)).filter(
                    LedgerEntry.type == LedgerType.SALE
                ),
                total(LedgerType.SALE),
                total(LedgerType.FEE),
                total(LedgerType.REFUND),
            )
            .where(*conds)
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()
    out = []
    for b, orders, gross, fees, refunds in rows:
        out.append(
            {
                "period_start": b.date().isoformat() if hasattr(b, "date") else str(b),
                "orders": int(orders),
                "gross_minor": int(gross),
                "fees_minor": int(fees),
                "refunds_minor": int(refunds),
                "net_minor": int(gross) + int(fees) + int(refunds),
            }
        )
    return out
