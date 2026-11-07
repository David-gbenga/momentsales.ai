# Resend (email, primary)

Adapter: `apps/api/src/momentsales/adapters/email/resend.py`. Mock: `adapters/email/mock.py`. Selected by `PROVIDER_EMAIL=resend`.

Checked against the official docs on 2026-11-07:
- https://resend.com/docs/api-reference/emails/send-email
- https://resend.com/docs/dashboard/emails/idempotency-keys
- https://resend.com/docs/api-reference/errors

| Item | Value |
| --- | --- |
| Endpoint | `POST https://api.resend.com/emails` |
| Auth | `Authorization: Bearer <RESEND_API_KEY>` |
| Body (used) | `from` (string, `Name <email>`), `to` (string or array, max 50; we send a one-item array), `subject`, `text`, `html` (optional), `reply_to` (optional), `tags` (`[{name, value}]`, ASCII letters, digits, `_` and `-`, max 256 chars) |
| Idempotency | `Idempotency-Key` header, 1–256 chars, kept 24 h. Same key + different body → 409 |
| Success | `{"id": "<uuid>"}` |
| Errors we retry on the secondary | 429 (rate/quota), 500, 503 |
| Errors we don't retry | 400, 401, 403 (e.g. unverified `from` domain), 404, 409, 422 |

Not verified on the pages read: rate-limit header names, and the exact error JSON body. The adapter relies only on the status code.

Terms: transactional email to people who asked for it (sign-in links, invitations, waitlist double opt-in). The sending domain must be verified in Resend before live use.
