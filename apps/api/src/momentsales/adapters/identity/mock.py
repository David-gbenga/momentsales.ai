"""Mock identity provider for PROVIDER_MODE=mock (local only).

The "consent screen" is the web page /dev/mock-google. It asks for an email and
redirects to our callback with a signed code. Config refuses mock mode in production.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlencode

from momentsales.adapters.identity.base import IdentityError, VerifiedIdentity
from momentsales.core.config import Settings
from momentsales.core.security import sign, unsign


class MockIdentityAdapter:
    name = "mock-google"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def authorization_url(self, *, state: str, nonce: str, redirect_uri: str) -> str:
        params = urlencode({"state": state, "nonce": nonce, "redirect_uri": redirect_uri})
        return f"{self._settings.web_base_url.rstrip('/')}/dev/mock-google?{params}"

    def issue_code(self, email: str, nonce: str, name: str | None = None) -> str:
        """Used by the dev endpoint behind the mock consent page."""
        return sign(
            {"k": "mock_idp", "email": email, "nonce": nonce, "name": name},
            ttl_seconds=300,
            settings=self._settings,
        )

    async def exchange(self, *, code: str, redirect_uri: str, nonce: str) -> VerifiedIdentity:
        data = unsign(code, self._settings)
        if not data or data.get("k") != "mock_idp":
            raise IdentityError("invalid mock code")
        if data.get("nonce") != nonce:
            raise IdentityError("nonce mismatch")
        email = str(data["email"])
        return VerifiedIdentity(
            provider=self.name,
            subject="mock-" + hashlib.sha256(email.lower().encode()).hexdigest()[:32],
            email=email,
            email_verified=True,
            name=data.get("name"),
        )
