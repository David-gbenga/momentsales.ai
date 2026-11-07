"""Google OIDC adapter against recorded discovery/token responses and a test RSA key."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

from momentsales.adapters.identity.base import IdentityError
from momentsales.adapters.identity.google import DISCOVERY_URL, GoogleIdentityAdapter

DISCOVERY = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
}
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeJwks:
    class _K:
        key = KEY.public_key()

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return self._K()


def _id_token(**overrides: Any) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "client-123",
        "sub": "1100220033",
        "email": "seller@example.com",
        "email_verified": True,
        "nonce": "n-1",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "name": "Sam Seller",
    }
    claims.update(overrides)
    return jwt.encode(claims, KEY, algorithm="RS256")


async def _adapter(http: httpx.AsyncClient) -> GoogleIdentityAdapter:
    adapter = GoogleIdentityAdapter("client-123", "secret", http)
    await adapter._config()
    adapter._jwks = _FakeJwks()  # type: ignore[assignment]
    return adapter


@respx.mock
async def test_authorization_url() -> None:
    respx.get(DISCOVERY_URL).mock(return_value=httpx.Response(200, json=DISCOVERY))
    async with httpx.AsyncClient() as http:
        adapter = await _adapter(http)
        url = await adapter.authorization_url(state="s", nonce="n", redirect_uri="http://x/cb")
    q = parse_qs(urlparse(url).query)
    assert url.startswith(DISCOVERY["authorization_endpoint"])
    assert q["scope"] == ["openid email profile"] and q["response_type"] == ["code"]
    assert q["state"] == ["s"] and q["nonce"] == ["n"]


@respx.mock
async def test_exchange_validates_and_returns_identity() -> None:
    respx.get(DISCOVERY_URL).mock(return_value=httpx.Response(200, json=DISCOVERY))
    token_route = respx.post(DISCOVERY["token_endpoint"]).mock(
        return_value=httpx.Response(200, json={"id_token": _id_token(), "token_type": "Bearer"})
    )
    async with httpx.AsyncClient() as http:
        adapter = await _adapter(http)
        ident = await adapter.exchange(code="c", redirect_uri="http://x/cb", nonce="n-1")
    assert ident.subject == "1100220033" and ident.email_verified
    form = parse_qs(token_route.calls.last.request.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["c"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"nonce": "other"},
        {"aud": "someone-else"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 10},
    ],
)
@respx.mock
async def test_exchange_rejects_bad_tokens(overrides: dict[str, Any]) -> None:
    respx.get(DISCOVERY_URL).mock(return_value=httpx.Response(200, json=DISCOVERY))
    respx.post(DISCOVERY["token_endpoint"]).mock(
        return_value=httpx.Response(200, json={"id_token": _id_token(**overrides)})
    )
    async with httpx.AsyncClient() as http:
        adapter = await _adapter(http)
        with pytest.raises(IdentityError):
            await adapter.exchange(code="c", redirect_uri="http://x/cb", nonce="n-1")


@respx.mock
async def test_string_email_verified_is_accepted() -> None:
    respx.get(DISCOVERY_URL).mock(return_value=httpx.Response(200, json=DISCOVERY))
    respx.post(DISCOVERY["token_endpoint"]).mock(
        return_value=httpx.Response(200, json={"id_token": _id_token(email_verified="true")})
    )
    async with httpx.AsyncClient() as http:
        adapter = await _adapter(http)
        ident = await adapter.exchange(code="c", redirect_uri="http://x/cb", nonce="n-1")
    assert ident.email_verified
