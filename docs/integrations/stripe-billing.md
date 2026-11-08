# Stripe Billing (SaaS subscriptions)

Adapter: `apps/api/src/momentsales/adapters/billing/stripe.py` (mock: `adapters/billing/mock.py`). Webhooks arrive on the platform endpoint `/api/v1/webhooks/stripe` (see `stripe-checkout-webhooks.md`) and are processed by `modules/billing/webhooks.py`.

API version: the pinned SDK (`stripe` 16.1.0) uses `2026-10-28.endive`, the version the pages below show. **Test mode only**: live keys are refused at startup.

Checked against the official docs on 2026-11-08 (Markdown versions of each page):
- https://docs.stripe.com/api/checkout/sessions/create (and `?query=subscription_data`, `automatic_tax`, `tax_id_collection`, `customer_update`, `discounts`)
- https://docs.stripe.com/billing/subscriptions/trials
- https://docs.stripe.com/api/customer_portal/configurations/create (`?query=features`), https://docs.stripe.com/api/customer_portal/sessions/create
- https://docs.stripe.com/api/subscriptions/pause, https://docs.stripe.com/api/subscriptions/resume, https://docs.stripe.com/billing/subscriptions/pause-payment
- https://docs.stripe.com/billing/subscriptions/cancel, https://docs.stripe.com/api/subscriptions/update (`?query=discounts`, `cancel_at_period_end`)
- https://docs.stripe.com/api/subscriptions/object, https://docs.stripe.com/api/subscription_items/object, https://docs.stripe.com/api/invoices/object (`?query=parent`, `total_taxes`)
- https://docs.stripe.com/billing/subscriptions/webhooks, https://docs.stripe.com/billing/revenue-recovery/smart-retries
- https://docs.stripe.com/tax/subscriptions
- https://docs.stripe.com/api/products/create, https://docs.stripe.com/api/prices/create, https://docs.stripe.com/api/coupons/create

## Catalogue (seed script, never by hand)

`python -m momentsales.tools.stripe_billing_seed` creates the following from the `plans`, `plan_prices` and `addon_products` tables, and stores the ids back:
- One Product per plan and add-on: `POST /v1/products` with `name` and `metadata[plan_code]`.
- One Price per currency and interval: `POST /v1/prices` with `product`, `currency`, `unit_amount`, `recurring[interval]=month|year`, and `lookup_key` (e.g. `growth_gbp_month`). Add-ons are one-off prices with no `recurring`.
- The "one month off" coupon: `POST /v1/coupons` with `id=retention_one_month`, `percent_off=100`, `duration=once`.
- The Portal configuration (below).

`tax_behavior` is **not set** on prices yet, because the VAT decision is still open (setup guide procedure 3D). Prices are created as `unspecified` until then.

## Trial and subscription checkout (`POST /v1/checkout/sessions`)

| Parameter | Value | Doc note |
| --- | --- | --- |
| `mode` | `subscription` | |
| `line_items[0][price]`, `[quantity]` | plan price id, `1` | |
| `subscription_data[trial_period_days]` | `14` (first trial for the workspace only) | "Has to be at least 1" |
| `payment_method_collection` | `always` (the default) | `if_required` skips the card when nothing is due, so we never send it: the PRD requires a card |
| `subscription_data[metadata][workspace_id]`, `client_reference_id` | workspace id | `client_reference_id` max 200 chars |
| `customer` / `customer_email` | existing customer id, else the Owner's email | |
| `customer_update[address]`, `[name]` | `auto` | "Can only be provided when `customer` is provided" |
| `tax_id_collection[enabled]` | `true` | B2B VAT numbers |
| `automatic_tax[enabled]` | `STRIPE_TAX_ENABLED` (default `false`) | Stripe Tax only collects where the account has registrations; with none, tax is 0 and no error is raised |
| `success_url`, `cancel_url` | Billing page URLs | |
| Idempotency | `Idempotency-Key` header | |

Packs and pay-per-report use `mode=payment` with `line_items[0][price]` set to the add-on price, `customer`, `metadata[workspace_id]`, `metadata[addon]` and `client_reference_id`. They are fulfilled on `checkout.session.completed` with `payment_status=paid`.

## Customer Portal

Configuration (`POST /v1/billing_portal/configurations`, created by the seed):

