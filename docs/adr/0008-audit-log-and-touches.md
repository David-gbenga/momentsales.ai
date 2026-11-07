# ADR-0008: Hash-chained audit log and automatic touches

- Status: Accepted (Phase 0)
- Date: 2026-11-07

## Context
FR-8 needs an immutable, hash-chained audit log, and every permission change must be logged as a /touch. The ledger and audit log are append-only.

## Decision
- **One chain per workspace**, plus a `platform` chain for events with no workspace (sign-in, 2FA changes). `hash = sha256(prev_hash ‖ canonical_json(entry))`, where the canonical JSON uses sorted keys and UTC ISO timestamps.
- Appends lock the chain's row in `audit_chain_heads` (`SELECT … FOR UPDATE`), so concurrent writers can't fork a chain. That table holds only hashes, so it needs no RLS, and a workspace never has to read another tenant's rows to extend its chain.
- **Append-only, enforced three ways:** `app_rw` has no UPDATE, DELETE or TRUNCATE privilege; there are no UPDATE/DELETE RLS policies (so even the owner changes zero rows); and triggers raise an error on UPDATE, DELETE and TRUNCATE. The same applies to `touches`.
- `verify_chain()` recomputes a workspace's chain, and AC-10's audit-pack extract will use it.
- **Touches:** every permission change (workspace created, invitation created, revoked or accepted, role changed, member removed) writes an automatic touch (`auto = true`, `minutes = 0`) in category **`team_and_permissions`**. That category is our addition to the FR-7 list, so the rule fits.

## Consequences
- Correcting a record means writing a new reversing entry. That is the ledger rule, and it arrives with the ledger in Phase 1.
- Test databases are recreated with `alembic downgrade base` + `upgrade head`, because append-only rows can't be deleted.
