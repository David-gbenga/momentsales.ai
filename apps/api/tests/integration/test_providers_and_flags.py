"""Provider registry (FR-15) and feature-flag resolution."""

from __future__ import annotations

import uuid

import httpx
import pytest
from helpers import create_workspace, sign_in
from sqlalchemy import delete

from momentsales.adapters.email.mock import MockEmailAdapter
from momentsales.adapters.email.postmark import PostmarkEmailAdapter
from momentsales.adapters.email.resend import ResendEmailAdapter
from momentsales.adapters.identity.google import GoogleIdentityAdapter
from momentsales.adapters.identity.mock import MockIdentityAdapter
from momentsales.core.config import Env, ProviderMode, Settings
from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.core.redis import get_redis
from momentsales.db.models import FeatureFlagOverride, ProviderConfig
from momentsales.modules.flags.service import FeatureFlagService
from momentsales.modules.providers.registry import ProviderConfigError, ProviderRegistry


def _settings(**kw: object) -> Settings:
    return Settings(env=Env.LOCAL, **kw)  # type: ignore[arg-type]


async def _registry(settings: Settings) -> tuple[ProviderRegistry, httpx.AsyncClient]:
    http = httpx.AsyncClient()
    return ProviderRegistry(settings, get_redis(settings.redis_url), http), http


async def test_mock_mode_uses_mocks() -> None:
    reg, http = await _registry(_settings(provider_mode=ProviderMode.MOCK))
    async with http, get_sessionmaker()() as s, s.begin():
        sender = await reg.email(s)
        assert isinstance(sender.primary, MockEmailAdapter) and sender.secondary is None
        assert isinstance(await reg.identity(s), MockIdentityAdapter)


async def test_live_mode_wires_primary_secondary_and_failover() -> None:
    reg, http = await _registry(
        _settings(
            provider_mode=ProviderMode.LIVE,
            resend_api_key="re_x",
            postmark_server_token="pm_x",
            google_client_id="cid",
            google_client_secret="sec",
        )
    )
    async with http, get_sessionmaker()() as s, s.begin():
        sender = await reg.email(s)
        assert isinstance(sender.primary, ResendEmailAdapter)
        assert isinstance(sender.secondary, PostmarkEmailAdapter) and sender.auto
        google = await reg.identity(s)
        assert isinstance(google, GoogleIdentityAdapter)
        assert await reg.identity(s) is google  # cached, keeps discovery/JWKS


async def test_switch_primary_by_env() -> None:
    reg, http = await _registry(
        _settings(
            provider_mode=ProviderMode.LIVE,
            provider_email="postmark",
            provider_email_secondary="resend",
            provider_email_failover="manual",
            postmark_server_token="pm_x",
        )
    )
    async with http, get_sessionmaker()() as s, s.begin():
        sender = await reg.email(s)
        assert isinstance(sender.primary, PostmarkEmailAdapter)
        assert sender.secondary is None  # resend key missing → secondary skipped, logged
        assert not sender.auto


async def test_live_mode_without_keys_is_a_config_error() -> None:
    reg, http = await _registry(_settings(provider_mode=ProviderMode.LIVE))
    async with http, get_sessionmaker()() as s, s.begin():
        with pytest.raises(ProviderConfigError, match="RESEND_API_KEY"):
            await reg.email(s)
        with pytest.raises(ProviderConfigError, match="GOOGLE_CLIENT_ID"):
            await reg.identity(s)


async def test_db_row_overrides_env() -> None:
    reg, http = await _registry(
        _settings(provider_mode=ProviderMode.MOCK, postmark_server_token="pm")
    )
    async with http, get_sessionmaker()() as s, s.begin():
        s.add(
            ProviderConfig(
                category="email", primary="postmark", secondary=None, mode="live", failover="manual"
            )
        )
        await s.flush()
        resolved = await reg.resolve(s, "email")
        assert resolved.source == "db" and resolved.mode == "live"
        assert isinstance((await reg.email(s)).primary, PostmarkEmailAdapter)
        listing = {p.category: p for p in await reg.describe_all(s)}
        assert set(listing) >= {"email", "identity", "market_data", "supplier", "ocr", "llm"}
        assert listing["market_data"].built == ()
        await s.execute(delete(ProviderConfig))


async def test_production_never_resolves_mock_from_db() -> None:
    strong = "s" * 40
    prod = Settings(
        env=Env.PRODUCTION,
        jwt_secret=strong,  # type: ignore[arg-type]
        signing_secret=strong,  # type: ignore[arg-type]
        totp_encryption_key="Z" * 43 + "=",  # type: ignore[arg-type]
        database_url="postgresql+psycopg://app_rw:real@db/ms",
        cookie_secure=True,
        stripe_webhook_secret="whsec_real_platform",  # type: ignore[arg-type]
        stripe_connect_webhook_secret="whsec_real_connect",  # type: ignore[arg-type]
        storage_backend="s3",
        provider_mode=ProviderMode.LIVE,
    )
    reg, http = await _registry(prod)
    async with http, get_sessionmaker()() as s, s.begin():
        s.add(ProviderConfig(category="email", primary="resend", mode="mock", failover="auto"))
        await s.flush()
        with pytest.raises(ProviderConfigError, match="production"):
            await reg.resolve(s, "email")
        with pytest.raises(ProviderConfigError, match="unknown"):
            await reg.resolve(s, "nope")
        await s.rollback()


async def test_flag_override_and_cache(client: httpx.AsyncClient) -> None:
    owner = await sign_in(client)
    ws = uuid.UUID(await create_workspace(owner))
    redis = get_redis(Settings().redis_url)
    flags = FeatureFlagService(redis)
    async with get_sessionmaker()() as s, s.begin():
        await set_tenant_context(s, user_id=uuid.UUID(owner.user_id), workspace_id=ws)
        assert not await flags.is_enabled(s, "connector.shopify", ws)
        s.add(FeatureFlagOverride(workspace_id=ws, flag_key="connector.shopify", enabled=True))
        await s.flush()
        assert not await flags.is_enabled(s, "connector.shopify", ws)  # cached for 30s
        await flags.invalidate(ws)
        assert await flags.is_enabled(s, "connector.shopify", ws)
        assert await flags.enabled_countries(s) == ["GB"]
        assert not await FeatureFlagService(None).is_enabled(s, "missing.flag")
    r = await client.get("/api/v1/flags", headers=owner.headers(str(ws)))
    body = r.json()["flags"]
    assert body["country.GB"] is True and body["module.audience_research"] is False