| Feature | Setting |
| --- | --- |
| `features.subscription_update.enabled` | `true`; `default_allowed_updates=[price]`; `products[]` lists each plan Product with its prices |
| `features.subscription_update.proration_behavior` | `always_invoice` (upgrades charged now, as the PRD requires) |
| `features.subscription_update.schedule_at_period_end.conditions[]` | `decreasing_item_amount`, `shortening_interval`: "When any condition is true, the update will be scheduled at the end of the current period", so downgrades apply at period end |
| `features.subscription_cancel.enabled` | `false`: cancelling goes through our cancel survey |
| `features.subscription_pause.enabled` | `false`: pausing goes through our 1–3 month pause |
| `features.payment_method_update.enabled`, `features.invoice_history.enabled` | `true` |
| `features.customer_update` | `enabled=true`, `allowed_updates=[address, name, tax_id]` |

Session: `POST /v1/billing_portal/sessions` with `customer`, `return_url` and `configuration`.

## Pause (PRD: 1–3 months, research disabled, data kept)

- `POST /v1/subscriptions/{id}/pause` "transition[s] it to the paused status. A paused subscription does not generate invoices and will not advance to new billing periods." It "Cannot pause subscriptions with attached schedules". A pending period-end downgrade may use a schedule, in which case we tell the Owner to wait or remove the pending change.
- The pause endpoint takes no resume date. Our daily billing sweep calls `POST /v1/subscriptions/{id}/resume` when `pause_until` passes. If a resumption invoice fails, the subscription stays `paused` or becomes `past_due` per the resume docs, and we follow the webhook.
- Not used: `pause_collection`. It keeps the subscription `active` and still generates invoices.

## Cancel (after the cancel survey)

- `POST /v1/subscriptions/{id}` with `cancel_at_period_end=true`. Undo with `false` before the period ends. `customer.subscription.deleted` fires when the period ends.
- Retention offer: `discounts[0][coupon]=retention_one_month` on the subscription update. "A populated array overwrites the existing discounts".

## Objects (fields we read)

- **Subscription:** `id`, `customer`, `status` (`incomplete`, `incomplete_expired`, `trialing`, `active`, `past_due`, `canceled`, `unpaid`, `paused`), `trial_end`, `cancel_at_period_end`, `cancel_at`, `metadata.workspace_id`, `items.data[0].price.id`.
- **Billing period:** in this API version `current_period_start` and `current_period_end` are on the **subscription item** (`items.data[0]`), not the subscription.
- **Invoice:** `id`, `number`, `status`, `total`, `currency`, `hosted_invoice_url`, `invoice_pdf`, `billing_reason`, `period_start/end`.
  - The subscription id is at `parent.subscription_details.subscription` (`parent.type=subscription_details`).
  - Tax is the sum of `total_taxes[].amount`.

## Webhooks (platform endpoint)

| Event | Effect |
| --- | --- |
| `customer.subscription.created` / `updated` | Upsert our `subscriptions` row: status mapping below, plan from the price id, period from the item, trial end, cancel flag |
| `customer.subscription.deleted` | Status `canceled` (gated to the plan picker) |
| `customer.subscription.paused` / `resumed` | Status `paused` / from the payload |
| `customer.subscription.trial_will_end` | Stored only: "Sent 3 days before"; our own reminders go on day 10 and 13 (PRD) |
| `invoice.paid` | Store the SaaS invoice; clear `past_due_since`; Stripe status follows via `subscription.updated` |
| `invoice.payment_failed` | Store the invoice; set `past_due_since` if empty; email the Owner; show the banner |
| `checkout.session.completed` | `mode=subscription`: link customer and subscription to the workspace. `mode=payment` with `payment_status=paid`: grant pack or report credits once (unique checkout session id) |

Status mapping: `trialing`, `active` and `paused` map to themselves. `past_due` and `unpaid` map to `past_due`, which our sweep turns into `suspended` 30 days after `past_due_since`. `canceled` and `incomplete_expired` map to `canceled`. `incomplete` maps to `incomplete` (gated).

**Dashboard settings to make in the test account** (README):
- Smart Retries: "Leave the subscription past-due", so our 30-day `suspended` rule applies.
- Trial reminder emails are ours. Stripe-hosted reminders "are sent 7 days before the trial ends" if switched on.
