"""Application settings, read from environment variables (12-factor).

Every key is documented in `.env.example`. Non-local environments refuse to start
with development placeholder secrets or mock identity.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[5]
_DEV_TOTP_KEY = "xb5Yb0bcm4o0r9bK0wQ1Zc5i8iX0b2S3dE4fG5hI6jY="


class Env(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ProviderMode(StrEnum):
    MOCK = "mock"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Env = Env.LOCAL
    log_level: str = "info"
    web_base_url: str = "http://localhost:3000"
    cookie_secure: bool = True

    database_url: str = "postgresql+psycopg://app_rw:change-me-rw@localhost:5432/momentsales"
    migration_database_url: str = (
        "postgresql+psycopg://app_owner:change-me-owner@localhost:5432/momentsales"
    )
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: SecretStr = SecretStr("change-me-jwt-secret-at-least-32-characters-long")
    signing_secret: SecretStr = SecretStr("change-me-signing-secret-at-least-32-characters")
    totp_encryption_key: SecretStr = SecretStr(_DEV_TOTP_KEY)
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=90)
    magic_link_ttl_minutes: int = Field(default=15, ge=5, le=60)
    invitation_ttl_days: int = Field(default=7, ge=1, le=30)
    rate_limit_enabled: bool = True

    provider_mode: ProviderMode = ProviderMode.MOCK
    provider_email: Literal["resend", "postmark"] = "resend"
    provider_email_secondary: Literal["resend", "postmark"] | None = "postmark"
    provider_email_failover: Literal["auto", "manual"] = "auto"
    provider_identity: Literal["google"] = "google"
    email_from: str = "momentsales.ai <hello@momentsales.ai>"
    resend_api_key: SecretStr | None = None
    postmark_server_token: SecretStr | None = None
    postmark_message_stream: str = "outbound"
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None

    @property
    def is_local(self) -> bool:
        return self.env in (Env.LOCAL, Env.TEST)

    @property
    def dev_tools_enabled(self) -> bool:
        """Dev-only endpoints (mock IdP, email outbox) exist only locally in mock mode."""
        return self.is_local and self.provider_mode is ProviderMode.MOCK

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.web_base_url.rstrip('/')}/api/v1/auth/google/callback"

    @model_validator(mode="after")
    def _refuse_dev_secrets_outside_local(self) -> Settings:
        if self.is_local:
            return self
        problems: list[str] = []
        for name in ("jwt_secret", "signing_secret"):
            value: SecretStr = getattr(self, name)
            if "change-me" in value.get_secret_value() or len(value.get_secret_value()) < 32:
                problems.append(f"{name} must be a real secret of 32+ characters")
        if self.totp_encryption_key.get_secret_value() == _DEV_TOTP_KEY:
            problems.append("totp_encryption_key must not be the development key")
        if "change-me" in self.database_url:
            problems.append("database_url uses a development password")
        if not self.cookie_secure:
            problems.append("cookie_secure must be true outside local")
        if self.provider_mode is ProviderMode.MOCK and self.env is Env.PRODUCTION:
            problems.append("PROVIDER_MODE=mock is not allowed in production")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
