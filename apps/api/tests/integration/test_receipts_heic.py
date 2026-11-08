"""HEIC receipts: converted to JPEG for OCR when libheif's decoder is installed (ADR-0014).

The real-conversion tests skip where `heif-dec`/`heif-convert` is missing (e.g. Windows); CI
and the Docker images install it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from helpers import create_workspace, sign_in

from momentsales.adapters.ocr import heic

FIXTURE = (Path(__file__).parents[1] / "fixtures" / "receipt.heic").read_bytes()
needs_decoder = pytest.mark.skipif(heic.converter() is None, reason="libheif decoder not installed")


async def _upload(client: httpx.AsyncClient, content: bytes) -> tuple[dict[str, object], object]:
    owner = await sign_in(client)
    ws = await create_workspace(owner, "Heic Shop")
    r = await owner.client.post(
        "/api/v1/receipts",
        files={"file": ("IMG_0001.HEIC", content, "image/heic")},
        headers=owner.headers(ws),
    )
    assert r.status_code == 201, r.text
    detail = await owner.client.get(f"/api/v1/receipts/{r.json()['id']}", headers=owner.headers(ws))
    return detail.json(), owner


@needs_decoder
async def test_heic_decodes_to_jpeg() -> None:
    jpeg = await heic.heic_to_jpeg(FIXTURE)
    assert jpeg.startswith(b"\xff\xd8\xff")


@needs_decoder
async def test_corrupt_heic_raises() -> None:
    with pytest.raises(heic.HeicConversionError):
        await heic.heic_to_jpeg(FIXTURE[:40])


async def test_no_decoder_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heic.shutil, "which", lambda _name: None)
    assert heic.converter() is None
    with pytest.raises(heic.HeicConversionError):
        await heic.heic_to_jpeg(FIXTURE)


@needs_decoder
async def test_heic_receipt_is_read_and_original_kept(client: httpx.AsyncClient) -> None:
    detail, owner = await _upload(client, FIXTURE)
    assert detail["mime"] == "image/heic"
    assert detail["ocr_status"] == "done"
    assert detail["sha256"] == hashlib.sha256(FIXTURE).hexdigest()
    # The stored original is the HEIC as uploaded, not the JPEG copy OCR read
    dl = await owner.client.get(detail["download_url"])  # type: ignore[attr-defined]
    assert dl.status_code == 200 and dl.content == FIXTURE


@needs_decoder
async def test_unreadable_heic_fails_softly(client: httpx.AsyncClient) -> None:
    detail, _ = await _upload(client, FIXTURE[:40])  # valid HEIC header, broken body
    assert detail["ocr_status"] == "failed"
    assert "HEIC" in str(detail["ocr_error"])


async def test_heic_skips_ocr_without_decoder(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(heic.shutil, "which", lambda _name: None)
    detail, _ = await _upload(client, FIXTURE)
    assert detail["ocr_status"] == "skipped"
