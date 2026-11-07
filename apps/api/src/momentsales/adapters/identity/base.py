"""Identity provider port ("Continue with Google")."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    provider: str
    subject: str  # stable unique id at the provider (Google `sub`)
    email: str
    email_verified: bool
    name: str | None = None


class IdentityError(Exception):
    pass


class IdentityPort(Protocol):
    name: str

    async def authorization_url(self, *, state: str, nonce: str, redirect_uri: str) -> str: ...

    async def exchange(self, *, code: str, redirect_uri: str, nonce: str) -> VerifiedIdentity: ...
