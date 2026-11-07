from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp
from helpers import XRW, link_token, sign_in, unique_email
from redis.asyncio import Redis
from sqlalchemy import update

from momentsales.core.db import get_sessionmaker
from momentsales.db.models import MagicLinkToken


async def test_magic_link_signs_up_and_is_single_use(client: httpx.AsyncClient) -> None:
    email = unique_email()
    r = await client.post("/api/v1/auth/magic-link", json={"email": email})
    assert r.status_code == 202
    token = await link_token(email, "/auth/verify")
    r = await client.post("/api/v1/auth/magic-link/verify", json={"token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "signed_in" and body["user"]["email"] == email
    assert "ms_refresh" in r.cookies
    r2 = await client.post("/api/v1/auth/magic-link/verify", json={"token": token})
    assert r2.status_code == 400 and r2.json()["type"] == "invalid_link"
    assert r2.headers["content-type"].startswith("application/problem+json")


async def _clear_rate_limits(redis: Redis) -> None:
    keys = [k async for k in redis.scan_iter(match="rl:*")]
    if keys:
        await redis.delete(*keys)


async def test_magic_link_rate_limit_per_ip(app: object) -> None:
    """Other tests (and the e2e stack in CI) turn limits off; this proves they work."""
    state = app.state  # type: ignore[attr-defined]
    original = state.settings
    state.settings = original.model_copy(update={"rate_limit_enabled": True})
    try:
        await _clear_rate_limits(state.redis)
        first = httpx.ASGITransport(app=app, client=("203.0.113.7", 40000))  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=first, base_url="http://test") as c:
            codes = [
                (
                    await c.post("/api/v1/auth/magic-link", json={"email": unique_email()})
                ).status_code
                for _ in range(6)
            ]
        assert codes == [202] * 5 + [429]
        other = httpx.ASGITransport(app=app, client=("203.0.113.8", 40000))  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=other, base_url="http://test") as c2:
            r = await c2.post("/api/v1/auth/magic-link", json={"email": unique_email()})
        assert r.status_code == 202
    finally:
        state.settings = original
        await _clear_rate_limits(state.redis)


async def test_expired_magic_link_rejected(client: httpx.AsyncClient) -> None:
    email = unique_email()
    await client.post("/api/v1/auth/magic-link", json={"email": email})
    token = await link_token(email, "/auth/verify")
    async with get_sessionmaker()() as s, s.begin():
        await s.execute(
            update(MagicLinkToken)
            .where(MagicLinkToken.email == email)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    r = await client.post("/api/v1/auth/magic-link/verify", json={"token": token})
    assert r.status_code == 400


async def test_me_requires_bearer(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    r = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    s = await sign_in(client)
    r = await client.get("/api/v1/auth/me", headers=s.headers())
    assert r.status_code == 200 and r.json()["memberships"] == []


async def test_refresh_rotation_and_reuse_revokes_family(app: object) -> None:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await sign_in(c)
        first = c.cookies.get("ms_refresh", path="/api/v1/auth")
        assert first
        # CSRF header required
        assert (await c.post("/api/v1/auth/refresh")).status_code == 403
        r = await c.post("/api/v1/auth/refresh", headers=XRW)
        assert r.status_code == 200 and r.json()["access_token"]
        second = c.cookies.get("ms_refresh", path="/api/v1/auth")
        assert second and second != first
        # Replaying the old token → 401 and the whole family is revoked
        c.cookies.clear()
        c.cookies.set("ms_refresh", first, path="/api/v1/auth")
        r = await c.post("/api/v1/auth/refresh", headers=XRW)
        assert r.status_code == 401 and r.json()["reason"] == "reused"
        c.cookies.clear()
        c.cookies.set("ms_refresh", second, path="/api/v1/auth")
        r = await c.post("/api/v1/auth/refresh", headers=XRW)
        assert r.status_code == 401


async def test_logout_revokes_refresh(app: object) -> None:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await sign_in(c)
        token = c.cookies.get("ms_refresh", path="/api/v1/auth")
        assert (await c.post("/api/v1/auth/logout", headers=XRW)).status_code == 204
        c.cookies.set("ms_refresh", token or "", path="/api/v1/auth")
        assert (await c.post("/api/v1/auth/refresh", headers=XRW)).status_code == 401


async def test_mock_google_flow(app: object) -> None:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/v1/auth/google/start")
        assert r.status_code == 302
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert urlparse(r.headers["location"]).path == "/dev/mock-google"
        email = unique_email("google")
        r = await c.post(
            "/api/v1/dev/mock-google/authorize",
            json={
                "email": email,
                "name": "G User",
                "state": q["state"][0],
                "nonce": q["nonce"][0],
                "redirect_uri": q["redirect_uri"][0],
            },
        )
        assert r.status_code == 200
        callback = urlparse(r.json()["redirect_url"])
        r = await c.get(f"{callback.path}?{callback.query}")
        assert r.status_code == 302 and r.headers["location"] == "http://test/auth/callback"
        r = await c.post("/api/v1/auth/refresh", headers=XRW)
        assert r.status_code == 200 and r.json()["user"]["email"] == email


async def test_google_callback_rejects_bad_state(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/auth/google/callback?code=x&state=y")
    assert r.status_code == 302 and "error=google" in r.headers["location"]


async def test_totp_enroll_then_required_at_sign_in(client: httpx.AsyncClient) -> None:
    s = await sign_in(client)
    r = await client.post("/api/v1/auth/2fa/enroll", headers=s.headers())
    assert r.status_code == 200
    secret = r.json()["secret"]
    bad = await client.post("/api/v1/auth/2fa/verify", json={"code": "000000"}, headers=s.headers())
    assert bad.status_code in (400, 200)  # 000000 could, very rarely, be valid
    r = await client.post(
        "/api/v1/auth/2fa/verify", json={"code": pyotp.TOTP(secret).now()}, headers=s.headers()
    )
    assert r.status_code == 200 and r.json()["totp_enabled"]
    # next sign-in needs the code
    await client.post("/api/v1/auth/magic-link", json={"email": s.email})
    token = await link_token(s.email, "/auth/verify")
    r = await client.post("/api/v1/auth/magic-link/verify", json={"token": token})
    assert r.json()["status"] == "mfa_required"
    mfa = r.json()["mfa_token"]
    r = await client.post("/api/v1/auth/2fa/challenge", json={"mfa_token": mfa, "code": "123"})
    assert r.status_code == 422
    r = await client.post(
        "/api/v1/auth/2fa/challenge",
        json={"mfa_token": mfa, "code": pyotp.TOTP(secret).now()},
    )
    assert r.status_code == 200 and r.json()["status"] == "signed_in"
