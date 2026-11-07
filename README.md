# momentsales.ai

Agentic commerce assistant for **UK side-hustlers and small online shops**: *find what's about to sell, launch a shop in a weekend, and keep books your accountant will accept.*

The source of truth for scope and phases is [`docs/PRD.md`](docs/PRD.md). **Status: Phase 1 (Ledger and payments) complete; Phase 1B not started.**

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
               ledger, payments, receipts, imports, exports)
apps/worker    Celery workers (webhook processing, receipt OCR, audit packs, sweeper)
apps/web       Next.js App Router: landing page, auth, onboarding, dashboard shell
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

## What is mocked (as of Phase 1)

| Area | Mock | Live adapter | To go live |
| --- | --- | --- | --- |
| Email (sign-in links, invitations, waitlist) | Redis dev outbox, `GET /api/v1/dev/outbox` | Resend (primary), Postmark (secondary), automatic failover | `PROVIDER_MODE=live`, `RESEND_API_KEY` and/or `POSTMARK_SERVER_TOKEN`, a verified sending domain |
| Google sign-in | Local mock consent page `/dev/mock-google` | Google OIDC | A Google Cloud OAuth client (testing mode), `GOOGLE_CLIENT_ID/SECRET` |
| Stripe Connect, Checkout, webhooks | Mock onboarding and checkout pages; signed Stripe-shaped events posted to the real webhook endpoints | Stripe (test mode only) | `STRIPE_SECRET_KEY` (test), `STRIPE_PUBLISHABLE_KEY`, webhook secrets from the Stripe CLI or dashboard |
| Receipt OCR | Fixture results keyed by file hash; never invents values | Claude vision (primary), AWS Textract (secondary), automatic failover | `ANTHROPIC_API_KEY` and/or AWS credentials (`eu-west-2`). HEIC receipts skip OCR until a converter is approved |
| File storage | Local Docker volume with signed 5-minute links | S3 or Cloudflare R2 | `STORAGE_BACKEND=s3`, `S3_BUCKET`, credentials, `S3_ENDPOINT_URL` for R2 |
| Plan limits | `EntitlementService` allows everything (no plans yet) | — | Phase 1B adds plans and limits |
| Market data, suppliers, research LLM | Registered in the provider registry, no adapters yet | — | Phase 2 |

Dev endpoints (`/api/v1/dev/*`) exist only when `ENV` is `local` or `test` **and** `PROVIDER_MODE=mock`. Settings refuse mock providers and placeholder secrets in production.

## Status

- **Phase 0 — Foundations:** done (merged).
- **Phase 1 — Ledger and payments:** done. AC-6, AC-7, AC-8, AC-9, AC-10 and AC-28 pass, plus the Phase 1 parts of AC-19.
- **Phase 1B — SaaS subscriptions and billing:** next. It needs approval before work starts.

See [CHANGELOG.md](CHANGELOG.md) for details. The landing page and waitlist are built but **not deployed**. Publishing them is a separate step that needs approval.
