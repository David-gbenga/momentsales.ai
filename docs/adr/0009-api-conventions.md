# ADR-0009: API conventions

- Status: Accepted (Phase 0)
- Date: 2026-11-07

## Decision
- REST + JSON under `/api/v1`. The OpenAPI spec is generated and committed (`docs/openapi.json`), and the TS types are generated from it (`packages/shared/src/generated/api.d.ts`).
- Errors use **RFC 9457 `application/problem+json`**: `type` (a stable short code such as `workspace_not_found`, `forbidden`, `plan_limit_reached`), `title`, `status`, `detail`, `instance`, plus extra fields (`action`, `errors`, `limit`, …).
- The workspace comes from `X-Workspace-Id` (ADR-0004). `/workspaces/{id}` takes it from the path.
- Plan limits return `402 plan_limit_reached` with `limit`, `used`, `allowed` and `upgrade_url` (wired through `EntitlementService`; plans arrive in Phase 1B).
- List endpoints with unbounded growth will use cursor pagination (from Phase 1; Phase 0 lists are small and bounded).
- Money endpoints will accept an `Idempotency-Key` (Phase 1).

## Phase 0 deviations from the plan
- `GET /me` is served at `GET /auth/me`.
- Member and invitation routes use the header (`/members`, `/invitations`) rather than `/workspaces/{id}/members`, matching the PRD's unprefixed style.
