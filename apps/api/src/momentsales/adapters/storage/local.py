"""Local-folder storage for development (a Docker volume). Refused outside local/test."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from momentsales.adapters.storage.base import StorageError
from momentsales.core.config import Settings
from momentsales.core.security import sign


class LocalStorageAdapter:
    name = "local"

    def __init__(self, root: Path, settings: Settings) -> None:
        self.root = root.resolve()
        self._settings = settings

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError("key escapes the storage root")
        return path

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("xb") as fh:  # "x": fail if it exists, never overwrite
                    fh.write(content)
            except FileExistsError as exc:
                raise StorageError(f"object already exists: {key}") from exc

        await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise StorageError(f"object not found: {key}") from exc

    async def signed_url(
        self, key: str, *, workspace_id: uuid.UUID, filename: str, ttl_seconds: int
    ) -> str:
        token = sign(
            {"k": key, "w": str(workspace_id), "f": filename},
            ttl_seconds=ttl_seconds,
            settings=self._settings,
        )
        return f"/api/v1/files/{token}"
