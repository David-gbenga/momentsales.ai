# ADR-0007: Feature flags and the provider registry

- Status: Accepted (Phase 0)
- Date: 2026-11-07

## Context
Only the UK is selectable at launch, and Later-roadmap features must stay hidden. Countries, channels and fulfilment types must switch on without rewrites. FR-15 needs two adapters per critical category, switchable by config, and the app must run with `PROVIDER_MODE=mock` and no keys.

## Decision
**Feature flags**
- Table `feature_flags` (global default) plus `feature_flag_overrides` (per workspace, RLS-scoped). Redis caches effective flags for 30 seconds, with explicit invalidation.
- Countries are flags: `country.GB` is on, and `country.US`, `AU`, `IE`, `DE`, `FR`, `ES`, `IT`, `NL` are off. `GET /countries` returns only enabled ones, and workspace creation rejects others (`422 country_not_available`).
- Later-roadmap flags exist and are off: `module.audience_research` (L1), `connector.shopify` (L2), `connector.ads` (L4), `fulfilment.own_stock`, `fulfilment.print_on_demand` (L5), `platform.mobile_app` (L6), `growth.referrals`, `store.custom_domains` (L7).

**Provider registry**
- Each category resolves `primary`, `secondary`, `mode` (mock or live) and `failover` (auto or manual) from the environment (`PROVIDER_MODE`, `PROVIDER_EMAIL`, `PROVIDER_EMAIL_SECONDARY`, `PROVIDER_EMAIL_FAILOVER`, ...). A `provider_configs` DB row, set by a platform admin, overrides the environment. Production never resolves mock.
- Phase 0 categories with adapters: **email** (Resend primary, Postmark secondary, mock outbox) and **identity** (Google, mock IdP). `market_data`, `supplier`, `ocr` and `llm` are registered with no adapters yet. They arrive in Phases 1 and 2.
- Email failover is automatic on retryable errors (network, 429, 5xx) and never on 4xx. Supplier orders (Phase 2) will use manual failover, as FR-15 requires.

## Consequences
- `GET /admin/providers` (platform admin, read-only) shows the resolved configuration. Editing comes with the admin console (Phase 4).
- Every adapter has recorded-fixture contract tests and is documented in `docs/integrations/`.
