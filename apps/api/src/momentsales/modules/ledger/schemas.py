"""Request/response models for the ledger API. Amounts travel as decimal strings
("24.99") and are stored as integer minor units."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from momentsales.db.ledger import SpendCategory
from momentsales.db.models import TouchCategory
from momentsales.modules.ledger.money import BASE_CURRENCY, MoneyError, check_currency, to_minor

PRODUCT_KEY_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"


def _norm_key(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) and v.strip() else (None if v == "" else v)


ProductKey = Annotated[
    str | None, BeforeValidator(_norm_key), Field(default=None, pattern=PRODUCT_KEY_PATTERN)
]
Amount = Annotated[str, Field(pattern=r"^\d{1,12}(\.\d{1,4})?$", examples=["24.99"])]


class SpendCreate(BaseModel):
    amount: Amount
    currency: str = Field(default=BASE_CURRENCY, min_length=3, max_length=3)
    base_amount: Amount | None = Field(
        default=None, description="GBP actually charged; required when currency is not GBP"
    )
    tax_amount: Amount | None = None
    spent_on: date
    vendor: str = Field(min_length=1, max_length=255)
    category: SpendCategory
    payment_method: str = Field(min_length=1, max_length=50)
    product_key: ProductKey = None
    order_id: uuid.UUID | None = None
    receipt_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check(self) -> SpendCreate:
        try:
            self.currency = check_currency(self.currency)
            to_minor(self.amount, self.currency)
            if self.tax_amount is not None:
                to_minor(self.tax_amount, self.currency)
            if self.currency != BASE_CURRENCY:
                if self.base_amount is None:
                    raise ValueError(
                        "base_amount (the GBP amount actually charged) is required for "
                        f"{self.currency} spends; we never guess an exchange rate"
                    )
                to_minor(self.base_amount, BASE_CURRENCY)
        except MoneyError as exc:
            raise ValueError(str(exc)) from exc
        if self.amount and to_minor(self.amount, self.currency) == 0:
            raise ValueError("amount must be greater than zero")
        if self.category is SpendCategory.ADS and not self.product_key:
            raise ValueError("ads spends need a product_key")
        return self

    @property
    def amount_minor(self) -> int:
        return to_minor(self.amount, self.currency)

    @property
    def tax_minor(self) -> int:
        return to_minor(self.tax_amount, self.currency) if self.tax_amount else 0

    @property
    def base_amount_minor(self) -> int:
        if self.currency == BASE_CURRENCY:
            return self.amount_minor
        assert self.base_amount is not None
        return to_minor(self.base_amount, BASE_CURRENCY)


class SpendOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount_minor: int
    tax_minor: int
    currency: str
    base_amount_minor: int
    spent_on: date
    vendor: str
    category: SpendCategory
    payment_method: str
    product_key: str | None
    order_id: uuid.UUID | None
    note: str | None
    source: str
    created_at: datetime
    receipt_id: uuid.UUID | None = None
    voided: bool = False


class SpendPage(BaseModel):
    items: list[SpendOut]
    next_cursor: str | None
    missing_receipts: int


class VoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class LinkReceiptIn(BaseModel):
    receipt_id: uuid.UUID


class TouchCreate(BaseModel):
    category: TouchCategory
    description: str = Field(min_length=1, max_length=500)
    minutes: int = Field(ge=0, le=24 * 60)
    object_type: str | None = Field(default=None, pattern=r"^(order|spend|receipt|export)$")
    object_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _check(self) -> TouchCreate:
        if self.category is TouchCategory.TEAM_AND_PERMISSIONS:
            raise ValueError("team_and_permissions touches are recorded automatically")
        if (self.object_type is None) != (self.object_id is None):
            raise ValueError("object_type and object_id go together")
        return self


class TouchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID
    category: TouchCategory
    description: str
    minutes: int
    object_type: str | None
    object_id: uuid.UUID | None
    auto: bool
    created_at: datetime


class TouchSummary(BaseModel):
    manual_count: int
    manual_minutes: int
    auto_count: int


class TouchPage(BaseModel):
    items: list[TouchOut]
    next_cursor: str | None
    summary: TouchSummary


class SaleItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1, le=10_000)
    unit_amount: Amount
    product_key: ProductKey = None

    @property
    def unit_amount_minor(self) -> int:
        return to_minor(self.unit_amount, BASE_CURRENCY)


class SaleCreate(BaseModel):
    """FR-7 /sale: a sale made outside Stripe (base currency only in Phase 1)."""

    sold_on: date
    channel: str = Field(min_length=1, max_length=50, examples=["Etsy", "Market stall"])
    items: list[SaleItemIn] = Field(min_length=1, max_length=50)
    order_ref: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _check(self) -> SaleCreate:
        if sum(i.quantity * i.unit_amount_minor for i in self.items) <= 0:
            raise ValueError("a sale must be worth more than zero")
        return self


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    amount_minor: int
    currency: str
    occurred_on: date
    order_id: uuid.UUID | None
    spend_id: uuid.UUID | None
    product_key: str | None
    source: str
    reverses_id: uuid.UUID | None
    description: str
    created_at: datetime


class LedgerPage(BaseModel):
    items: list[LedgerEntryOut]
    next_cursor: str | None


class PnlLineOut(BaseModel):
    product_key: str | None
    revenue_minor: int
    fees_minor: int
    refunds_minor: int
    cogs_minor: int
    ads_minor: int
    other_spend_minor: int
    adjustments_minor: int
    profit_minor: int


class PnlOut(BaseModel):
    start: date
    end: date
    currency: str
    total: PnlLineOut
    by_product: list[PnlLineOut] | None


class SalesBucket(BaseModel):
    period_start: str
    orders: int
    gross_minor: int
    fees_minor: int
    refunds_minor: int
    net_minor: int


class SalesSummaryOut(BaseModel):
    period: str
    start: date
    end: date
    currency: str
    buckets: list[SalesBucket]
    totals: SalesBucket


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    description: str
    product_key: str | None
    quantity: int
    unit_amount_minor: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel: str
    external_id: str
    status: str
    customer_email: str | None
    currency: str
    gross_minor: int
    tax_minor: int
    fees_minor: int
    refunded_minor: int
    net_minor: int
    paid_at: datetime | None
    created_at: datetime
    note: str | None
    items: list[OrderItemOut] = []


class OrderPage(BaseModel):
    items: list[OrderOut]
    next_cursor: str | None
