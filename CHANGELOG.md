# Changelog

All notable changes are recorded here, by phase.

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
