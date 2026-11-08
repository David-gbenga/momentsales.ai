# ADR-0017: Platform fee on seller sales

- Status: Accepted (Phase 1B follow-up)
- Date: 2026-11-08

## Context
The product owner set the platform fee on seller sales (PRD FR-10, 2026-11-08):

| Seller | Fee |
| --- | --- |
| Lite | 2% |
| Starter, Growth, Pro | 0% |
| No subscription, or cancelled (pay-per-report) | 2% |

The fee covers Stripe Connect costs on the cheapest tiers. They also decided that when a seller refunds a shopper, momentsales.ai **gives back its fee in proportion to the refund**.

Sales are direct charges on the seller's Stripe account (ADR-0011). Two Stripe behaviours shape the design (docs/integrations/stripe-connect.md):
- The connected account's balance transaction `fee` includes the application fee.
- Refunding a charge does **not** refund the application fee.

## Decision
- **Rate source:** `plans.platform_fee_bps` for any current subscriber: trialing, active, or past due, paused or suspended. A failed card retry must never raise a paying seller's fee. (Confirmed by the product owner, 2026-11-08.) A trial uses the trial plan (Growth, 0%). A workspace with no subscription, or a cancelled or incomplete one, uses `PLATFORM_FEE_BPS_NO_PLAN` (default 200). Migration `0004` sets Lite to 200. Platform admin can change plan rates through `/admin/plans`.
- **Amount at checkout:** `gross × bps // 10 000`, rounded down so a seller is never over-charged. It is sent as `payment_intent_data.application_fee_amount` only when above 0, and stored on the checkout session.
- **Ledger uses Stripe's figures, never ours:**
  - `fee_details` entries of type `application_fee` become the platform fee; the rest of `fee` is Stripe's own fee.
  - The sale posts separate `fee` entries: "Stripe fee: …" and "Platform fee: …", both spread across items.
  - `orders.fees_minor` is the total of both; `orders.platform_fee_minor` holds the platform part.
- **Refunds:**
  - On `charge.refunded`, the target refund is `platform_fee × refunded ÷ gross`, rounded down; a full refund gives back the whole fee.
  - Before refunding, we read the fee's `amount_refunded` from Stripe (`GET /v1/application_fees/{id}`). We then send only `target − max(Stripe's refunded, orders.platform_fee_refunded_minor)` through `POST /v1/application_fees/{id}/refunds` on the platform account, with idempotency key `appfee-refund-{fee}-{target}`. This stays safe even after Stripe's 24-hour idempotency window, for example when a refund succeeded at Stripe but our commit rolled back.
  - A positive "Platform fee refund" `fee` entry is posted.
  - Totals are cumulative, so a replayed or out-of-order event refunds nothing twice.
  - If the Stripe call fails, the whole event fails and the webhook job retries it; the ledger writes roll back with it.
  - We refund through the fee endpoint, not `refund_application_fee=true`, because sellers refund in their own Stripe Dashboard and we only hear about it through the webhook.

## Consequences
- The pricing page and plan picker show each plan's fee from the `plans` table; the pay-per-report line shows the no-plan fee.
- Stripe's own fee is never refunded, so a fully refunded sale still costs the seller that fee, as before.
- Phase 3's storefront checkout reuses the same rate and amount functions (`modules/billing/entitlements.py`).
