# momentsales.ai

Agentic commerce assistant for **UK side-hustlers and small online shops**: *find what's about to sell, launch a shop in a weekend, and keep books your accountant will accept.*

The source of truth for scope and phases is [`docs/PRD.md`](docs/PRD.md). **Status: Phases 0, 1 and 1B (with its follow-up) are merged. Phase 2 is approved and in progress: 2a (season engine, research runs, season calendar), 2b (market data, rising searches, competition, shared cache) and 2c (UK supplier matching) are merged; 2d (scoring, compliance, the Top 10 report) is in review.**

## Quick start (no paid keys needed)

Needs Docker Desktop. Everything runs with `PROVIDER_MODE=mock`.

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec api python -m momentsales.tools.seed
```

Then open http://localhost:3000.

- **Sign in:** go to `/sign-in` and enter any email. In mock mode no email is sent: open the link from the dev outbox at http://localhost:3000/api/v1/dev/outbox, or use "Continue with Google", which opens a local mock consent page.
- **Seeded demo workspace** "Demo Candle Co": `owner@`, `admin@`, `analyst@` and `accountant@example.com`, one per role.
- **API docs:** http://localhost:${API_PORT:-8000}/api/v1/docs (local only).
- **Plans and trial:** new workspaces start on the plan picker. Choose a plan on Billing to start the 14-day trial through a mock Stripe Checkout page (no card needed in mock mode). The demo workspace already has a mock Pro subscription. In mock mode, Manage billing opens a mock Customer Portal with test tools to start the next billing period or fail and pay an invoice. The Billing page has a "Use one research run" test tool until research arrives in Phase 2. Public pricing: http://localhost:3000/pricing.
- **Try the ledger:** in Integrations, connect Stripe (a mock onboarding page), then take a test payment from Sales & Orders. It appears on Home within seconds. Press `/` and type `/spend 24.99 GBP shipping vendor:Royal Mail` or `/touch pricing 10m "adjusted margin"`, then attach a receipt and export an audit pack from Ledger.

## Developing on the host

| Need | Version |
| --- | --- |
| Python | 3.12, with [uv](https://docs.astral.sh/uv/) 0.9+ |
| Node | 22 LTS. **Please update to the latest 22.x** (see [ADR-0006](docs/adr/0006-runtime-versions.md)) |
| pnpm | 10.34.9. Use `npx pnpm@10.34.9 …` if corepack fails on older Node 22 |
| Docker | for Postgres and Redis |

```bash
docker compose up -d postgres redis
uv sync
uv run alembic -c apps/api/alembic.ini upgrade head
npx pnpm@10.34.9 install
npx pnpm@10.34.9 dev
```

`pnpm dev` serves the web app on :3000 and proxies `/api/*` to `API_INTERNAL_URL` (default http://localhost:8000). Run the API on the host with `uv run uvicorn momentsales.main:app --reload`, or keep `docker compose up -d api worker`.

### Tests and checks

```bash
uv run ruff check apps && uv run ruff format --check apps
uv run mypy apps/api/src apps/worker/src
uv run pytest --cov
npx pnpm@10.34.9 lint && npx pnpm@10.34.9 typecheck && npx pnpm@10.34.9 test
npx pnpm@10.34.9 e2e
```

`pytest` uses the `momentsales_test` database in the compose Postgres (recreated each run), so `postgres` and `redis` must be up. `pnpm e2e` needs the full stack (`docker compose up`). The audit-pack PDF test needs Pango (WeasyPrint). It is skipped on Windows hosts and runs in Docker and CI.

Regenerate the API contracts after changing endpoints or permissions:

```bash
npx pnpm@10.34.9 gen:api
```

## Layout

```
apps/api       FastAPI modular monolith (auth, tenancy, workspaces, flags, providers, audit,
               ledger, payments, receipts, imports, exports, billing)
apps/worker    Celery workers (webhooks, receipt OCR, audit packs, sweepers, billing sweep)
apps/web       Next.js App Router: landing and pricing pages, auth, onboarding, dashboard
packages/shared  Generated API types and client, role matrix, command grammar, Zod schemas
infra/         Dockerfiles, Postgres role bootstrap, Terraform placeholder
docs/          PRD, ADRs, ERD, OpenAPI spec, integration notes, runbooks, compliance
```

## Key rules (from CLAUDE.md)
- Multi-tenant: every query is workspace-scoped. Postgres RLS is enabled **and forced** on every tenant table, and tests prove isolation ([ADR-0004](docs/adr/0004-tenancy-rls.md)).
- Every external service sits behind an adapter with a mock, and the app runs with `PROVIDER_MODE=mock`.
- Only the UK is selectable. Later-roadmap features are behind feature flags that are off.
- The audit log and touches are append-only and hash-chained ([ADR-0008](docs/adr/0008-audit-log-and-touches.md)). So are the ledger, spends and receipts, with corrections as reversing entries ([ADR-0010](docs/adr/0010-ledger.md)).
- Money is stored as integer minor units plus currency, and every money write is idempotent.
- Stripe runs in test mode only; settings refuse live keys and live events ([ADR-0011](docs/adr/0011-stripe-connect.md)).
- Every plan-limited action goes through `EntitlementService`; writes need a trialing or active subscription ([ADR-0015](docs/adr/0015-plans-entitlements-usage.md), [ADR-0016](docs/adr/0016-subscription-state-and-access.md)).

## What is mocked (as of Phase 2d)

| Area | Mock | Live adapter | To go live |
| --- | --- | --- | --- |
| Email (sign-in links, invitations, waitlist) | Redis dev outbox, `GET /api/v1/dev/outbox` | Resend (primary), Postmark (secondary), automatic failover | `PROVIDER_MODE=live`, `RESEND_API_KEY` and/or `POSTMARK_SERVER_TOKEN`, a verified sending domain |
| Google sign-in | Local mock consent page `/dev/mock-google` | Google OIDC | A Google Cloud OAuth client (testing mode), `GOOGLE_CLIENT_ID/SECRET` |
| Stripe Connect, Checkout, webhooks | Mock onboarding and checkout pages; signed Stripe-shaped events posted to the real webhook endpoints | Stripe (test mode only) | `STRIPE_SECRET_KEY` (test), `STRIPE_PUBLISHABLE_KEY`, webhook secrets from the Stripe CLI or dashboard |
| Receipt OCR | Fixture results keyed by file hash; never invents values | Claude vision (primary), AWS Textract (secondary), automatic failover | `ANTHROPIC_API_KEY` and/or `AWS_REGION` (`eu-west-2`), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (used only by Textract). HEIC receipts are converted to JPEG for OCR by libheif's decoder (in the Docker images); on a host without it they are typed in by hand |
| File storage | Local Docker volume with signed 5-minute links | S3 or Cloudflare R2 | `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL` (R2), `S3_REGION` (`auto` for R2), `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`. Separate from Textract's `AWS_*` keys |
| SaaS billing (trial, subscriptions, Portal, packs, reports, dunning) | Mock Stripe Billing: mock Checkout and Customer Portal pages; signed Stripe-shaped events posted to the real platform webhook endpoint; test tools for renewals and failed/paid invoices | Stripe Billing (test mode only) | `STRIPE_SECRET_KEY` (test), then `uv run python -m momentsales.tools.stripe_billing_seed` to create Products, Prices, the retention coupon and the Portal configuration (`STRIPE_PORTAL_CONFIGURATION_ID`). In the Stripe dashboard set Smart Retries to "Leave the subscription past-due". `STRIPE_TAX_ENABLED=true` once tax registrations exist |
| Plan prices and limits | Final launch values (product owner, 2026-11-08) in the `plans` tables (GBP only) | Edited by a Platform admin through `/api/v1/admin/plans` | Other currencies when other countries open |
| Platform fee (2% on Lite and pay-per-report, ADR-0017) | Mock Stripe stores application fees and fee refunds | Stripe Connect application fees (test mode only) | Nothing extra beyond the Stripe Connect keys |
| Research runs | Real runs through `POST /research-runs`, metered by `consume_run`. Seven steps: Season, Trend (rising searches, growth rate, "crowded" flag), Marketplace (competition score), Supplier (UK dropship supplier, Verified UK supplier rule), then Scoring, Compliance and Report, which also run after the cost cap on what was gathered. The mock-only "Use one research run" billing tool stays for billing tests | — | — |
| Public holidays (Season agent) | Mock computes the England and Wales bank holidays locally | Nager.Date v4 (no key) | `PROVIDER_MODE=live`; nothing else needed |
| Act-by dates (season calendar) | Use the median delivery time of matched suppliers from the workspace's latest run (AC-26); before any run, an assumed delivery time (`RESEARCH_ASSUMED_DELIVERY_DAYS`, default 5), labelled as an assumption | — | — |
| Listings | Count 0 against the trial and plan caps | — | Phase 3 |
| Market data (Google Trends, Google results) | Deterministic mock series and offers, labelled "Mock data" in the UI, with `mock://` snapshot URLs and example.com links. Mocks charge the real providers' cost estimates, so a mock run shows a simulated spend | DataForSEO (primary), SerpApi (secondary), automatic failover ([ADR-0019](docs/adr/0019-market-data-cache.md)) | **Get written confirmation from both providers that we may show derived signals and share the cache** (draft emails in `docs/integrations/dataforseo.md` and `serpapi.md`). Then set `PROVIDER_MODE=live`, `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` and `SERPAPI_API_KEY`, and check a live results sample against the parser |
| Shared market-data cache | Real (Postgres), reused for 24 hours across workspaces; non-personal data only | — | A 90-day retention job (Phase 5) |
| Marketplace data (eBay, Amazon) | Not used. eBay's licence forbids the aggregated use we need without written permission; Amazon was left out by owner decision | — | eBay Partner Network approval and written permission ([note](docs/integrations/ebay-browse.md)) |
| Suppliers (CJ, Avasam) | Deterministic mock items (UK stock, untracked, slow, low-margin, non-UK, no match) with `mock://` URLs. CJ's mock prices in USD to exercise FX; Avasam's mock, like the real adapter, has no shipping cost, so its matches show "no verified supplier" | CJ Dropshipping (primary, one platform account), Avasam (secondary, each seller's own keys), manual switch only ([ADR-0020](docs/adr/0020-supplier-matching.md)) | **Get written confirmation from CJ and Avasam** (draft emails in `docs/integrations/cj-dropshipping.md` and `avasam.md`), then set `CJ_API_KEY`, check a live sample (GB stock, tracking, `logisticAging`) against the parser, and have a Platform admin record evidence for checks 1–4 |
| Verified UK supplier evidence | Real (Postgres, append-only), recorded by a Platform admin through `POST /admin/supplier-verifications`. Locally, `POST /api/v1/dev/mock-suppliers/verify` adds `mock://` evidence so mock matches can be verified | — | Real evidence for CJ and Avasam (dropshipping, stock and cost updates, tracked UK delivery, UK returns) |
| FX rates | Fixed mock rate (1 USD = £0.75), labelled `mock://` | Frankfurter (ECB reference rates, no key) | Set `PROVIDER_MODE=live` |
| Research LLM (compliance check, one-line reasons) | Deterministic mock answers, labelled; £0 | Claude Opus 5.5 ([ADR-0021](docs/adr/0021-scoring-compliance-report.md), [note](docs/integrations/anthropic-llm.md)) | Set `ANTHROPIC_API_KEY` and `PROVIDER_MODE=live`; check `LLM_PRICE_*` against the current price list |
| Top 10 report, CSV/PDF export, share links | Real (Postgres, RLS). Built from the mocked research data above | — | — |
| Ad cost per sale, selling price | Estimates: a configured share of the price (`RESEARCH_AD_COST_BPS`) and the middle price seen on Google, labelled in the report | — | A licensed benchmark source, if one is chosen later |

Dev endpoints (`/api/v1/dev/*`) exist only when `ENV` is `local` or `test` **and** `PROVIDER_MODE=mock`. Settings refuse mock providers and placeholder secrets in production.

## Status

- **Phase 0 — Foundations:** done (merged).
- **Phase 1 — Ledger and payments:** done. AC-6, AC-7, AC-8, AC-9, AC-10 and AC-28 pass, plus the Phase 1 parts of AC-19.
- **Phase 1B — SaaS subscriptions and billing:** done (merged). A follow-up applies the product owner's decisions: the trial listing cap, the £1 run cost cap and the 2% platform fee with proportional refunds. AC-14, AC-15, AC-16 and AC-17 pass (API and e2e), the API part of AC-18 (admin tenants, MRR, trial extension; the console UI is Phase 4), the billing parts of AC-19, and AC-13 on the new screens.
- **Phase 2 — Seasonal research:** approved (2026-11-08), delivered as four PRs.
  - **2a — season engine, research runs and calendar:** in review. Covers the season parts of AC-1, AC-2 (engine level, since only the UK is selectable), AC-3 for holiday facts, AC-15 on real runs, and the calendar part of AC-26 (with an assumed delivery time).
  - **2b — market data, rising searches and competition:** in review. Covers the data part of AC-25 (growth rate, "crowded" badge and competition score for each search), AC-3 for every trend and market number, and the market-data half of AC-34 (provider switch and automatic failover). Ranking a crowded product below an otherwise equal rising one is part of scoring in 2d.
  - **2c — UK supplier matching:** in review. Covers AC-20 (each checked search shows a verified UK supplier with unit cost, shipping, warehouse and delivery range, or a "no supplier" / "no verified supplier" flag), the data part of AC-21 ("arrives too late"), AC-26 (act-by dates follow the matched suppliers' delivery time) and the supplier half of AC-34 (switch to Avasam, no automatic failover). Blocking listing for "no supplier" products comes with listings in Phase 3, and ranking late products down with scoring in 2d.
  - **2d — scoring, compliance and the Top 10 report:** in review. Covers AC-1 (ten products, each with an image, purchase links, best price, sizes or "not available", delivery estimate and a reason), AC-3 for report facts, the ranking halves of AC-21 and AC-25, the profit part of AC-27, the flag part of AC-29, and AC-19 for reports. Marketplace sales rank is not scored (no licensed source). The marketing kit, listings and the "Why this product?" follow-up come in Phase 3.

See [CHANGELOG.md](CHANGELOG.md) for details. The landing page and waitlist are built but **not deployed**. Publishing them is a separate step that needs approval.
