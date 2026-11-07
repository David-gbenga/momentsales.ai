"""Storage port: private object storage with short-lived signed download URLs (ADR-0012)."""

from __future__ import annotations

import uuid
from typing import Protocol


class StorageError(Exception):
    pass


class StoragePort(Protocol):
    name: str

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        """Store `content` at `key`. Never overwrites: an existing key raises StorageError."""
        ...

    async def get(self, key: str) -> bytes: ...

    async def signed_url(
        self, key: str, *, workspace_id: uuid.UUID, filename: str, ttl_seconds: int
    ) -> str: ...


def object_key(workspace_id: uuid.UUID, kind: str, object_id: uuid.UUID, suffix: str = "") -> str:
    """`{workspace_id}/{kind}/{object_id}{suffix}`; the workspace prefix is checked on read."""
    if not kind.isalnum():
        raise ValueError("kind must be alphanumeric")
    return f"{workspace_id}/{kind}/{object_id}{suffix}"


def key_belongs_to(key: str, workspace_id: uuid.UUID) -> bool:
    return key.startswith(f"{workspace_id}/") and ".." not in key
