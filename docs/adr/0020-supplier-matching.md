# ADR-0020: Supplier matching and the Verified UK supplier rule

- Status: Accepted (Phase 2c)
- Date: 2026-11-09

## Context
FR-11 adds a Supplier agent after the Marketplace agent. For each candidate it finds a dropship supplier with UK stock and records the unit cost, shipping, delivery range and warehouse. A product can only be matched to a supplier that passes the **Verified UK supplier rule**. A product with no match is flagged "no supplier", one with only failing matches "no verified supplier", and one that would arrive after its retail moment "arrives too late". FR-15 needs two supplier adapters, switched manually, with no automatic failover.

Owner decisions on 2026-11-09:
- **CJ Dropshipping** is the primary supplier, searched through one platform account. Its non-personal catalogue results go in the shared cache.
- **Avasam** is the secondary supplier and works per seller. Its API lists only the products a seller has already sourced, and its terms forbid redistribution and caching. So each workspace connects its own keys, and nothing from Avasam is cached across workspaces.
- **FX:** Frankfurter v1 (ECB reference rates).
- **Matching:** title and attributes only. No image embeddings until the owner approves an embedding provider and pgvector.
- **Live mode** waits for CJ and Avasam to confirm their terms in writing.

## Decision
- **Pipeline:** `season → trend → marketplace → supplier`.
- **Supplier agent:** checks the top `RESEARCH_SUPPLIER_TOP_N` measured candidates, by growth.
- **Port:** `SupplierPort` has `search(keyword, country)` and `freight(variant_id, dest_country)`, and declares `shared_cache`. Results carry the provider, a URL without secrets, the fetch time, the raw response and the cost, so each one is stored as a source snapshot (AC-3).
- **Gateway:** the market-data gateway becomes a general `CachedGateway`:
  - `tool` names the tool call.
  - `shared=False` skips cache reads and writes (Avasam).
  - `monthly_budget=False` skips the paid market-data budget.
  - Data types `supplier_search`, `supplier_freight` and `fx_rate` join the market-data types.
- **Matching (`modules/research/supplier_rules.py`, pure functions):**
  - Confidence is the share of the keyword's tokens found in the item title, ignoring plurals.
  - An item qualifies if its confidence is at least `SUPPLIER_MATCH_MIN_SCORE`, it has UK stock above 0, and its warehouse is GB.
  - The best 3 qualifying items get a GB→GB freight quote. The cheapest *tracked* option is kept.
- **Verified UK supplier rule:**
  - **Checks 1–4:** dropship with no minimum order, automatic stock and cost updates, tracked UK delivery, and a UK returns address with a policy of at least 14 days. Each comes from the newest `supplier_verifications` row per (provider, check). A Platform admin records these rows with an evidence URL. The table is append-only, and with no row a check fails.
  - **Check 5, margin:**
    - The selling price is the median of the in-band GBP offer prices from the Marketplace agent, labelled as an estimate.
    - Net = selling price − unit cost − shipping − Stripe fee − platform fee − returns allowance.
    - The Stripe fee is `STRIPE_FEE_BPS` + `STRIPE_FEE_FIXED_MINOR`, defaulting to 1.5% + 20p, Stripe's published rate for standard UK cards.
    - The platform fee is the workspace's plan rate (ADR-0017).
    - The returns allowance is `SUPPLIER_RETURNS_ALLOWANCE_BPS`, a configurable assumption.
    - It passes when net ≥ `SUPPLIER_MIN_MARGIN_BPS` (2500, i.e. 25%) of the selling price.
    - With no selling price, shipping cost or FX rate, the check is "not checked", which **does not pass**.
  - All money is integer minor units. Fees and allowances round up, against the seller.
- **Statuses:** `matched`, `no_verified_supplier` (with the reasons per check), `no_supplier`, `not_checked` (beyond the top N, or stopped at the cost cap) and `not_available` (provider error, or Avasam not connected). The last two make the step degraded.
- **Arrives too late:** `as_of + delivery_max_days` falls after the candidate's moment date (taken from its category hints, otherwise the window end). Ranking these down is done by FR-3 scoring in 2d.
- **Season calendar (AC-26):** act-by dates use the median `delivery_max_days` of matched suppliers from the workspace's latest finished run, with `delivery_source: "supplier"`. Without one, the assumed delivery days are used, as before.
- **Credentials:** Avasam keys are stored Fernet-encrypted in `integrations.credentials_encrypted`, using `INTEGRATION_ENCRYPTION_KEY`. Production refuses the development key. Keys are never returned by the API.
- **Mock mode:** migration 0007 seeds the global `suppliers` rows. The research tests seed verification rows for the mock providers, and devtools seed the local database.

## Consequences
- In live mode every match is "no verified supplier" until a Platform admin records evidence. That's the safe default.
- Avasam matches stay "no verified supplier" until Avasam's API provides a shipping cost.
- SupplierProduct and Product tables come with listings (2d, Phase 3). For now matches live in `run.demand`, each with its snapshot.
- Supplier calls cost £0 but still go through the meter, so tool calls and the cost cap stay consistent.
