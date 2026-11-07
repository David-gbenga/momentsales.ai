# Stripe Checkout (direct charges) and webhooks

Adapter: `apps/api/src/momentsales/adapters/payments/stripe.py`. Webhook routes: `modules/payments/webhooks.py`. Processing: worker task `process_webhook_event`.

Checked against the official docs on 2026-11-07:
- https://docs.stripe.com/connect/direct-charges (`?platform=web&ui=stripe-hosted`)
- https://docs.stripe.com/connect/direct-charges-fee-payer-behavior
- https://docs.stripe.com/connect/webhooks
- https://docs.stripe.com/api/events/types
- https://docs.stripe.com/api/charges/object

## Checkout Session (direct charge)

| Item | Value |
| --- | --- |
| Endpoint | `POST /v1/checkout/sessions` with header `Stripe-Account: acct_…` (makes it a direct charge on the seller's account) |
| Body (used) | `mode=payment`; `line_items[n][price_data][currency]`, `[product_data][name]`, `[unit_amount]`, `line_items[n][quantity]`; `success_url` (may include `{CHECKOUT_SESSION_ID}`); `cancel_url`; `metadata[workspace_id]`, `metadata[checkout_id]` |
| Platform fee | `payment_intent_data[application_fee_amount]`, positive and less than the charge. Not sent in Phase 1 (fee is 0 until plans set it in 1B) |
| Idempotency | `Idempotency-Key` header on every create call (our checkout id) |

`cancel_url` was not shown in the direct-charges example; it is a documented Checkout Session parameter and is optional. The adapter sends it.

## Webhooks

- **Two endpoints**, each with its own signing secret (`whsec_…`):
  - `/api/v1/webhooks/stripe` — "Your account" scope (`connect=false`). `STRIPE_WEBHOOK_SECRET`.
  - `/api/v1/webhooks/stripe-connect` — "Connected accounts" scope (`connect=true`). `STRIPE_CONNECT_WEBHOOK_SECRET`. Direct-charge events and `account.updated` arrive here, with a top-level `account` field naming the seller.
- Signature: `Stripe-Signature` header verified on the raw request body with the endpoint's secret (SDK `Webhook.construct_event`, default 300 s tolerance).
- Check `livemode`: test-mode deployments reject `livemode=true` events.
- Replay protection: `webhook_events.event_id` is unique; a repeat returns 200 without reprocessing.

Events handled:

| Event | Effect |
| --- | --- |
| `checkout.session.completed` (`payment_status=paid`) | Order `paid`, `sale` ledger entry (+gross), `fee` ledger entry (−Stripe fee from the balance transaction) |
| `checkout.session.async_payment_succeeded` | Same as above for delayed payment methods |
| `checkout.session.async_payment_failed`, `checkout.session.expired` | Checkout record marked failed/expired; no ledger entry |
| `charge.refunded` (includes partial refunds) | `refund` entry per new refund (−amount); order `refunded` / `partially_refunded` |
| `charge.dispute.created` | Order `disputed`; no ledger entry |
| `charge.dispute.funds_withdrawn` / `funds_reinstated` | `adjustment` entries (−/+ amount) |
| `payout.paid` | `payout` entry (cash movement, outside P&L) |
| `account.updated` | Integration status refresh |
| `account.application.deauthorized` | Integration `disconnected` |

Other event types are stored and marked ignored.

## Fees

- The Stripe fee is read from the charge's balance transaction: `GET /v1/charges/{id}?expand[]=balance_transaction` with `Stripe-Account`. We use `balance_transaction.fee` (and `fee_details`) — never a computed rate.
- From the session we reach the charge via `payment_intent` → `latest_charge`. That expand path was not shown on the pages read; the adapter retrieves the PaymentIntent and then the charge in two calls, which uses only documented objects.
- Refunds are created by the seller (in their Dashboard) in Phase 1. Application fees are not refunded automatically (`refund_application_fee`); irrelevant while the fee is 0.

Not verified on the pages read: Stripe's processing-fee treatment on refunds. We record refunds at the refunded amount and do not assume a fee reversal; any fee change appears on later balance transactions.

## Local testing

- Mock mode: `/dev/mock-stripe/*` pages produce events with the same JSON shape, signed with the configured secret, posted to the same endpoints.
- Live test mode: `stripe listen --forward-connect-to localhost:8001/api/v1/webhooks/stripe-connect --forward-to localhost:8001/api/v1/webhooks/stripe`.
- Test card `4242 4242 4242 4242`.
