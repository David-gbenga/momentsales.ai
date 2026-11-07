"""Test helpers: sign up through the real magic-link flow and read the mock outbox."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

import httpx

from momentsales.adapters.email.mock import read_outbox
from momentsales.core.config import get_settings
from momentsales.core.redis import get_redis

XRW = {"X-Requested-With": "momentsales"}


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


async def last_email_to(address: str) -> dict[str, object]:
    for msg in await read_outbox(get_redis(get_settings().redis_url), 200):
        if str(msg["to"]).lower() == address.lower():
            return msg
    raise AssertionError(f"no email to {address}")


async def link_token(address: str, path: str) -> str:
    msg = await last_email_to(address)
    match = re.search(rf"{re.escape(path)}\?token=([^\s]+)", str(msg["text"]))
    assert match, f"no {path} link in email"
    return match.group(1)


@dataclass
class Session:
    email: str
    access_token: str
    user_id: str
    client: httpx.AsyncClient

    def headers(self, workspace_id: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.access_token}"}
        if workspace_id:
            h["X-Workspace-Id"] = workspace_id
        return h


async def sign_in(client: httpx.AsyncClient, email: str | None = None) -> Session:
    email = email or unique_email()
    r = await client.post("/api/v1/auth/magic-link", json={"email": email})
    assert r.status_code == 202, r.text
    token = await link_token(email, "/auth/verify")
    r = await client.post("/api/v1/auth/magic-link/verify", json={"token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "signed_in", body
    return Session(email, body["access_token"], body["user"]["id"], client)


async def create_workspace(s: Session, name: str = "Test Shop") -> str:
    r = await s.client.post("/api/v1/workspaces", json={"name": name}, headers=s.headers())
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def add_member(owner: Session, workspace_id: str, role: str) -> Session:
    """Invite a fresh user with `role` and accept as them."""
    member_email = unique_email(role)
    r = await owner.client.post(
        "/api/v1/invitations",
        json={"email": member_email, "role": role},
        headers=owner.headers(workspace_id),
    )
    assert r.status_code == 201, r.text
    token = await link_token(member_email, "/invite/accept")
    member = await sign_in(owner.client, member_email)
    r = await owner.client.post(
        "/api/v1/invitations/accept", json={"token": token}, headers=member.headers()
    )
    assert r.status_code == 200, r.text
    return member
