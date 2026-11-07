# ADR-0003: Authentication built in FastAPI

- Status: Accepted (Phase 0, chosen by the product owner)
- Date: 2026-11-07

## Context
The PRD asks for email magic link + Google, JWT access + rotating refresh, optional TOTP (required for Owners), and a backend that serves a later mobile app. Hosted auth (Clerk) would be a paid dependency outside our Postgres and RLS, and it would need mocking for `PROVIDER_MODE=mock`.

## Decision
- **Sign-in:** a single-use email magic link (15 min), or Google OpenID Connect (authorization code flow with `state` + `nonce`, ID token verified locally against Google's JWKS). In mock mode, a local "mock Google" consent page stands in for Google. Settings refuse mock mode in production.
- **Sessions:** a 15-minute HS256 access JWT returned in the response body and held in memory by the web app. A 30-day opaque refresh token in an `httpOnly`, `Secure`, `SameSite=Strict` cookie scoped to `/api/v1/auth`. Every refresh rotates the token, and replaying a rotated token revokes the whole token family (theft detection). Cookie endpoints also require `X-Requested-With: momentsales` (CSRF defence: cross-site pages can't set custom headers without CORS).
- **No passwords.** All opaque tokens (magic link, refresh, invitation, waitlist) are 256-bit random values stored only as SHA-256 hashes. Argon2id is unnecessary for high-entropy tokens, so it is not a dependency. If passwords are ever added, they must use Argon2id.
- **TOTP:** RFC 6238 via pyotp. Secrets are Fernet-encrypted at rest (`TOTP_ENCRYPTION_KEY`). When enabled, sign-in returns a 5-minute `mfa_token` and requires the code. "Required for Owners" is enforced when billing lands (Phase 1B).
- Rate limits (Redis, per IP) apply on every auth endpoint. Responses never reveal whether an email has an account.

## Consequences
- Mobile clients will receive the refresh token in the body instead of a cookie (a small addition when L6 starts).
- Live Google sign-in needs a Google Cloud OAuth client (testing mode) and `GOOGLE_CLIENT_ID/SECRET`.
- Production should move `JWT_SECRET`, `SIGNING_SECRET` and `TOTP_ENCRYPTION_KEY` into a secrets manager (Phase 5).
