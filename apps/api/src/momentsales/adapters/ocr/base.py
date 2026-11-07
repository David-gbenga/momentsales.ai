"""OCR port. Providers return text as printed; this module parses it into our types.

Nothing here is saved to the ledger: results only pre-fill a form the user confirms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from momentsales.modules.ledger.money import CURRENCY_EXPONENTS

_SYMBOLS = {"£": "GBP", "€": "EUR", "$": "USD"}


class OcrError(Exception):
    """`retryable` means another provider may succeed (rate limit, 5xx, timeout)."""

    def __init__(self, provider: str, message: str, *, retryable: bool) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class OcrResult:
    provider: str
    vendor: str | None
    date: date | None
    total_minor: int | None
    tax_minor: int | None
    currency: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "vendor": self.vendor,
            "date": self.date.isoformat() if self.date else None,
            "total_minor": self.total_minor,
            "tax_minor": self.tax_minor,
            "currency": self.currency,
            "raw": self.raw,
        }


class OcrPort(Protocol):
    name: str

    async def extract(self, content: bytes, mime: str) -> OcrResult: ...


def parse_currency(text: str | None, amount_text: str | None = None) -> str | None:
    for candidate in (text, amount_text):
        if not candidate:
            continue
        code = candidate.strip().upper()
        if code in CURRENCY_EXPONENTS:
            return code
        for symbol, iso in _SYMBOLS.items():
            if symbol in candidate:
                return iso
    return None


def parse_amount(text: str | None, currency: str | None) -> int | None:
    """'£24.99' / '24,99' / '1,234.50' -> minor units, or None if it can't be read safely."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.,]", "", text)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(".") > cleaned.rfind(","):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        head, _, tail = cleaned.rpartition(",")
        cleaned = f"{head.replace(',', '')}.{tail}" if len(tail) == 2 else cleaned.replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    exp = CURRENCY_EXPONENTS.get(currency or "GBP", 2)
    scaled = value.scaleb(exp)
    if value < 0 or scaled != scaled.to_integral_value():
        return None
    return int(scaled)


_DATE_PATTERNS = (
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})"), ("y", "m", "d")),
    (re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$"), ("d", "m", "y")),  # UK day-first
    (re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2})$"), ("d", "m", "yy")),
)


def parse_date(text: str | None) -> date | None:
    """ISO or UK day-first dates only; anything odd is left for the user to type."""
    if not text:
        return None
    value = text.strip()
    for pattern, order in _DATE_PATTERNS:
        match = pattern.match(value)
        if not match:
            continue
        parts = dict(zip(order, match.groups(), strict=True))
        year = int(parts["y"]) if "y" in parts else 2000 + int(parts["yy"])
        try:
            return date(year, int(parts["m"]), int(parts["d"]))
        except ValueError:
            return None
    return None


def clean_vendor(text: str | None) -> str | None:
    if not text:
        return None
    vendor = " ".join(text.split())[:255]
    return vendor or None
