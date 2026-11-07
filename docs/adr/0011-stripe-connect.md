# ADR-0011: Stripe Connect accounts, direct charges and webhook processing

- Status: Accepted (Phase 1)
- Date: 2026-11-07

## Context
FR-6: each seller is paid directly through Stripe Connect, sales count automatically, and webhooks write to the ledger idempotently. The seller, not momentsales.ai, should carry disputes and negative balances, because dropshipping carries delivery and dispute risk the platform can't control.

## Decision
- **Accounts:** Stripe now deprecates `type: "standard"` for new platforms and recommends controller properties. We create Accounts v1 accounts with the Standard-equivalent controller: `losses.payments=stripe`, `fees.payer=account`, `requirement_collection=stripe`, `stripe_dashboard.type=full`. Accounts v2 is in preview and is not used. Details: `docs/integrations/stripe-connect.md`.
- **Onboarding** through Account Links (`account_onboarding`). Completion is read from the account (`details_submitted`, `charges_enabled`, `payouts_enabled`, `requirements`), never from the return URL.
- **Charges** are direct charges: Checkout Sessions created with `Stripe-Account`. Platform fee 0 in Phase 1.
- **Fees** come from the charge's balance transaction, never from a calculated rate.
- **Webhook pipeline:** verify signature on the raw body → insert into `webhook_events` (unique `event_id`, `ON CONFLICT DO NOTHING`) → 200 → Celery `process_webhook_event` resolves the workspace from `account` via the SECURITY DEFINER function `app_workspace_for_stripe_account`, sets tenant context and applies the event in one transaction. A Beat task re-queues unprocessed events every minute; failures retry with backoff and record `last_error`.
- **Test mode only.** Settings refuse `sk_live_`/`rk_live_` keys and `livemode=true` events. Turning on live mode will need an explicit, separately approved production switch.
- **Mock mode** reproduces the same event shapes, signed with the configured secret and posted to the same endpoints, so the processing code is identical in both modes.

## Consequences
- Disputes, refunds and Stripe's fees are the seller's, as in a Standard account. The seller can refund from their own Stripe Dashboard; we learn about it from `charge.refunded`.
- Sales appear within seconds: the webhook returns immediately and the worker writes the ledger (AC-6 target < 10 s).
