# Changelog

All notable changes are recorded here, by phase.

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
