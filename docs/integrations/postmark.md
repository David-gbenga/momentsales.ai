# Postmark (email, secondary)

Adapter: `apps/api/src/momentsales/adapters/email/postmark.py`. Selected by `PROVIDER_EMAIL=postmark`, or as `PROVIDER_EMAIL_SECONDARY` with failover.

Checked against the official docs on 2026-11-07:
- https://postmarkapp.com/developer/api/email-api
- https://postmarkapp.com/developer/api/overview

| Item | Value |
| --- | --- |
| Endpoint | `POST https://api.postmarkapp.com/email` |
| Headers | `Accept: application/json`, `Content-Type: application/json`, `X-Postmark-Server-Token: <POSTMARK_SERVER_TOKEN>` (test value: `POSTMARK_API_TEST`) |
| Body (used) | `From` (needs a sender signature), `To` (comma-separated, max 50), `Subject`, `TextBody` and/or `HtmlBody`, `ReplyTo`, `Metadata` (object), `MessageStream` (default `outbound`) |
| Success (200) | `{To, SubmittedAt, MessageID, ErrorCode: 0, Message: "OK"}` |
| Errors | 401 bad token (ErrorCode 10); 422 validation (`{"ErrorCode": n, "Message": …}`, e.g. 300 invalid request, 406 inactive recipient); 429 rate limit; 500 server error (ErrorCode 101); 503 maintenance (ErrorCode 100) |
| Retried on the other provider | 429, 500, 503 |

Postmark documents no idempotency header for single sends. A retry after a transport error could, rarely, deliver twice. Sign-in links are single-use, so a duplicate is harmless.
