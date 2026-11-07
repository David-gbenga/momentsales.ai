"""Provider registry (FR-15, ADR-0007).

Resolution order per category: a `provider_configs` DB row (set by a platform admin)
overrides the environment (`PROVIDER_MODE`, `PROVIDER_<CATEGORY>`, ...).
`PROVIDER_MODE=mock` swaps every adapter for its mock, so the app runs with no keys.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.email.base import EmailMessage, EmailPort, EmailSendError, SendResult
from momentsales.adapters.email.mock import MockEmailAdapter
from momentsales.adapters.email.postmark import PostmarkEmailAdapter
from momentsales.adapters.email.resend import ResendEmailAdapter
from momentsales.adapters.identity.base import IdentityPort
from momentsales.adapters.identity.google import GoogleIdentityAdapter
from momentsales.adapters.identity.mock import MockIdentityAdapter
from momentsales.core.config import Env, ProviderMode, Settings
from momentsales.core.logging import get_logger
from momentsales.db.models import ProviderConfig

log = get_logger(__name__)

# Categories and the adapters built so far. Others are listed so the admin view shows
# what is still to come; they get adapters in the phase that needs them.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "email": ("resend", "postmark"),
    "identity": ("google",),
    "market_data": (),  # Phase 2
    "supplier": (),  # Phase 2
    "ocr": (),  # Phase 1
    "llm": (),  # Phase 2
}


class ProviderConfigError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    category: str
    primary: str | None
    secondary: str | None
    mode: Literal["mock", "live"]
    failover: Literal["auto", "manual"]
    source: Literal["env", "db"]
    built: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["built"] = list(self.built)
        return d


class FailoverEmailSender:
    """Sends through the primary; on a retryable error, tries the secondary (if auto)."""

    def __init__(self, primary: EmailPort, secondary: EmailPort | None, auto: bool) -> None:
        self.primary = primary
        self.secondary = secondary
        self.auto = auto

    async def send(self, message: EmailMessage) -> SendResult:
        try:
            return await self.primary.send(message)
        except EmailSendError as exc:
            if not (exc.retryable and self.auto and self.secondary is not None):
                raise
            log.warning("email_failover", primary=self.primary.name, error=str(exc))
            return await self.secondary.send(message)


class ProviderRegistry:
    def __init__(self, settings: Settings, redis: Redis, http: httpx.AsyncClient) -> None:
        self._s = settings
        self._redis = redis
        self._http = http
        self._google: GoogleIdentityAdapter | None = None

    def _env(self, category: str) -> ResolvedProvider:
        s = self._s
        mode: Literal["mock", "live"] = "mock" if s.provider_mode is ProviderMode.MOCK else "live"
        if category == "email":
            return ResolvedProvider(
                category,
                s.provider_email,
                s.provider_email_secondary,
                mode,
                s.provider_email_failover,
                "env",
                CATEGORIES[category],
            )
        if category == "identity":
            return ResolvedProvider(
                category, s.provider_identity, None, mode, "manual", "env", CATEGORIES[category]
            )
        return ResolvedProvider(category, None, None, mode, "manual", "env", CATEGORIES[category])

    async def resolve(self, session: AsyncSession, category: str) -> ResolvedProvider:
        if category not in CATEGORIES:
            raise ProviderConfigError(f"unknown provider category {category!r}")
        row = await session.get(ProviderConfig, category)
        if row is None:
            resolved = self._env(category)
        else:
            resolved = ResolvedProvider(
                category,
                row.primary,
                row.secondary,
                "mock" if row.mode == "mock" else "live",
                "auto" if row.failover == "auto" else "manual",
                "db",
                CATEGORIES[category],
            )
        if resolved.mode == "mock" and self._s.env is Env.PRODUCTION:
            raise ProviderConfigError(f"{category}: mock mode is not allowed in production")
        return resolved

    async def describe_all(self, session: AsyncSession) -> list[ResolvedProvider]:
        return [await self.resolve(session, c) for c in CATEGORIES]

    # --- Email -------------------------------------------------------------------------

    def _email_adapter(self, name: str) -> EmailPort:
        s = self._s
        if name == "resend":
            if not s.resend_api_key:
                raise ProviderConfigError("RESEND_API_KEY is not set")
            return ResendEmailAdapter(s.resend_api_key.get_secret_value(), s.email_from, self._http)
        if name == "postmark":
            if not s.postmark_server_token:
                raise ProviderConfigError("POSTMARK_SERVER_TOKEN is not set")
            return PostmarkEmailAdapter(
                s.postmark_server_token.get_secret_value(),
                s.email_from,
                self._http,
                s.postmark_message_stream,
            )
        raise ProviderConfigError(f"unknown email provider {name!r}")

    async def email(self, session: AsyncSession) -> FailoverEmailSender:
        cfg = await self.resolve(session, "email")
        if cfg.mode == "mock":
            return FailoverEmailSender(MockEmailAdapter(self._redis), None, auto=False)
        if cfg.primary is None:
            raise ProviderConfigError("email: no primary provider configured")
        primary = self._email_adapter(cfg.primary)
        secondary = None
        if cfg.secondary and cfg.secondary != cfg.primary:
            try:
                secondary = self._email_adapter(cfg.secondary)
            except ProviderConfigError:
                log.warning("email_secondary_unconfigured", secondary=cfg.secondary)
        return FailoverEmailSender(primary, secondary, auto=cfg.failover == "auto")

    # --- Identity ----------------------------------------------------------------------

    async def identity(self, session: AsyncSession) -> IdentityPort:
        cfg = await self.resolve(session, "identity")
        if cfg.mode == "mock":
            return MockIdentityAdapter(self._s)
        s = self._s
        if not (s.google_client_id and s.google_client_secret):
            raise ProviderConfigError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")
        if self._google is None:  # reuse so the discovery doc and JWKS stay cached
            self._google = GoogleIdentityAdapter(
                s.google_client_id, s.google_client_secret.get_secret_value(), self._http
            )
        return self._google
