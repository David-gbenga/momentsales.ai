# Changelog

All notable changes are recorded here, by phase.

## [Phase 2c — UK supplier matching] — 2026-11-09

### Added
- **Supplier agent (FR-11, ADR-0020):** the fourth pipeline step. For the top 10 measured searches it:
  - searches the configured supplier
  - keeps matching items with UK stock in a GB warehouse
  - quotes GB-to-GB freight and keeps the cheapest tracked option
  - converts costs to GBP with a stored, dated FX rate
  - applies the **Verified UK supplier rule**
- **Statuses:**
  - Each search shows "verified supplier", "no verified supplier" (with the failed checks), "no supplier", "not checked" or "not available".
  - "Arrives too late" is flagged when the slowest delivery lands after the search's retail moment.
- **Verified UK supplier rule:**
  - **Checks 1–4** (dropshipping with no minimum order, automatic stock and cost updates, tracked UK delivery, UK returns of at least 14 days) come from admin-recorded evidence. With no evidence a check fails.
  - **Check 5** is a 25% net margin floor. It counts unit cost, shipping, Stripe's UK card fee (1.5% + 20p), the plan's platform fee and a returns allowance, against the median price seen on Google. With any input missing, the check fails.
- **Adapters:**
  - `SupplierPort` with CJ Dropshipping (primary: one platform account, Redis-cached token, USD prices) and Avasam (secondary: each seller's own keys, never in the shared cache).
  - `FxPort` with Frankfurter (ECB rates).
  - Deterministic mocks for both.
- **Shared gateway:** the market-data cache gateway is now `CachedGateway`. CJ results and FX rates are reused across workspaces for 24 hours. Avasam data is never cached.
- **Migration `0007`:**
  - `suppliers` (global, seeded with CJ and Avasam)
  - append-only `supplier_verifications`
  - `integrations.credentials_encrypted`
  - the `avasam` integration type
- **API:**
  - `GET/POST /admin/supplier-verifications` and `GET /admin/suppliers` (Platform admin, audit-logged).
  - `GET/POST/DELETE /integrations/avasam` (Owner or Admin can connect; keys are Fernet-encrypted with `INTEGRATION_ENCRYPTION_KEY` and never returned).
  - Typed `SupplierResultOut` and `SupplierMatchOut` on each candidate.
  - A new permission, `integrations.suppliers.manage`.
- **Season calendar (AC-26):** act-by dates use the matched suppliers' median delivery time from the latest run (`delivery_source: "supplier"`).
- **Web:**
  - A Supplier column in the rising-searches table: landed cost, delivery days, warehouse, estimated margin, FX rate, and badges with reasons.
  - An Avasam card on Integrations.
  - The calendar says where its delivery time comes from.
- **Settings:** `PROVIDER_SUPPLIER*`, `CJ_*`, `AVASAM_*`, `PROVIDER_FX`, `FRANKFURTER_BASE_URL`, `RESEARCH_SUPPLIER_TOP_N`, `SUPPLIER_*`, `STRIPE_FEE_*` and `INTEGRATION_ENCRYPTION_KEY`, all in `.env.example`. Production refuses the development encryption key.
- **Docs:** ADR-0020, `docs/integrations/cj-dropshipping.md`, `avasam.md` and `frankfurter.md`, with draft emails to CJ and Avasam.

### Changed
- **Free calls are left out of spend by provider:** supplier, FX and cache hits no longer add zero entries to `cost_by_provider`.

### Tests
- **Contract:** CJ (token caching, search and GB stock, freight, the envelope and error codes), Avasam (token, local keyword filter, published tier, errors) and Frankfurter.
- **Unit:** match confidence, qualifying items, the best tracked option, the estimated selling price, the margin maths and its rounding, verification results, the moment deadline and arrives too late.
- **Integration:**
  - AC-20 matches with snapshots for the search, freight and FX
  - untracked, low-margin and non-UK items
  - no evidence gives "no verified supplier"
  - AC-34 with Avasam, both not connected and connected, with no cache rows written
  - a supplier error doesn't fail over
  - the CJ cache is shared, while each run keeps its own snapshots
  - admin-only, append-only verifications
  - encrypted, workspace-scoped Avasam keys
  - AC-26 calendar
- **Playwright:** the research journey shows the UK suppliers step and the Supplier column, with axe.

### Still mocked
- Suppliers (CJ and Avasam) and FX until live mode. Live suppliers wait for written confirmation from CJ and Avasam and for admin-recorded evidence.
- Avasam's API has no shipping cost, so Avasam matches can't pass the margin check until Avasam confirms a source.
- Scoring, compliance and the Top 10 report arrive in Phase 2d.

## [Phase 2b — market data, rising searches and competition] — 2026-11-08

### Added
- **Market data (ADR-0019):**
  - A provider-neutral `MarketDataPort`: Google Trends interest over time, rising related queries, and a Google results overview.
  - DataForSEO (primary) and SerpApi (secondary) adapters, with automatic failover on retryable errors (FR-15, AC-34), plus a deterministic mock.
  - The SerpApi key is never stored in snapshots.
- **Trend agent:**
  - Candidates come from the run's category, the audience interests and UK seed terms for the moments in the window, plus rising related searches.
  - It works out a growth rate (last 3 weeks against the 6 before), a "crowded" flag (at or near the 12-month high) and last year's lift over the window.
- **Marketplace agent:** a competition score from 0 to 100 (lower is better), from Google ads, shopping ads, distinct sellers, price spread and the results count. Shopping offers are kept as demand evidence and purchase links only.
- **Shared market-data cache:** global and append-only, with no workspace data. A result is reused for 24 hours across workspaces at no cost, and each run still gets its own source snapshots (AC-3).
- **Cost metering in GBP micros:**
  - New columns `cost_micros` and `cost_by_provider`. `cost_minor` is still the total rounded up.
  - An agent that reaches the £1.00 cap keeps the data it already paid for, and the run ends as `partial`.
  - A platform-wide monthly market-data budget (£40) switches runs to cache only once it's reached.
- **Migration `0006`:** `market_data_cache`, `category_seed_terms` (seeded with UK terms), `research_runs.demand`, `cost_micros` and `cost_by_provider`.
- **API:** `RunOut.demand` is typed (`DemandOut`, `CandidateOut`, `TrendOut`, `MarketOut`, `OfferOut`). The OpenAPI spec and TS client are regenerated.
- **Web:** a "Rising searches" table on the Research page. It shows:
  - growth and a Crowded badge
  - the competition score and the prices seen
  - the source and fetch time, with a Stale badge after 24 hours
  - "Not available" for missing data, and a "Mock data" notice
- **Settings:** `PROVIDER_MARKET_DATA*`, `DATAFORSEO_*`, `SERPAPI_*`, `COST_*_MICROS`, `MARKET_DATA_MONTHLY_BUDGET_MICROS`, `MARKET_CACHE_MAX_AGE_HOURS` and `RESEARCH_*`, all in `.env.example`. An empty secret in `.env` counts as not set.
- **Docs:** ADR-0019, `docs/integrations/dataforseo.md`, `serpapi.md` and `ebay-browse.md`.

### Changed
- **eBay is dropped from the MVP** (owner decision). Its API licence forbids deriving average prices, aggregated seller data or site-wide statistics without written permission, and Browse production access is a Limited Release. The `marketplace` provider category stays registered with no adapters.
- **Amazon is left out** for now (owner decision).

### Tests
- **Contract:** DataForSEO and SerpApi request shapes, parsing, retryable and non-retryable errors, and that the key is never stored. Also failover and the mock.
- **Unit:** growth, peak, last-year lift, too little data, competition score with missing parts, price spread, the micros meter, seed ordering, the rising-query filter and cache keys.
- **Integration:**
  - The UK run on 6 Nov finds rising and crowded searches, every number traceable to a snapshot.
  - Provider switch and automatic failover (AC-34).
  - Both providers down gives degraded steps, not a failed run.
  - Cache: a hit costs nothing for a second workspace, which can't see the first one's snapshots. Entries older than 24 hours are fetched again, and the cache holds no workspace data.
  - Partial at the cap, the monthly budget, cost by provider, and price-band filtering.
- **Vitest and Playwright:** the J2 flow shows rising searches with a Crowded badge, checked with axe.

### Still mocked
- Market data stays on the mock until DataForSEO and SerpApi confirm their terms in writing. Draft emails are in the integration notes.
- Per-call cost estimates are settings converted from the providers' USD prices. Check them against real invoices.
- The DataForSEO shopping and popular-products fields follow the documented field list. Check them against a live sample before going live.
- Suppliers, scoring, compliance and the Top 10 report arrive in Phases 2c–2d.

## [Phase 2a — season engine, research runs and calendar] — 2026-11-08

### Added
- **Research runs (ADR-0018):**
  - Endpoints: `POST /research-runs`, `GET /research-runs`, `GET /research-runs/{id}`, and live progress at `GET /research-runs/{id}/events` (Server-Sent Events).
  - Runs are metered: each spends one research run, and a 402 `plan_limit_reached` comes back when none are left (AC-15). An optional `Idempotency-Key` header prevents double-spending.
  - A run that fails before spending anything gives the run back. A run that reaches its £1.00 cost cap stops as `partial` and keeps what it found.
  - Only the UK can be chosen. Audience mode stays behind its Later-roadmap flag.
- **Orchestrator:**
  - A plain Python pipeline run by the worker, one transaction per agent step.
  - Steps are stored, so a retried run resumes after the last finished step.
  - The minute sweeper re-queues stuck runs.
- **Season agent and engine (FR-2):**
  - Meteorological seasons by hemisphere, a look-ahead window of 2–12 weeks (default 8), and a season override.
  - Retail moments from a new `season_moments` table with yearly date rules, seeded with UK moments (AU too, for hemisphere tests only).
  - Act-by dates.
  - Public holidays in the window from Nager.Date, each stored as a source snapshot with URL and fetch time (AC-3).
- **Season calendar:** `GET /season-calendar` returns the next three retail moments with act-by dates. It uses an assumed delivery time until Phase 2c matches suppliers, and says so.
- **Audience profile:** `GET` and `PUT /audience-profile`. "Describe your audience" stores preferences only; runs copy them into their parameters.
- **Migration `0005`:** `season_moments`, `research_runs`, `agent_steps`, `source_snapshots` and `audience_profiles`, with RLS forced on the tenant tables.
- **Holidays adapter:** Nager.Date v4 plus a mock (`PROVIDER_HOLIDAYS`, `NAGER_BASE_URL`). Settings `RESEARCH_ASSUMED_DELIVERY_DAYS` and `RESEARCH_STUCK_AFTER_MINUTES`.
- **Web:**
  - Research page: a run form (window, season override, category, price band, audience toggle), a live progress timeline with spend against the cap, season results, recent runs, and the audience form.
  - Home: a "Coming up" season calendar card.
- **Docs:** ADR-0018 and `docs/integrations/nager-date.md`.

### Tests
- **Unit:** season rules, including Easter and nth-weekday rules, hemisphere seasons, windows, overrides and act-by.
- **Contract:** the Nager adapter.
- **Integration (pytest):**
  - The UK run on 6 Nov (autumn into winter, with Black Friday and Christmas).
  - Metering and 402, rejected runs spending nothing, idempotency and roles.
  - Isolation, failure with refund, partial at the cap, a degraded holidays step, and resume.
  - The sweeper, the events stream, the calendar and the audience profile.
- **Security:** RLS isolation for the four new tables.
- **Vitest and Playwright:** research helpers, and the J2 season flow with axe.

### Still mocked
- Market data, suppliers and the research LLM have no adapters yet (Phases 2b–2d).
- Act-by dates use an assumed delivery time.
- The mock holidays adapter computes UK bank holidays locally.

## [Phase 1B follow-up — product-owner decisions] — 2026-11-08

### Added
- **Platform fee on seller sales (ADR-0017):**
  - Rates: 2% on Lite and for workspaces without a trialing or active plan (pay-per-report); 0% on Starter, Growth, Pro and during the trial.
  - Checkout sends the fee as the Connect application fee, rounded down.
  - The ledger splits Stripe's `fee_details` into separate "Stripe fee" and "Platform fee" entries.
  - When a seller refunds a shopper, the fee is given back in proportion through Stripe's application-fee refunds. We check what Stripe has already refunded first, so a replay or a late retry never refunds twice.
  - Sellers on a paid plan keep their plan's rate while past due, paused or suspended. The 2% no-plan rate applies only with no subscription or a cancelled one.
- **Migration `0004`:** `checkout_sessions.application_fee_minor`, plus `orders.platform_fee_minor`, `platform_fee_refunded_minor` and `platform_fee_id`. Lite's `platform_fee_bps` is set to 200.
- **API:** `GET /plans` now returns each plan's `platform_fee_bps`, `no_plan_platform_fee_bps` and `trial.live_listings`.
- **Settings:** `TRIAL_LIVE_LISTINGS=5` and `PLATFORM_FEE_BPS_NO_PLAN=200`.

### Changed
- **The trial is capped at 5 live listings** as well as 3 runs. Listings arrive in Phase 3; the limit is in `PlanPolicy` now.
- **Run cost cap is £1.00 per run** (`RUN_COST_CAP_MINOR=100`, `RUN_COST_CAP_CURRENCY=GBP`; was $2.00). The unit-economics guard now compares against the GBP prices, and all four plans pass.
- **Pricing page and plan picker:** each plan shows "2% fee on your sales" or "No fee on your sales". The pay-per-report line mentions the fee, and the trial copy mentions the listing cap.
- **Docs:** PRD decisions (PR #5), ADR-0017, ADR-0015 notes, and the application-fee fields in `docs/integrations/stripe-connect.md`.

### Tests
- pytest:
  - trial listing allowance
  - fee rate per plan and status, and rounding
  - Lite checkout pays 2%, with separate ledger lines
  - a partial then full refund gives back 20p then 50p, and a replayed refund event changes nothing
  - Pro and trial pay nothing
  - a fee Stripe already refunded is not sent again
  - Stripe contract tests for the fee parameter, the `fee_details` split and platform-account fee refunds
  - the guard in GBP
- Playwright: the pricing page fee wording.

### Still mocked
- The mock Stripe stores application fees and their refunds in Redis. No live charges are made.

## [Phase 1B — SaaS subscriptions and billing] — 2026-11-08

### Added
- **Plans (ADR-0015):** migration `0003` adds `plans`, `plan_prices` and `addon_products`, seeded with the PRD placeholders:

  | Plan | Price (monthly / annual) | Research runs | Listings | Seats |
  | --- | --- | --- | --- | --- |
  | Lite | £12 / £120 | 3 | 10 | 1 |
  | Starter | £29 / £290 | 10 | 25 | 1 |
  | Growth | £79 / £790 | 40 | 100 | 3 |
  | Pro | £199 / £1,990 | 120 | unlimited | 10 |

  - Annual plans get +2 runs a month.
  - Run pack: 10 runs for £15. Single report: £9.
  - GBP only for now: prices are stored per currency, so others can be added later without code.
- **Subscriptions:** a 14-day trial with a card required, on Growth features and capped at 3 runs. Upgrades apply now; downgrades and cancellations apply at the period end.
  - Managed through Stripe Checkout and the Customer Portal (test mode only, ADR-0016).
  - The Customer Portal configuration turns off cancel and pause there, so both go through the app.
- **Entitlements:** `PlanPolicy` behind `EntitlementService`, with 402 `plan_limit_reached` when a limit is hit.
  - Metering: `consume_run`/`refund_run` spend plan runs first, then packs, then single reports, and are idempotent.
  - Seats count members plus pending invitations.
  - Access gate: gated writes need a trialing or active plan. `past_due`, `suspended` and `paused` are read-only; reads, exports, billing and settings stay open.
- **Billing API:**
  - `GET /plans` (public)
  - `/billing/checkout`, `packs/checkout`, `report-checkout` and `portal`
  - `/billing/subscription`, `usage` and `invoices` (invoices: Owner only)
  - `/billing/pause`, `resume`, `cancel-survey` and `keep`
- **Webhooks:** billing events arrive on the existing platform endpoint and webhook job. Older events can't overwrite newer state, and packs and reports are granted once.
- **Billing sweep (hourly, Celery Beat):**
  - trial reminder emails on day 10 and 13, stating the price and the date of the first charge
  - `past_due` → `suspended` after 30 days, with an email
  - paused subscriptions resume at the end of the pause
- **Dunning:** a failed payment makes the workspace read-only, shows a banner and emails the Owner. Paying the invoice restores access.
- **Pause and cancel:** pause for 1–3 months. The cancel survey offers a pause or one month free depending on the reason (the discount only once).
- **Platform admin API:**
  - tenants with plan, status, usage and MRR (the sum of active subscriptions)
  - plan and price edits with the unit-economics guard
  - trial extension, logged as a /touch and in the audit log
- **Web:**
  - public `/pricing` page, server-rendered from the plans table, annual plans first with the saving
  - plan gate for workspaces without a plan; trial countdown and status banners
  - Billing page: usage bars, plan picker, Portal, packs, single reports, pause and cancel survey, invoices
  - upgrade prompt when a plan limit is hit
  - mock Checkout and Customer Portal pages for local mock mode
- **Tools:** `tools/stripe_billing_seed.py` creates the Stripe catalogue from the tables, never by hand. The demo seed gives the demo workspace a mock Pro subscription.
- **Tests:**
  - pytest covers AC-14 to AC-17, the API part of AC-18, billing isolation (AC-19), webhook replay and out-of-order events, entitlement maths and a concurrency check, and contract tests for every Stripe Billing call.
  - Playwright covers pricing (desktop and mobile), AC-14 to AC-17, and axe on the billing screens in light and dark.
- **Docs:** `docs/integrations/stripe-billing.md` (checked against the current Stripe docs, API `2026-10-28.endive`) and ADRs 0015–0016.

### Changed
- `EntitlementService` now enforces plan limits. Before Phase 1B it allowed everything.
- Research runs are metered by `consume_run` rather than the write gate, so pay-per-report works without a plan.
- New touch category `billing` and the Owner-only permission `billing.invoices.read`.
- The web image passes `API_INTERNAL_URL` to the run stage for server-rendered pages.

### Still mocked or open
- **Stripe Billing** uses the mock adapter and dev pages unless test keys are set and the billing seed has run. It stays in test mode.
- **Stripe Tax** is off (`STRIPE_TAX_ENABLED=false`). The pricing page doesn't say whether prices include VAT until the VAT decision is made, and Stripe prices are created without a tax behaviour.
- **Prices and limits** are the PRD placeholders. The unit-economics guard only compares prices in the cost cap's currency (the PRD's $2.00 cap against GBP prices is reported as unchecked; no exchange rate is assumed).
- **Research runs and listings** don't exist yet. Runs are metered through a mock-only test tool, and listings count as 0 until Phase 3.
- **Admin console UI** is Phase 4; Phase 1B ships the API only. Deleting suspended workspaces' data after 90 days is a Phase 5 retention job.

## [Phase 1 follow-ups] — 2026-11-08

### Added
- **HEIC receipt OCR (ADR-0014):** HEIC receipts are converted to JPEG with libheif's decoder (`heif-dec`) before OCR; the original stays stored unaltered. The api and worker images install Debian's libheif tools with only the LGPL libde265 decoder, and the build fails if the GPL x265 encoder is present (pillow-heif was not used because its wheels bundle x265 and are GPLv2). Hosts without the decoder fall back to manual entry. CI installs the decoder so the conversion tests run.

### Fixed
- **Storage credentials:** file storage and Textract both read `AWS_REGION`, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, so storage on Cloudflare R2 (region `auto`, R2 token) and OCR on AWS Textract (`eu-west-2`, AWS keys) could not run together. Storage now has its own `S3_REGION` (default `auto`), `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY`; the `AWS_*` settings are Textract's only. **If you deploy with S3/R2, set the new `S3_*` keys.**
- **E2E:** the receipt step waits up to 30 s for OCR, which runs in the Celery worker; Playwright's 5 s default was tight on slow Docker hosts.
- **Image scan:** the api and worker images now run `apt-get upgrade` at build time, so Debian security fixes (here zlib1g CVE-2026-76844 and CVE-2026-85091) land even when the `python:3.12-slim` base lags behind.

### Decided
- Backup OCR stays AWS Textract (not Google Cloud Vision).
- Sellers keep the full Stripe Dashboard (Standard-equivalent connected accounts, ADR-0011), not Express.

## [Phase 1 — Ledger and payments] — 2026-11-07

### Added
- **Database:** Alembic migration `0002`: Stripe integrations, webhook events, idempotency keys, orders and order items, checkout sessions, spends, spend voids and receipts, ledger entries, CSV imports and exports. RLS is enabled and forced on every new table. `ledger_entries`, `spends`, `spend_voids` and `spend_receipts` are append-only (no grant, no policy, trigger), like the audit log.
- **Ledger (FR-7, ADR-0010):** signed integer minor-unit entries with currency; sales per item with Stripe fees spread by value; spends (a GBP base amount is required for other currencies, and no rate is guessed); voids as reversing entries; P&L by period and by product. Payouts are recorded as cash movements and kept out of P&L.
- **Idempotent money writes:** a unique `(workspace, source, source_ref)` on ledger entries, plus an `Idempotency-Key` header on every money-writing call (a replay returns the stored response; a changed body gets 422).
- **Commands:** `/spend` (including `ads`, which needs a product key), `/touch`, `/sale` (manual sale) and `/receipt`. The command bar opens each form pre-filled from the typed arguments; nothing saves without a click.
- **Receipts and OCR (ADR-0014):** magic-byte type check, 10 MB limit, SHA-256 stored, OCR in a background job, human confirmation that creates or links the spend and clears the "missing receipt" flag. OCR adapters: Claude vision (primary), AWS Textract AnalyzeExpense (secondary), automatic failover, and a mock that never invents values.
- **Stripe Connect and Checkout (FR-6, ADR-0011):** onboarding through Account Links with Standard-equivalent controller properties (Owner only); test-mode Checkout as direct charges; fees read from the balance transaction. Webhook receivers for the platform and Connect endpoints verify signatures on the raw body, refuse live events, store each event once and process it in a job. They handle checkout completion, refunds (cumulative, so replays write nothing), disputes, payouts and account updates. A one-minute sweeper re-queues stuck webhooks and OCR jobs.
- **CSV imports (FR-13, J7):** ad spend and outside sales, with a suggested column mapping, per-row errors and duplicate-file protection. There are no ad-platform presets yet, because the export columns are unverified.
- **Audit pack (FR-8):** a worker job that builds ledger, spends, touches and orders CSVs, a receipts ZIP with a hash manifest, an audit-log extract with chain verification, a WeasyPrint summary PDF and a SHA-256 manifest. Owner and Accountant only. Downloads use 5-minute signed links.
- **File storage (ADR-0012):** a storage port with a local adapter (Docker volume, HMAC-signed links) and an S3/R2 adapter.
- **Interim product key (ADR-0013):** order items, spends and ledger entries carry an optional `product_key` for per-product P&L until listings arrive in Phase 3.
- **Web:** Home KPIs and daily chart; Sales & Orders (counters, orders, test checkout, manual sale, CSV import); Ledger tabs (spends, touches, receipts with OCR confirm, entries, P&L by product, audit-pack export); Integrations (Stripe card). Charts have data-table fallbacks. Mock Stripe onboarding and checkout pages exist in mock mode only.
- **Shared package:** command argument parsers, money formatting and form schemas.
- **Tests:** 248 pytest tests (86% coverage), including AC-6, AC-7, AC-8, AC-9, AC-10 and AC-28, isolation and append-only checks on every new table, and contract tests for Stripe, Claude, Textract (botocore Stubber) and S3 (moto). Vitest: 14 shared and 8 web. Playwright: 10 end-to-end tests covering payments (AC-6), `/spend` with a receipt, `/touch` and the audit pack (AC-8 to AC-10), and axe on the ledger screens in light and dark.
- **Docs:** ADRs 0010–0014; integration notes for Stripe Connect, Stripe Checkout and webhooks, Claude vision, Textract and S3/R2.
- **Dependencies:** stripe, boto3, anthropic, weasyprint, pillow and recharts (runtime); moto and boto3 type stubs (dev).

### Changed
- CI actions were bumped to their Node 24 majors, and Pango is installed for the audit-pack PDF test. The Postgres-roles step had been merged into the Pango step by mistake, so the roles were never created; that is fixed.
- Settings refuse Stripe live keys, mock webhook secrets and local storage outside `local`/`test`.

### Acceptance criteria
- AC-6, AC-7, AC-8, AC-9, AC-10 and AC-28 pass (pytest and Playwright).
- AC-19 covers the Phase 1 parts: ledger, receipts, orders and exports are isolated at the database and API level. Usage and invoices come with Phase 1B.

### Still mocked or not yet built
- **Stripe:** the mock signs Stripe-shaped events and posts them to the real webhook endpoints. Live test mode needs `STRIPE_SECRET_KEY` (test key) and webhook secrets from the Stripe CLI or dashboard. Production keys are refused.
- **OCR:** with no keys, a mock returns fixture results keyed by file hash. HEIC receipts skip OCR (entered by hand) until a conversion library is approved.
- **Storage:** local volume only, unless `STORAGE_BACKEND=s3` and bucket credentials are set.
- **Platform fee:** 0 in Phase 1.
- **Per-product P&L** uses the interim `product_key` until listings arrive in Phase 3.
- **The audit-pack PDF unit test** is skipped on hosts without Pango (Windows). It runs in Docker and CI.
- Plans and limits (Phase 1B), research (Phase 2) and listings and fulfilment (Phase 3) are not started.

## [Phase 0 — Foundations] — 2026-11-07

### Added
- **Monorepo:** pnpm workspaces (`apps/web`, `packages/shared`) and a uv workspace (`apps/api`, `apps/worker`) with locked dependencies. Docker Compose stack: pgvector Postgres 16, Redis 7, api, worker, web.
- **Database:** Alembic migration `0001`: users, magic-link and refresh tokens, workspaces, memberships, invitations, feature flags with per-workspace overrides, provider config, waitlist, touches, hash-chained audit log. Separate `app_owner` (migrations) and `app_rw` (runtime, no RLS bypass) roles.
- **Tenant isolation:** RLS enabled and forced on every tenant table. The workspace comes from `X-Workspace-Id`. A non-member gets 404, and a forbidden role gets 403.
- **Auth:** email magic links, Google OIDC (with a local mock IdP), 15-minute access JWTs, rotating refresh cookies with reuse detection, optional TOTP 2FA, rate limits.
- **Workspaces and roles:** create a UK workspace (GBP), invite Admin, Analyst or Accountant, accept invitations, change roles, remove members, last-owner protection. Every permission change writes an automatic `/touch` (`team_and_permissions`) and an audit entry.
- **Feature flags:** UK on, every other country off. Later-roadmap modules (audience research, Shopify, ads, 3PL/POD, mobile, referrals, custom domains) off.
- **Provider registry (FR-15):** env and DB configuration, mock mode. Email adapters: Resend (primary), Postmark (secondary), automatic failover. Google identity adapter.
- **EntitlementService:** wired into invitations. It allows everything until plans arrive in Phase 1B.
- **Waitlist:** a landing page with the promise, unbundled consent, double opt-in and signed unsubscribe links.
- **Web app (Next.js 16, React 19):** landing page, sign-in, verify (with 2FA), Google callback, onboarding, dashboard shell with all 11 PRD screens as empty states, role-gated navigation, workspace switcher, light and dark themes, Team & roles and 2FA settings, command bar (`/` or Ctrl/⌘+K) with FR-7 command autocomplete.
- **Shared package:** generated OpenAPI types and client, role matrix, command grammar, Zod schemas.
- **Worker:** a Celery app with a heartbeat task on Beat.
- **Tests:** 103 pytest tests (90% coverage) covering RLS coverage, DB and API tenant isolation, the role matrix, auth flows, audit tamper detection, append-only enforcement, adapter contracts and contract drift. Vitest for shared and web. Playwright end-to-end with axe for the waitlist, onboarding, invitations, role gating, the command bar and mock Google.
- **CI:** GitHub Actions (ruff, mypy, pytest + coverage gate, contract drift, pip-audit, eslint, tsc, vitest, build, pnpm audit, Playwright e2e, gitleaks, Trivy).
- **Docs:** ADRs 0001–0009, ERD, OpenAPI spec, integration notes (Resend, Postmark, Google OIDC), runbook and compliance stubs.

### Changed
- PRD constraint updated: Node 22 LTS and React 19 instead of Node 20 and React 18 (Node 20 is end-of-life). See ADR-0006.

### Still mocked or not yet built
- Email delivery and Google sign-in are mocked unless live keys are set.
- Plan limits: `EntitlementService` allows everything until Phase 1B.
- The market data, supplier, OCR and LLM adapters come in Phases 1–2.
- Platform-admin break-glass access and the admin console UI come in Phase 4.
- The landing page is not deployed.
