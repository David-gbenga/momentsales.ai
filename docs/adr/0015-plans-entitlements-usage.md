# ADR-0015: Plans, entitlements and usage metering

- Status: Accepted (Phase 1B)
- Date: 2026-11-08

## Context
FR-10 needs plan limits (research runs, live listings, seats), a 14-day trial capped at 3 runs, run packs and pay-per-report, all editable by a Platform admin without a code change. `EntitlementService` (Phase 0) already sits in front of every plan-limited action with an allow-everything policy. Research runs (Phase 2) and listings (Phase 3) don't exist yet.

## Decision
- **Catalogue in the database:**
  - `plans`: code, limits (null = unlimited), features JSON, `annual_bonus_runs`, `platform_fee_bps`, active
  - `plan_prices`: plan, currency, interval, `amount_minor`, Stripe price id
  - `addon_products`: `run_pack` and `single_report`, with run quantity and price
  - Global tables (not tenant data), readable by anyone, writable only through the platform-admin API.
  - Seeded with the PRD placeholders (Lite, Starter, Growth, Pro; GBP only until other countries open).
- **`PlanPolicy`** replaces `UnrestrictedPolicy`; callers don't change.
  - Allowed runs per period = plan runs, +`annual_bonus_runs` on annual plans, or **3 while trialing**.
  - Listings and seats compare live counts with the plan limit. Trials get Growth limits.
- **Metering:**
  - `usage_counters(workspace, period_start, period_end, research_runs_used)` covers the subscription's current period (taken from the subscription item). A new period gets a new row, so counts reset on renewal.
  - `run_credits(workspace, pack_runs, single_runs)` hold balances that don't expire while subscribed: PRD packs, and single reports bought on the plan picker.
- **`consume_run(workspace, key)`:**
  - Locks the workspace's counter row (`SELECT … FOR UPDATE`).
  - Spends plan allowance first, then pack runs, then single runs.
  - Is idempotent per `key`: a stored `run_consumptions` row records which pool paid.
  - `refund_run(key)` returns the run to that pool, for runs that fail before producing a result.
  - Over the limit, `402 plan_limit_reached` (existing shape).
- **Unit-economics guard:** an admin plan edit returns a warning when monthly price ≤ runs × `RUN_COST_CAP_MINOR` × 3. The cap is configured until Phase 2 measures real costs.
- **Platform fee:** Connect checkouts send `application_fee_amount = gross × platform_fee_bps / 10 000` only when the plan's fee is above 0 (default 0).

## Consequences
- Phase 2 calls `consume_run` / `refund_run`; Phase 3 adds a listings count to `PlanPolicy`. Neither changes the API contract.
- Until Phase 2, a mock-only dev route consumes a run, so AC-15 can be shown end to end.
