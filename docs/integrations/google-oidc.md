# Google OpenID Connect ("Continue with Google")

Adapter: `apps/api/src/momentsales/adapters/identity/google.py`. Mock: `adapters/identity/mock.py` plus the web page `/dev/mock-google`, used when `PROVIDER_MODE=mock`.

Checked against https://developers.google.com/identity/openid-connect/openid-connect on 2026-11-07.

| Item | Value |
| --- | --- |
| Discovery | `https://accounts.google.com/.well-known/openid-configuration`. We read `authorization_endpoint`, `token_endpoint` and `jwks_uri` from it (cached 1 h), as Google recommends, instead of hard-coding them |
| Authorization request | GET `authorization_endpoint` with `client_id`, `response_type=code`, `scope=openid email profile`, `redirect_uri`, `state`, `nonce`, `prompt=select_account` |
| Token request | POST `token_endpoint`, form-encoded: `code`, `client_id`, `client_secret`, `redirect_uri`, `grant_type=authorization_code` |
| ID token checks | Signature (RS256, keys from `jwks_uri`); `iss` ∈ {`https://accounts.google.com`, `accounts.google.com`}; `aud` = our client id; `exp` not passed; `nonce` equals what we sent; `email_verified` true (accepts boolean or `"true"`) |
| User key | `sub` (never the email). An existing email account is linked only when Google says the email is verified |
| Redirect URI | `${WEB_BASE_URL}/api/v1/auth/google/callback` |

PKCE isn't documented on that page, so it isn't used. The flow is a confidential server-side client with `state` (in a signed, `SameSite=Lax`, 10-minute cookie) and `nonce`.

Setup for live use: create an OAuth client (type "Web application") in Google Cloud in **testing** mode, add the redirect URI, and set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
