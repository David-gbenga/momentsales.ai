"""HEIC -> JPEG for OCR, using libheif's command-line decoder (ADR-0014).

The api and worker images install Debian's libheif tools with only the LGPL libde265 HEVC
decoder; the GPL x265 encoder is never installed. Where the tool is missing (for example a
Windows host), HEIC receipts skip OCR and the user types the details.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

# libheif >= 1.17 ships `heif-dec`; older releases (and some distros) call it `heif-convert`.
_TOOLS = ("heif-dec", "heif-convert")
TIMEOUT_SECONDS = 30


class HeicConversionError(Exception):
    pass


def converter() -> str | None:
    """Path of the libheif decoder tool, or None when it isn't installed."""
    for name in _TOOLS:
        path = shutil.which(name)
        if path:
            return path
    return None


def _read_output(tmp: Path) -> bytes:
    # Files with several top-level images are written as out-1.jpg, out-2.jpg, ...
    out = tmp / "out.jpg"
    written = [out] if out.exists() else sorted(tmp.glob("out-*.jpg"))
    if not written:
        raise HeicConversionError("decoder wrote no image")
    return written[0].read_bytes()


async def heic_to_jpeg(content: bytes) -> bytes:
    """Decode the primary (first) image of a HEIC file to JPEG."""
    tool = converter()
    if tool is None:
        raise HeicConversionError("no HEIC decoder installed")
    with tempfile.TemporaryDirectory(prefix="heic-") as tmp_name:
        tmp = Path(tmp_name)
        src, out = tmp / "in.heic", tmp / "out.jpg"
        await asyncio.to_thread(src.write_bytes, content)
        proc = await asyncio.create_subprocess_exec(
            tool,
            "--quiet",
            "-q",
            "90",
            str(src),
            str(out),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), TIMEOUT_SECONDS)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise HeicConversionError("HEIC decode timed out") from exc
        if proc.returncode != 0:
            raise HeicConversionError(err.decode(errors="replace").strip()[:300] or "failed")
        return await asyncio.to_thread(_read_output, tmp)
