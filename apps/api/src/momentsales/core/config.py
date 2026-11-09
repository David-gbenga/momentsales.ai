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
_DEV_INTEGRATION_KEY = "NzxpdScn8PA1JHjPuy6TrWIDMhgbsvNyzHyHonBntVg="


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
    # Fernet key for seller integration credentials (e.g. Avasam keys), ADR-0020.
    integration_encryption_key: SecretStr = SecretStr(_DEV_INTEGRATION_KEY)
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

    # Payments (ADR-0011). Test mode only: live keys are refused below.
    provider_payments: Literal["stripe"] = "stripe"
    stripe_secret_key: SecretStr | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: SecretStr = SecretStr("whsec_mock_platform_secret")
    stripe_connect_webhook_secret: SecretStr = SecretStr("whsec_mock_connect_secret")

    # Background jobs: "celery" (worker) or "inline" (run in-process after the response;
    # tests only)
    jobs_mode: Literal["celery", "inline"] = "celery"

    # SaaS billing (FR-10, ADR-0015). Trial: card required, Growth features, capped runs.
    trial_days: int = Field(default=14, ge=1, le=60)
    trial_runs: int = Field(default=3, ge=0)
    trial_live_listings: int = Field(default=5, ge=0)
    trial_plan_code: str = "growth"
    # Stripe Tax on SaaS invoices; needs registrations in the Stripe account first.
    stripe_tax_enabled: bool = False
    # Portal configuration id from tools/stripe_billing_seed.py (Stripe's default if unset).
    stripe_portal_configuration_id: str | None = None
    # Unit-economics guard (FR-10): price must exceed runs x cap x 3. Cap is £1.00 per run.
    run_cost_cap_minor: int = Field(default=100, ge=0)
    run_cost_cap_currency: str = "GBP"
    # Platform fee (ADR-0017) for workspaces without a trialing or active plan
    # (pay-per-report). Plans carry their own rate in plans.platform_fee_bps.
    platform_fee_bps_no_plan: int = Field(default=200, ge=0, le=5000)
    # Dunning (FR-10): past_due -> suspended after this many days.
    suspend_after_days: int = Field(default=30, ge=1)

    # Research (Phase 2, ADR-0018)
    provider_holidays: Literal["nager"] = "nager"
    nager_base_url: str = "https://nagerholidays.com/api/v4"
    # Delivery time used for act-by dates until a supplier is matched (Phase 2c replaces
    # it with the matched supplier's real maximum). Shown to users as an assumption.
    research_assumed_delivery_days: int = Field(default=5, ge=0, le=60)
    # Runs stuck in queued/running longer than this are resumed by the sweeper.
    research_stuck_after_minutes: int = Field(default=10, ge=1)

    # Market data (Phase 2b, ADR-0019). DataForSEO primary, SerpApi switchable (FR-15).
    provider_market_data: Literal["dataforseo", "serpapi"] = "dataforseo"
    provider_market_data_secondary: Literal["dataforseo", "serpapi"] | None = "serpapi"
    provider_market_data_failover: Literal["auto", "manual"] = "auto"
    dataforseo_login: str | None = None
    dataforseo_password: SecretStr | None = None
    dataforseo_base_url: str = "https://api.dataforseo.com/v3"
    serpapi_api_key: SecretStr | None = None
    serpapi_base_url: str = "https://serpapi.com"
    # Per-call cost estimates in GBP micros (1,000,000 = £1), for the run cost cap only.
    # From the providers' USD prices (integration notes); update from real invoices.
    cost_dataforseo_trends_micros: int = Field(default=9_000, ge=0)
    cost_dataforseo_serp_micros: int = Field(default=2_000, ge=0)
    cost_serpapi_search_micros: int = Field(default=20_000, ge=0)
    # Platform-wide paid market-data spend per calendar month; past it, cache only.
    market_data_monthly_budget_micros: int = Field(default=40_000_000, ge=0)
    # Shared cache reuse window (owner decision: 24 h, matching the stale badge).
    market_cache_max_age_hours: int = Field(default=24, ge=1, le=168)
    research_max_anchors: int = Field(default=4, ge=1, le=10)
    research_max_candidates: int = Field(default=20, ge=1, le=50)
    research_marketplace_top_n: int = Field(default=12, ge=1, le=30)
    research_growth_recent_weeks: int = Field(default=3, ge=2, le=4)
    research_growth_baseline_weeks: int = Field(default=6, ge=4, le=8)
    research_peak_ratio: float = Field(default=0.9, gt=0, le=1)
    research_min_interest: int = Field(default=5, ge=0, le=100)
    research_competition_weights: dict[str, float] = Field(
        default_factory=lambda: {"results": 0.25, "ads": 0.35, "merchants": 0.2, "spread": 0.2}
    )

    # --- Suppliers (FR-11, FR-15, ADR-0020): manual switch only, never auto failover ----
    provider_supplier: Literal["cj", "avasam"] = "cj"
    provider_supplier_secondary: Literal["cj", "avasam"] | None = "avasam"
    cj_api_key: SecretStr | None = None
    cj_base_url: str = "https://developers.cjdropshipping.com/api2.0/v1"
    cost_cj_call_micros: int = Field(default=0, ge=0)
    avasam_base_url: str = "https://app.avasam.com"
    avasam_max_pages: int = Field(default=5, ge=1, le=50)
    provider_fx: Literal["frankfurter"] = "frankfurter"
    frankfurter_base_url: str = "https://api.frankfurter.dev/v1"
    research_supplier_top_n: int = Field(default=10, ge=1, le=30)
    supplier_match_min_score: float = Field(default=0.5, gt=0, le=1)
    supplier_freight_candidates: int = Field(default=3, ge=1, le=5)
    # Verified UK supplier rule, check 5 (FR-11): net margin floor and its inputs.
    supplier_min_margin_bps: int = Field(default=2500, ge=0, le=9000)
    supplier_returns_allowance_bps: int = Field(default=500, ge=0, le=5000)
    # Stripe's published fee for standard UK cards: 1.5% + 20p (stripe.com/gb/pricing).
    stripe_fee_bps: int = Field(default=150, ge=0, le=1000)
    stripe_fee_fixed_minor: int = Field(default=20, ge=0, le=500)

    provider_ocr: Literal["claude", "textract"] = "claude"
    provider_ocr_secondary: Literal["claude", "textract"] | None = "textract"
    provider_ocr_failover: Literal["auto", "manual"] = "auto"
    anthropic_api_key: SecretStr | None = None
    ocr_claude_model: str = "claude-haiku-5-5"
    aws_region: str = "eu-west-2"
    aws_access_key_id: str | None = None
    aws_secret_access_key: SecretStr | None = None

    # File storage (ADR-0012)
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: Path = _REPO_ROOT / "data" / "storage"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    # Storage has its own credentials so it can live on Cloudflare R2 while Textract uses AWS.
    # Region "auto" for R2. Unset keys fall back to boto3's default credential chain.
    s3_region: str = "auto"
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    signed_url_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    receipt_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)

    @property
    def is_local(self) -> bool:
        return self.env in (Env.LOCAL, Env.TEST)

    @property
    def dev_tools_enabled(self) -> bool:
        """Dev-only endpoints (mock IdP, email outbox) exist only locally in mock mode."""
        return self.is_local and self.provider_mode is ProviderMode.MOCK

    @property
    def stripe_test_mode(self) -> bool:
        """Mock mode, or a live adapter running with test keys (always true until a separately
        approved production switch exists)."""
        key = self.stripe_secret_key.get_secret_value() if self.stripe_secret_key else ""
        return self.provider_mode is ProviderMode.MOCK or key.startswith(("sk_test_", "rk_test_"))

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.web_base_url.rstrip('/')}/api/v1/auth/google/callback"

    @model_validator(mode="after")
    def _refuse_stripe_live_keys(self) -> Settings:
        """CLAUDE.md: Stripe stays in test mode unless the owner explicitly says otherwise."""
        key = self.stripe_secret_key.get_secret_value() if self.stripe_secret_key else ""
        if key.startswith(("sk_live_", "rk_live_")):
            raise ValueError("STRIPE_SECRET_KEY is a live key; only test-mode keys are allowed")
        if (self.stripe_publishable_key or "").startswith("pk_live_"):
            raise ValueError("STRIPE_PUBLISHABLE_KEY is a live key; only test keys are allowed")
        return self

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
        if self.integration_encryption_key.get_secret_value() == _DEV_INTEGRATION_KEY:
            problems.append("integration_encryption_key must not be the development key")
        if "change-me" in self.database_url:
            problems.append("database_url uses a development password")
        if not self.cookie_secure:
            problems.append("cookie_secure must be true outside local")
        for name in ("stripe_webhook_secret", "stripe_connect_webhook_secret"):
            secret: SecretStr = getattr(self, name)
            if "mock" in secret.get_secret_value():
                problems.append(f"{name} must be the endpoint's real signing secret")
        if self.storage_backend == "local":
            problems.append("STORAGE_BACKEND=local is for development only")
        if not self.rate_limit_enabled:
            problems.append("RATE_LIMIT_ENABLED must be true outside local")
        if self.provider_mode is ProviderMode.MOCK and self.env is Env.PRODUCTION:
            problems.append("PROVIDER_MODE=mock is not allowed in production")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
