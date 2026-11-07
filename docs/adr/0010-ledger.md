# ADR-0010: Append-only ledger and idempotent money writes

- Status: Accepted (Phase 1)
- Date: 2026-11-07

## Context
FR-7 and FR-8 need a ledger the seller's accountant will accept: every sale, fee, refund, payout and spend, never edited, with corrections as reversing entries. Webhooks are delivered at least once and API clients retry, so every money write must be idempotent (AC-6).

## Decision
- **Single-entry, signed amounts.** `ledger_entries.amount_minor` is a signed `bigint` in minor units with a `currency`. Positive = money in, negative = money out. Types: `sale` (+), `fee` (−), `refund` (−), `spend` (−), `adjustment` (±), `payout` (−, cash leaving the Stripe balance). Double-entry is unnecessary for a seller-facing P&L and can be derived later.
- **P&L** = sum of `sale`, `fee`, `refund`, `spend`, `adjustment` entries for a period (optionally by `product_key`). `payout` entries are cash movements and are excluded. Spends contribute their GBP `base_amount_minor`.
- **Append-only**, enforced like the audit log (ADR-0008): no UPDATE/DELETE grant for `app_rw`, no UPDATE/DELETE RLS policy, and triggers that raise. Applies to `ledger_entries`, `spends`, `spend_voids` and `spend_receipts`.
- **Corrections** are new entries with `reverses_id` pointing at the original. Voiding a spend inserts a `spend_voids` row plus a reversing ledger entry, in one transaction.
- **Idempotency, two layers:**
  1. `UNIQUE(workspace_id, source, source_ref)` on `ledger_entries`, e.g. `source=stripe`, `source_ref=evt_…:sale`. Inserts use `ON CONFLICT DO NOTHING`.
  2. `Idempotency-Key` header on every money-writing API call, stored in `idempotency_keys` with a SHA-256 of the request body. Same key + same body → the stored response; same key + different body → 422 `idempotency_key_reused`. Keys are scoped to the workspace and kept 24 hours.
- **Currency:** the workspace base currency is GBP. A spend in another currency must carry the GBP amount actually charged (`base_amount_minor`), entered by the user. No exchange rate is fetched or guessed.

## Consequences
- Balances and P&L are computed with `SUM` queries over indexed `(workspace_id, occurred_on)`; fine at MVP scale, and a materialised summary can be added later without changing the write path.
- Mistakes are visible forever as reversal pairs, which is what an auditor expects.
