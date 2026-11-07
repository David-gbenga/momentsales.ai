# ADR-0004: Tenant isolation with Postgres RLS plus service checks

- Status: Accepted (Phase 0)
- Date: 2026-11-07

## Context
momentsales.ai is multi-tenant from day one. The PRD makes tenant isolation a release blocker and asks for Postgres row-level security as well as service-layer checks.

## Decision
- **Two database roles.** `app_owner` owns the schema and runs Alembic. `app_rw` is what the API and worker use: `NOSUPERUSER NOBYPASSRLS`, DML only, and no UPDATE/DELETE on append-only tables.
- **RLS on every tenant table**, both `ENABLE` and `FORCE` (so it applies to the owner too). Policies read per-transaction settings through helper functions: `app_current_workspace()`, `app_current_user_id()`, `app_is_platform()`.
  - `workspaces`: visible in the active workspace, or to any active member, so a user can list their own workspaces.
  - `memberships`: the active workspace's rows, plus the user's own rows.
  - `invitations`, `feature_flag_overrides`, `touches`, `audit_log`: the active workspace only. The platform audit chain is visible only in platform context.
- **Request flow.** One transaction per request (`Depends(get_db, scope="function")`, so it commits before the response). The `X-Workspace-Id` header (or the path id) picks the workspace. `set_config(..., is_local => true)` sets the context, which therefore can't leak through the connection pool. The service checks for an active membership: no membership returns **404 `workspace_not_found`**, so a workspace's existence is never confirmed. Then the role matrix (`tenancy/permissions.py`) is checked: **403 `forbidden`**.
- Services still filter by `workspace_id`. RLS is the second layer, not the only one.
- Invitations carry the workspace id in the token (`<workspace_id>.<secret>`). That is the only way a non-member's request looks inside a workspace, and only at its invitations.

## Tests (release blockers)
- `test_rls_coverage.py`: every table with `workspace_id` is listed in `TENANT_TABLES` and has RLS enabled, forced and with policies. The runtime role can't bypass RLS.
- `test_tenant_isolation_db.py`: in workspace B's context, workspace A's rows can't be read, updated, deleted or inserted into, for every tenant table.
- `test_tenant_isolation_api.py`: every workspace-scoped route, discovered automatically, returns 404 for a non-member.

## Consequences
- Every new tenant table needs RLS in its migration. The coverage test fails the build otherwise.
- The platform admin console (Phase 4) will add a logged break-glass context instead of broad platform policies.
