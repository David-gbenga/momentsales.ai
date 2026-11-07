"""Mock OCR for PROVIDER_MODE=mock. It never makes values up: unless the file carries a test
fixture marker (`MOCK-OCR:{json}` appended to the bytes), every field is empty and the user
types them in."""

from __future__ import annotations

import json
import re

from momentsales.adapters.ocr.base import (
    OcrError,
    OcrResult,
    clean_vendor,
    parse_amount,
    parse_currency,
    parse_date,
)

_MARKER = re.compile(rb"MOCK-OCR:(\{.*?\})")
_FAIL = b"MOCK-OCR-FAIL"


class MockOcrAdapter:
    def __init__(self, name: str = "mock") -> None:
        self.name = name

    async def extract(self, content: bytes, mime: str) -> OcrResult:
        if _FAIL + b":" + self.name.encode() in content:
            raise OcrError(self.name, "simulated outage", retryable=True)
        match = _MARKER.search(content)
        if not match:
            return OcrResult(self.name, None, None, None, None, None, {"fixture": False})
        data = json.loads(match.group(1))
        currency = parse_currency(data.get("currency"), data.get("total"))
        return OcrResult(
            provider=self.name,
            vendor=clean_vendor(data.get("vendor")),
            date=parse_date(data.get("date")),
            total_minor=parse_amount(data.get("total"), currency),
            tax_minor=parse_amount(data.get("tax"), currency),
            currency=currency,
            raw={"fixture": True, **data},
        )
