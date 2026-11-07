from __future__ import annotations

import httpx
from helpers import last_email_to, link_token, unique_email


async def test_consent_text_endpoint(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/waitlist/consent")
    assert r.status_code == 200 and r.json()["version"] == "waitlist-v1"


async def test_requires_consent(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/waitlist", json={"email": unique_email(), "consent": False})
    assert r.status_code == 422 and r.json()["type"] == "consent_required"


async def test_double_opt_in_and_unsubscribe(client: httpx.AsyncClient) -> None:
    email = unique_email("wl")
    r = await client.post(
        "/api/v1/waitlist", json={"email": email, "consent": True, "source": "landing"}
    )
    assert r.status_code == 202
    token = await link_token(email, "/waitlist/confirm")
    r = await client.post("/api/v1/waitlist/confirm", json={"token": token})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert (await client.post("/api/v1/waitlist/confirm", json={"token": token})).status_code == 400
    unsub = await link_token(email, "/waitlist/unsubscribe")
    r = await client.post("/api/v1/waitlist/unsubscribe", json={"token": unsub})
    assert r.status_code == 200 and r.json()["status"] == "unsubscribed"


async def test_repeat_signup_does_not_reveal_membership(client: httpx.AsyncClient) -> None:
    email = unique_email("wl")
    await client.post("/api/v1/waitlist", json={"email": email, "consent": True})
    first = await last_email_to(email)
    r = await client.post("/api/v1/waitlist", json={"email": email, "consent": True})
    assert r.status_code == 202 and r.json()["status"] == "check_your_email"
    assert (await last_email_to(email))["id"] != first["id"]  # unconfirmed → resend
