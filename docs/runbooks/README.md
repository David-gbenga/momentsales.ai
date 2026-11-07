# Runbooks

Planned in the PRD (*Deployment and operations*). Each is written in the phase that introduces the system it covers.

| Runbook | Phase |
| --- | --- |
| Stripe webhook backlog | 1 / 1B |
| Provider outage and switching (`PROVIDER_<CATEGORY>`, `provider_configs`) | 2 |
| Rotating secrets (`JWT_SECRET`, `SIGNING_SECRET`, `TOTP_ENCRYPTION_KEY`, provider keys) | 5 |
| Restoring backups | 5 |
| Handling a data-subject request | 3 / 4 |

## Phase 0 operational notes
- Rotating `JWT_SECRET` signs everyone out within 15 minutes (access tokens). Rotating `SIGNING_SECRET` invalidates unsubscribe links and in-flight Google sign-ins. Rotating `TOTP_ENCRYPTION_KEY` without re-encrypting makes stored TOTP secrets unreadable, so don't do it until the Phase 5 runbook exists.
- To revoke a user's sessions, set `refresh_tokens.revoked_at` for their families. Their access tokens expire within 15 minutes.
