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

from momentsales.adapters.billing.base import BillingPort
from momentsales.adapters.email.base import EmailMessage, EmailPort, EmailSendError, SendResult
from momentsales.adapters.email.mock import MockEmailAdapter
from momentsales.adapters.email.postmark import PostmarkEmailAdapter
from momentsales.adapters.email.resend import ResendEmailAdapter
from momentsales.adapters.identity.base import IdentityPort
from momentsales.adapters.identity.google import GoogleIdentityAdapter
from momentsales.adapters.identity.mock import MockIdentityAdapter
from momentsales.adapters.ocr.base import OcrError, OcrPort, OcrResult
from momentsales.adapters.ocr.mock import MockOcrAdapter
from momentsales.adapters.payments.base import PaymentsPort
from momentsales.adapters.payments.mock import MockPaymentsAdapter
from momentsales.adapters.storage.base import StoragePort
from momentsales.adapters.storage.local import LocalStorageAdapter
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
    "ocr": ("claude", "textract"),
    "payments": ("stripe",),
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


class FailoverOcr:
    """Primary OCR, then the secondary on a retryable error (FR-15 auto failover)."""

    def __init__(self, primary: OcrPort, secondary: OcrPort | None, auto: bool) -> None:
        self.primary = primary
        self.secondary = secondary
        self.auto = auto

    @property
    def name(self) -> str:
        return self.primary.name

    async def extract(self, content: bytes, mime: str) -> OcrResult:
        try:
            return await self.primary.extract(content, mime)
        except OcrError as exc:
            if not (exc.retryable and self.auto and self.secondary is not None):
                raise
            log.warning("ocr_failover", primary=self.primary.name, error=str(exc))
            return await self.secondary.extract(content, mime)


class ProviderRegistry:
    def __init__(self, settings: Settings, redis: Redis, http: httpx.AsyncClient) -> None:
        self._s = settings
        self._redis = redis
        self._http = http
        self._google: GoogleIdentityAdapter | None = None
        self._storage: StoragePort | None = None

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
        if category == "ocr":
            return ResolvedProvider(
                category,
                s.provider_ocr,
                s.provider_ocr_secondary,
                mode,
                s.provider_ocr_failover,
                "env",
                CATEGORIES[category],
            )
        if category == "payments":
            return ResolvedProvider(
                category, s.provider_payments, None, mode, "manual", "env", CATEGORIES[category]
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

    # --- OCR ---------------------------------------------------------------------------

    def _ocr_adapter(self, name: str) -> OcrPort:
        s = self._s
        if name == "claude":
            if not s.anthropic_api_key:
                raise ProviderConfigError("ANTHROPIC_API_KEY is not set")
            import anthropic

            from momentsales.adapters.ocr.claude import ClaudeOcrAdapter

            client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key.get_secret_value())
            return ClaudeOcrAdapter(client, s.ocr_claude_model)
        if name == "textract":
            if not (s.aws_access_key_id and s.aws_secret_access_key):
                raise ProviderConfigError("AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are not set")
            import boto3

            from momentsales.adapters.ocr.textract import TextractOcrAdapter

            return TextractOcrAdapter(
                boto3.client(
                    "textract",
                    region_name=s.aws_region,
                    aws_access_key_id=s.aws_access_key_id,
                    aws_secret_access_key=s.aws_secret_access_key.get_secret_value(),
                )
            )
        raise ProviderConfigError(f"unknown OCR provider {name!r}")

    async def ocr(self, session: AsyncSession) -> FailoverOcr:
        cfg = await self.resolve(session, "ocr")
        if cfg.primary is None:
            raise ProviderConfigError("ocr: no primary provider configured")
        if cfg.mode == "mock":
            # Mocks carry the configured provider names so switching and failover are testable.
            secondary_mock = MockOcrAdapter(cfg.secondary) if cfg.secondary else None
            return FailoverOcr(
                MockOcrAdapter(cfg.primary), secondary_mock, auto=cfg.failover == "auto"
            )
        primary = self._ocr_adapter(cfg.primary)
        secondary = None
        if cfg.secondary and cfg.secondary != cfg.primary:
            try:
                secondary = self._ocr_adapter(cfg.secondary)
            except ProviderConfigError:
                log.warning("ocr_secondary_unconfigured", secondary=cfg.secondary)
        return FailoverOcr(primary, secondary, auto=cfg.failover == "auto")

    # --- Storage (not a switchable provider category; chosen by STORAGE_BACKEND) --------

    def storage(self) -> StoragePort:
        if self._storage is None:
            s = self._s
            if s.storage_backend == "s3":
                from momentsales.adapters.storage.s3 import S3StorageAdapter, build_s3_client

                if not s.s3_bucket:
                    raise ProviderConfigError("S3_BUCKET is not set")
                # S3_* only: the AWS_* settings belong to Textract (they can be different
                # accounts, e.g. R2 storage with AWS OCR).
                client = build_s3_client(
                    region=s.s3_region,
                    endpoint_url=s.s3_endpoint_url,
                    access_key_id=s.s3_access_key_id,
                    secret=(
                        s.s3_secret_access_key.get_secret_value()
                        if s.s3_secret_access_key
                        else None
                    ),
                )
                self._storage = S3StorageAdapter(client, s.s3_bucket)
            else:
                self._storage = LocalStorageAdapter(s.storage_local_path, s)
        return self._storage

    # --- Payments ----------------------------------------------------------------------

    async def payments(self, session: AsyncSession) -> PaymentsPort:
        cfg = await self.resolve(session, "payments")
        if cfg.mode == "mock":
            return MockPaymentsAdapter(self._redis, self._s.web_base_url)
        if not self._s.stripe_secret_key:
            raise ProviderConfigError("STRIPE_SECRET_KEY is not set")
        from momentsales.adapters.payments.stripe import StripePaymentsAdapter

        return StripePaymentsAdapter.from_key(self._s.stripe_secret_key.get_secret_value())

    # --- Billing (SaaS subscriptions; same Stripe account and mode as payments) ---------

    async def billing(self, session: AsyncSession) -> BillingPort:
        cfg = await self.resolve(session, "payments")
        if cfg.mode == "mock":
            from momentsales.adapters.billing.mock import MockBillingAdapter

            return MockBillingAdapter(self._redis, self._s.web_base_url)
        if not self._s.stripe_secret_key:
            raise ProviderConfigError("STRIPE_SECRET_KEY is not set")
        from momentsales.adapters.billing.stripe import StripeBillingAdapter

        return StripeBillingAdapter.from_key(self._s.stripe_secret_key.get_secret_value())
