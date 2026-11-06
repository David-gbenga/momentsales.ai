# momentsales.ai

Read `docs/PRD.md` before every task and follow its phases. It is the source of truth for scope, architecture, rules and acceptance criteria.

## How we work
- Build one phase at a time, in the order in the PRD (Phase 0, 1, 1B, 2, 3, 4, 5, 6, 7).
- Never start the next phase without my explicit approval.
- For every phase: plan first (plan mode), wait for my approval, then implement.
- Run all tests and linters before saying a phase is done, and check that phase's acceptance criteria (AC) in the PRD.
- After each phase, update `README.md` and `CHANGELOG.md`, and list what is still mocked.
- If a requirement is unclear, risky or not possible with the available APIs, stop and ask me with options. Do not guess.
- Ask me before adding a new dependency, running a database migration against anything other than local, or changing the tech stack.

## Non-negotiable rules
- This is a multi-tenant SaaS: every query is scoped to a workspace, and every plan-limited action goes through the `EntitlementService`.
- Every external service (Claude API, data providers, social APIs, Stripe, Shopify, email, OCR) sits behind an adapter with a mock. The app must run locally with `PROVIDER_MODE=mock` and no paid keys.
- Never hard-code keys or secrets. Read them from environment variables; keep `.env.example` complete. Never commit `.env`.
- Stripe (Connect and Billing) and Shopify stay in test/dev mode unless I say otherwise.
- Never invent prices, sizes, delivery dates or API fields. Data shown to users must come from a tool result with a stored source and timestamp.
- No scraping that breaks a platform's terms. Social data only from accounts the seller owns or people who have consented.
- Agents never take irreversible actions (publish, charge, email customers, log spend) without a human approval recorded as a /touch event.
- Money is stored as integer minor units plus currency code. All money writes are idempotent. The ledger and audit log are append-only.

## Code standards
- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, mypy strict, ruff, pytest.
- Frontend: React + TypeScript strict, Tailwind, shadcn/ui, TanStack Query, Zod; eslint; Vitest and Playwright.
- Monorepo layout: `apps/web`, `apps/api`, `apps/worker`, `packages/shared`, `infra/`, `docs/`.
- Record significant decisions as ADRs in `docs/adr/`.
- Commit in small, clear steps with descriptive messages.
