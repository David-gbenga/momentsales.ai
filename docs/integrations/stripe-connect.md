# Stripe Connect (seller accounts)

Adapter: `apps/api/src/momentsales/adapters/payments/stripe.py`. Mock: `adapters/payments/mock.py`. Selected by `PROVIDER_PAYMENTS=stripe` with `PROVIDER_MODE=live`. Test mode only (keys starting `sk_test_` / `rk_test_`); settings refuse live keys.

Checked against the official docs on 2026-11-07:
- https://docs.stripe.com/connect/accounts
- https://docs.stripe.com/connect/standard-accounts
- https://docs.stripe.com/connect/migrate-to-controller-properties
- https://docs.stripe.com/connect/accounts-v2
- https://docs.stripe.com/api/accounts/create
- https://docs.stripe.com/api/account_links/create
- https://docs.stripe.com/connect/interactive-platform-guide

## Account model

`type: "standard"` is deprecated for new platforms. Stripe recommends **controller properties** on Accounts v1 (Accounts v2 is in preview). We create v1 accounts with the Standard-equivalent controller (these are also Stripe's defaults, but we send them explicitly so the risk profile is visible in code):

| Parameter | Value | Meaning |
| --- | --- | --- |
| `controller[losses][payments]` | `stripe` | Stripe, not the platform, is liable for unrecovered negative balances |
| `controller[fees][payer]` | `account` | The seller pays Stripe's processing fees |
| `controller[requirement_collection]` | `stripe` | Stripe collects KYC through hosted onboarding |
| `controller[stripe_dashboard][type]` | `full` | Seller gets the full Stripe Dashboard (cannot be changed later) |

Also sent: `country=GB` (only UK is enabled) and `metadata[workspace_id]`.

## Onboarding

| Item | Value |
| --- | --- |
| Create account | `POST /v1/accounts` |
| Onboarding link | `POST /v1/account_links` with `account`, `refresh_url`, `return_url`, `type=account_onboarding` |
| Link response | `url`, `expires_at` (example shows ~300 s after `created`), single use |
| `refresh_url` | Hit when the link expired or was already used; we create a fresh link and redirect |
| `return_url` | Carries no state; never treat it as "onboarding done" |
| Completion | `GET /v1/accounts/{id}` or the `account.updated` event: `details_submitted`, `charges_enabled`, `payouts_enabled`, `requirements.{currently_due, eventually_due, past_due, pending_verification, disabled_reason, current_deadline, errors}` |
| Account links | Never sent outside the app (we redirect the signed-in Owner) |
| Test mode | `return_url`/`refresh_url` may be http only in test mode/sandboxes |

Our status mapping: `pending` (no details submitted), `restricted` (details submitted but charges or payouts disabled, or `past_due` non-empty), `active` (charges and payouts enabled), `disconnected` (`account.application.deauthorized`).

## API version

The docs' v1 examples use `Stripe-Version: 2026-10-28.endive`. The adapter pins the version that the installed `stripe` SDK release targets and records it in `STRIPE_API_VERSION` (see `.env.example`); webhook endpoints must be created with the same version.

Not verified on the pages read: rate limits for account creation, and whether `country` can be omitted.

## Platform fee (application fee, ADR-0017)

Checked on 2026-11-08 against:
- https://docs.stripe.com/api/checkout/sessions/create
- https://docs.stripe.com/connect/direct-charges
- https://docs.stripe.com/api/balance_transactions/object
- https://docs.stripe.com/api/charges/object
- https://docs.stripe.com/api/fee_refunds/create

| Item | Value |
| --- | --- |
| Request the fee | `payment_intent_data[application_fee_amount]` (integer, minor units) on `POST /v1/checkout/sessions` with `mode=payment` and the `Stripe-Account` header (direct charge). It must be positive and below the charge amount, and is capped at the captured amount |
| Fee on the charge | `charge.application_fee` (ID, expandable, nullable) and `charge.application_fee_amount` (integer, nullable). The application fee object is created asynchronously, so the ID can be missing at first |
| Fee split | The connected account's balance transaction has `fee_details[]` with `amount`, `application`, `currency`, `description` and `type`. `type` is one of `application_fee`, `payment_method_passthrough_fee`, `stripe_fee`, `tax` or `withheld_tax`. We treat `application_fee` as the platform fee and everything else in `fee` as Stripe's |
| Refunds | Refunding a direct charge does **not** refund the application fee. We call `POST /v1/application_fees/{id}/refunds` with `amount` on the platform account (no `Stripe-Account` header). Partial refunds are allowed until the fee is fully refunded. We send an `Idempotency-Key` (Stripe's general rule for POST requests; the page doesn't mention it) |
| Alternative | `refund_application_fee=true` on `POST /v1/refunds` refunds the fee in proportion, but only when we create the refund. Sellers refund in their own Dashboard, so we don't use it |

Not verified on the pages read: rate limits for fee refunds.
