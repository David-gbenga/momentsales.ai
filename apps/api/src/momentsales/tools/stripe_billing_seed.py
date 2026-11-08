"""Create the Stripe Billing catalogue from the plans tables (FR-10: never by hand).

    uv run python -m momentsales.tools.stripe_billing_seed

Test mode only (live keys are refused). Idempotent: rows that already have a Stripe id are
skipped, so it's safe to run again after a Platform admin adds or reprices a plan. Creates:
- a Product per plan and add-on, and a Price per currency/interval (lookup keys like
  `growth_gbp_month`), storing the ids back in plans/plan_prices/addon_products;
- the `retention_one_month` coupon offered by the cancel survey;
- a Customer Portal configuration (plan changes with period-end downgrades; cancel and pause
  stay in the app). Put the printed id in STRIPE_PORTAL_CONFIGURATION_ID.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from momentsales.adapters.billing.stripe import StripeBillingAdapter
from momentsales.core.config import get_settings
from momentsales.core.db import get_sessionmaker
from momentsales.db.billing import AddonProduct, Plan, PlanPrice


async def seed() -> None:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise SystemExit("Set STRIPE_SECRET_KEY to a test-mode key (sk_test_...).")
    stripe = StripeBillingAdapter.from_key(settings.stripe_secret_key.get_secret_value())
    portal_products: list[dict[str, object]] = []
    async with get_sessionmaker()() as session, session.begin():
        plans = (await session.scalars(select(Plan).order_by(Plan.sort_order))).all()
        for plan in plans:
            if not plan.stripe_product_id:
                plan.stripe_product_id = await stripe.create_product(
                    name=f"momentsales.ai {plan.name}", metadata={"plan_code": plan.code}
                )
                print(f"product {plan.code}: {plan.stripe_product_id}")
            prices = (
                await session.scalars(select(PlanPrice).where(PlanPrice.plan_id == plan.id))
            ).all()
            for price in prices:
                if not price.stripe_price_id:
                    price.stripe_price_id = await stripe.create_price(
                        product_id=plan.stripe_product_id,
                        currency=price.currency,
                        unit_amount=price.amount_minor,
                        interval=price.interval.value,
                        lookup_key=f"{plan.code}_{price.currency.lower()}_{price.interval.value}",
                    )
                    print(
                        f"  price {price.currency} {price.interval.value}: {price.stripe_price_id}"
                    )
            if plan.active:
                portal_products.append(
                    {
                        "product": plan.stripe_product_id,
                        "prices": [p.stripe_price_id for p in prices if p.active],
                    }
                )
        for addon in (await session.scalars(select(AddonProduct))).all():
            if not addon.stripe_product_id:
                addon.stripe_product_id = await stripe.create_product(
                    name=f"momentsales.ai {addon.name}", metadata={"addon": addon.kind.value}
                )
            if not addon.stripe_price_id:
                addon.stripe_price_id = await stripe.create_price(
                    product_id=addon.stripe_product_id,
                    currency=addon.currency,
                    unit_amount=addon.amount_minor,
                    interval=None,
                    lookup_key=f"addon_{addon.kind.value}_{addon.currency.lower()}",
                )
                print(f"add-on {addon.kind.value}: {addon.stripe_price_id}")
    print(f"coupon: {await stripe.ensure_retention_coupon()}")
    config_id = await stripe.create_portal_configuration(
        portal_products, f"{settings.web_base_url.rstrip('/')}/app/billing"
    )
    print(f"Portal configuration: {config_id}  ->  set STRIPE_PORTAL_CONFIGURATION_ID")


def main() -> None:
    if sys.platform == "win32":  # psycopg async needs a selector loop on Windows
        asyncio.run(seed(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(seed())


if __name__ == "__main__":
    main()
