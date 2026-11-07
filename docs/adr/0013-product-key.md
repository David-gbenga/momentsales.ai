# ADR-0013: Interim `product_key` for per-product P&L

- Status: Accepted (Phase 1)
- Date: 2026-11-07

## Context
FR-13 asks for per-product P&L and requires `/spend ads` to link to a listing. Listings arrive in Phase 3, but the ledger and P&L ship in Phase 1.

## Decision
- Order items, spends and ledger entries carry an optional `product_key`: a seller-chosen, SKU-like string (`[a-z0-9-]{1,64}`, lower-cased).
- `/spend ads` requires a `product_key` in Phase 1 (database CHECK constraint).
- Per-product P&L groups by `product_key`.
- In Phase 3, each listing gets a unique `product_key`, and `listing_id` columns are added (expand-only). Existing rows join to listings through the key, so nothing is rewritten.

## Consequences
- Sellers can track true profit per product before listings exist, for example for products sold through test checkouts or manual sales.
- A typo in a key creates a separate product line; the UI offers autocomplete from keys already used.
