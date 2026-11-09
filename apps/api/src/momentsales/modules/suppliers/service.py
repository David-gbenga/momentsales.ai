"""Suppliers (FR-11, ADR-0020): Verified UK supplier evidence (Platform admin) and a
seller's own Avasam connection (keys stored encrypted, never returned)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.supplier.base import SupplierError
from momentsales.core.config import Settings
from momentsales.core.errors import ProblemError
from momentsales.core.security import encrypt_credentials
from momentsales.db.ledger import Integration, IntegrationStatus, IntegrationType
from momentsales.db.research import Supplier, SupplierVerification, VerificationCheck
from momentsales.modules.audit import service as audit
from momentsales.modules.providers.registry import ProviderRegistry

MOCK_EVIDENCE_URL = "mock://supplier-verification/{provider}/{check}"


async def list_suppliers(session: AsyncSession) -> list[Supplier]:
    return list(await session.scalars(select(Supplier).order_by(Supplier.provider)))


async def list_verifications(
    session: AsyncSession, provider: str | None = None
) -> list[SupplierVerification]:
    query = select(SupplierVerification).order_by(
        SupplierVerification.provider,
        SupplierVerification.check,
        SupplierVerification.verified_at.desc(),
    )
    if provider:
        query = query.where(SupplierVerification.provider == provider)
    return list(await session.scalars(query))


async def record_verification(
    session: AsyncSession,
    *,
    provider: str,
    check: VerificationCheck,
    passed: bool,
    evidence_url: str,
    evidence_note: str | None,
    verified_at: datetime | None,
    actor_user_id: uuid.UUID | None,
) -> SupplierVerification:
    if await session.scalar(select(Supplier.id).where(Supplier.provider == provider)) is None:
        raise ProblemError(404, "unknown_supplier", f"Unknown supplier {provider!r}")
    row = SupplierVerification(
        provider=provider,
        check=check,
        passed=passed,
        evidence_url=evidence_url,
        evidence_note=evidence_note,
        verified_at=verified_at or datetime.now(UTC),
        verified_by=actor_user_id,
    )
    session.add(row)
    await session.flush()
    await audit.record(
        session,
        action="supplier.verification_recorded",
        object_type="supplier_verification",
        object_id=row.id,
        workspace_id=None,
        actor_user_id=actor_user_id,
        after={
            "provider": provider,
            "check": check.value,
            "passed": passed,
            "evidence_url": evidence_url,
        },
    )
    return row


async def seed_mock_verifications(session: AsyncSession) -> int:
    """Mock mode only (devtools): passing evidence for every check of every supplier,
    labelled with `mock://` URLs. Adds rows only where a provider has none for a check."""
    added = 0
    for supplier in await list_suppliers(session):
        for check in VerificationCheck:
            exists = await session.scalar(
                select(SupplierVerification.id).where(
                    SupplierVerification.provider == supplier.provider,
                    SupplierVerification.check == check,
                )
            )
            if exists is None:
                session.add(
                    SupplierVerification(
                        provider=supplier.provider,
                        check=check,
                        passed=True,
                        evidence_url=MOCK_EVIDENCE_URL.format(
                            provider=supplier.provider, check=check.value
                        ),
                        evidence_note="Mock evidence for PROVIDER_MODE=mock",
                        verified_at=datetime.now(UTC),
                    )
                )
                added += 1
    await session.flush()
    return added


# --- Avasam connection (per seller) ----------------------------------------------------


async def get_avasam(session: AsyncSession) -> Integration | None:
    return await session.scalar(
        select(Integration).where(Integration.type == IntegrationType.AVASAM)
    )


async def connect_avasam(
    session: AsyncSession,
    registry: ProviderRegistry,
    settings: Settings,
    *,
    workspace_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    consumer_key: str,
    secret_key: str,
) -> Integration:
    """Check the keys with a token call (skipped in mock mode), then store them encrypted."""
    cfg = await registry.resolve(session, "supplier")
    if cfg.mode == "live":
        try:
            await registry.avasam(consumer_key, secret_key).verify()
        except SupplierError as exc:
            raise ProblemError(
                503 if exc.retryable else 422,
                "avasam_unavailable" if exc.retryable else "avasam_keys_rejected",
                "Avasam is not responding" if exc.retryable else "Avasam rejected these keys",
            ) from exc
    token = encrypt_credentials({"consumer_key": consumer_key, "secret_key": secret_key}, settings)
    row = await get_avasam(session)
    if row is None:
        row = Integration(
            workspace_id=workspace_id,
            type=IntegrationType.AVASAM,
            external_id=f"avasam:{workspace_id}",
            status=IntegrationStatus.ACTIVE,
            requirements={},
            connected_by=actor_user_id,
        )
        session.add(row)
    row.status = IntegrationStatus.ACTIVE
    row.credentials_encrypted = token
    row.connected_by = actor_user_id
    row.livemode = cfg.mode == "live"
    row.last_sync_at = datetime.now(UTC)
    await session.flush()
    await audit.record(
        session,
        action="avasam.connected",
        object_type="integration",
        object_id=row.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        after={"mode": cfg.mode},  # never the keys
    )
    return row


async def disconnect_avasam(
    session: AsyncSession, *, workspace_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    row = await get_avasam(session)
    if row is None:
        return
    row.status = IntegrationStatus.DISCONNECTED
    row.credentials_encrypted = None
    await session.flush()
    await audit.record(
        session,
        action="avasam.disconnected",
        object_type="integration",
        object_id=row.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
    )
