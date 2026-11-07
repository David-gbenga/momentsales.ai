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
