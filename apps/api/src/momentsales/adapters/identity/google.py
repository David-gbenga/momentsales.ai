"""Google OpenID Connect (authorization code flow). See docs/integrations/google-oidc.md.

Endpoints come from Google's discovery document, as Google recommends.
The ID token is validated locally against the keys at `jwks_uri`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from momentsales.adapters.identity.base import IdentityError, VerifiedIdentity

DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
ALLOWED_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
_DISCOVERY_TTL = 3600


class GoogleIdentityAdapter:
    name = "google"

    def __init__(self, client_id: str, client_secret: str, client: httpx.AsyncClient) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = client
        self._discovery: dict[str, Any] | None = None
        self._discovery_at = 0.0
        self._jwks: jwt.PyJWKClient | None = None

    async def _config(self) -> dict[str, Any]:
        if self._discovery is None or time.monotonic() - self._discovery_at > _DISCOVERY_TTL:
            resp = await self._http.get(DISCOVERY_URL, timeout=10)
            resp.raise_for_status()
            self._discovery = resp.json()
            self._discovery_at = time.monotonic()
            self._jwks = jwt.PyJWKClient(self._discovery["jwks_uri"])
        return self._discovery

    async def authorization_url(self, *, state: str, nonce: str, redirect_uri: str) -> str:
        cfg = await self._config()
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "prompt": "select_account",
        }
        return f"{cfg['authorization_endpoint']}?{urlencode(params)}"

    async def exchange(self, *, code: str, redirect_uri: str, nonce: str) -> VerifiedIdentity:
        cfg = await self._config()
        try:
            resp = await self._http.post(
                cfg["token_endpoint"],
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=10,
            )
        except httpx.HTTPError as exc:
            raise IdentityError("token request failed") from exc
        if resp.status_code != 200:
            raise IdentityError(f"token endpoint returned {resp.status_code}")
        id_token = resp.json().get("id_token")
        if not id_token:
            raise IdentityError("no id_token in token response")
        claims = await asyncio.to_thread(self._verify, id_token)
        if claims.get("nonce") != nonce:
            raise IdentityError("nonce mismatch")
        verified = claims.get("email_verified") in (True, "true")
        email = claims.get("email")
        if not email:
            raise IdentityError("email scope missing")
        return VerifiedIdentity(
            provider=self.name,
            subject=str(claims["sub"]),
            email=str(email),
            email_verified=verified,
            name=claims.get("name"),
        )

    def _verify(self, id_token: str) -> dict[str, Any]:
        assert self._jwks is not None
        try:
            key = self._jwks.get_signing_key_from_jwt(id_token)
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                options={"require": ["iss", "aud", "exp", "sub"], "verify_iss": False},
            )
        except jwt.PyJWTError as exc:
            raise IdentityError(f"invalid id_token: {exc}") from exc
        if claims.get("iss") not in ALLOWED_ISSUERS:
            raise IdentityError("unexpected issuer")
        return claims
