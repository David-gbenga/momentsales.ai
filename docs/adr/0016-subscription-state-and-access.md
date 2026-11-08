# ADR-0016: Subscription state machine, billing webhooks and access rules

- Status: Accepted (Phase 1B)
- Date: 2026-11-08

## Context
Stripe Billing owns the subscription; the app needs a local, tenant-scoped copy to decide what each workspace may do, plus dunning (past due, then suspended after 30 days), pause and cancel flows. Details of the Stripe objects are in `docs/integrations/stripe-billing.md`.

## Decision
- **One local `subscriptions` row per workspace** (RLS), updated only from verified webhooks and our own billing actions.
  - Statuses: `incomplete`, `trialing`, `active`, `past_due`, `suspended`, `paused`, `canceled`.
  - `suspended` is ours: the daily sweep sets it 30 days after `past_due_since`. Paying an invoice restores the Stripe status.
- **Webhooks** arrive on the existing platform endpoint `/webhooks/stripe`, not a separate `/webhooks/stripe-billing`.
  - They reuse the Phase 1 pipeline: raw-body signature check, `livemode` refused, stored once by event id, processed in a job.
  - Handlers are idempotent and ignore events older than the row's last applied Stripe event time (`event.created`), so out-of-order delivery can't regress state.
- **Access gate** (`require_subscription`, applied to every workspace route except billing, workspace settings and read-only GETs):

| State | Owner/Admin writes | Plan-limited actions | Webhook-driven ledger writes |
| --- | --- | --- | --- |
| trialing, active | allowed | per plan | allowed |
| past_due, suspended, paused | blocked (`403 subscription_read_only`), except billing | blocked | allowed (the storefront keeps selling; suspended pauses the storefront in Phase 3) |
| none, incomplete, canceled | blocked (`403 subscription_required`); the app shows the plan picker | blocked | allowed |

- **The Owner alone** starts checkout, opens the Portal, buys packs, pauses and cancels. Admins see plan and usage, but not invoices or card details (PRD role rules).
- **Daily billing sweep (Beat):**
  - trial reminder emails on trial day 10 and 13 (once each, stored)
  - `past_due` → `suspended` after 30 days
  - resume paused subscriptions at `pause_until`
- **Retention:** deleting suspended workspaces' data after 90 days is a Phase 5 retention job; until then data is kept.

## Consequences
- Every new write route must sit behind the gate; a test enumerates the routes to prove it.
- The demo seed and test helpers create a mock `active` subscription so existing flows keep working.
