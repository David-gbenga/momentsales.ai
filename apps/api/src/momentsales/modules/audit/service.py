"""Hash-chained, append-only audit log (ADR-0008, FR-8).

hash = sha256(prev_hash || canonical_json(entry)). One chain per workspace, plus a
"platform" chain for events that belong to no workspace. Appends to a chain are
serialised by locking its row in audit_chain_heads.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.ids import uuid7
from momentsales.db.models import AuditChainHead, AuditLog

PLATFORM_CHAIN = "platform"


def _chain_key(workspace_id: uuid.UUID | None) -> str:
    return str(workspace_id) if workspace_id else PLATFORM_CHAIN


def canonical_payload(entry: AuditLog) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "occurred_at": entry.occurred_at.astimezone(UTC).isoformat(),
        "workspace_id": str(entry.workspace_id) if entry.workspace_id else None,
        "actor_user_id": str(entry.actor_user_id) if entry.actor_user_id else None,
        "action": entry.action,
        "object_type": entry.object_type,
        "object_id": entry.object_id,
        "before": entry.before,
        "after": entry.after,
        "ip_hash": entry.ip_hash,
    }


def compute_hash(prev_hash: str | None, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(((prev_hash or "") + body).encode("utf-8")).hexdigest()


async def record(
    session: AsyncSession,
    *,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID | None,
    workspace_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip_hash: str | None = None,
) -> AuditLog:
    key = _chain_key(workspace_id)
    await session.execute(pg_insert(AuditChainHead).values(chain_key=key).on_conflict_do_nothing())
    head = await session.scalar(
        select(AuditChainHead).where(AuditChainHead.chain_key == key).with_for_update()
    )
    assert head is not None
    entry = AuditLog(
        id=uuid7(),
        occurred_at=datetime.now(UTC),
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id is not None else None,
        before=before,
        after=after,
        ip_hash=ip_hash,
        prev_hash=head.last_hash,
    )
    entry.hash = compute_hash(head.last_hash, canonical_payload(entry))
    if workspace_id is None:
        # INSERT ... RETURNING must pass the SELECT policy, so platform rows need platform
        # context. Switch it on *before* adding the row so no autoflush races it.
        await session.execute(text("SELECT set_config('app.platform', 'on', true)"))
        try:
            session.add(entry)
            await session.flush()
        finally:
            await session.execute(text("SELECT set_config('app.platform', 'off', true)"))
    else:
        session.add(entry)
        await session.flush()
    head.last_hash = entry.hash
    head.last_seq = entry.seq
    await session.flush()
    return entry


@dataclass(frozen=True, slots=True)
class ChainVerification:
    ok: bool
    entries: int
    first_bad_seq: int | None = None


def verify_entries(entries: Sequence[AuditLog]) -> ChainVerification:
    prev: str | None = None
    for e in entries:
        if e.prev_hash != prev or compute_hash(prev, canonical_payload(e)) != e.hash:
            return ChainVerification(ok=False, entries=len(entries), first_bad_seq=e.seq)
        prev = e.hash
    return ChainVerification(ok=True, entries=len(entries))


async def verify_chain(session: AsyncSession, workspace_id: uuid.UUID | None) -> ChainVerification:
    """Verify a whole chain. RLS means only the current workspace's chain is readable."""
    cond = (
        AuditLog.workspace_id == workspace_id if workspace_id else AuditLog.workspace_id.is_(None)
    )
    rows = (await session.scalars(select(AuditLog).where(cond).order_by(AuditLog.seq))).all()
    return verify_entries(rows)
