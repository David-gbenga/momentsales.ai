"""App-level dependencies (settings, Redis, provider registry, flags, email)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

from momentsales.adapters.email.base import EmailMessage, EmailSendError
from momentsales.core.config import Settings
from momentsales.core.errors import ProblemError
from momentsales.core.logging import get_logger
from momentsales.modules.flags.service import FeatureFlagService
from momentsales.modules.providers.registry import ProviderConfigError, ProviderRegistry
from momentsales.tenancy.deps import DbSession

log = get_logger(__name__)


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_app_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


def get_registry(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.registry
    return registry


def get_flags(request: Request) -> FeatureFlagService:
    return FeatureFlagService(request.app.state.redis)


AppSettings = Annotated[Settings, Depends(get_app_settings)]
AppRedis = Annotated[Redis, Depends(get_app_redis)]
Registry = Annotated[ProviderRegistry, Depends(get_registry)]
Flags = Annotated[FeatureFlagService, Depends(get_flags)]


async def send_email(registry: ProviderRegistry, session: DbSession, message: EmailMessage) -> None:
    """Send now, or fail the request with 503 so the caller can retry safely."""
    try:
        sender = await registry.email(session)
        await sender.send(message)
    except (EmailSendError, ProviderConfigError) as exc:
        log.error("email_send_failed", error=str(exc), subject=message.subject)
        raise ProblemError(
            503,
            "email_unavailable",
            "Email could not be sent",
            "We couldn't send the email just now. Please try again in a minute.",
        ) from exc
