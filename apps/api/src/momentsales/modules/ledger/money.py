"""Money helpers: decimal strings <-> integer minor units, and fair allocation.

Amounts are never floats. Only currencies with a known ISO 4217 exponent are accepted.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

BASE_CURRENCY = "GBP"

# ISO 4217 minor-unit exponents for the currencies a UK seller is likely to be charged in.
CURRENCY_EXPONENTS: dict[str, int] = {
    "GBP": 2,
    "EUR": 2,
    "USD": 2,
    "AUD": 2,
    "CAD": 2,
    "NZD": 2,
    "CHF": 2,
    "SEK": 2,
    "NOK": 2,
    "DKK": 2,
    "PLN": 2,
    "CNY": 2,
    "HKD": 2,
    "SGD": 2,
    "JPY": 0,
}

_AMOUNT_RE = re.compile(r"^\d{1,12}(\.\d{1,4})?$")


class MoneyError(ValueError):
    pass


def check_currency(code: str) -> str:
    code = code.strip().upper()
    if code not in CURRENCY_EXPONENTS:
        raise MoneyError(f"unsupported currency {code!r}")
    return code


def to_minor(amount: str | Decimal, currency: str) -> int:
    """'24.99' GBP -> 2499. Rejects negatives, more decimals than the currency has, and junk."""
    exp = CURRENCY_EXPONENTS[check_currency(currency)]
    text = str(amount).strip().replace(",", "")
    if not _AMOUNT_RE.match(text):
        raise MoneyError(f"invalid amount {amount!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyError(f"invalid amount {amount!r}") from exc
    scaled = value.scaleb(exp)
    if scaled != scaled.to_integral_value():
        raise MoneyError(f"{amount!r} has more decimals than {currency} allows")
    return int(scaled)


def from_minor(minor: int, currency: str) -> str:
    exp = CURRENCY_EXPONENTS[check_currency(currency)]
    value = Decimal(minor).scaleb(-exp).quantize(Decimal(1).scaleb(-exp), ROUND_HALF_EVEN)
    return f"{value:.{exp}f}"


def allocate(total: int, weights: list[int]) -> list[int]:
    """Split `total` across `weights` proportionally; parts are integers that sum to `total`
    (largest-remainder method). Used to spread an order's fee/refund across its items."""
    if not weights:
        return []
    weight_sum = sum(weights)
    if weight_sum <= 0:
        parts = [0] * len(weights)
        parts[0] = total
        return parts
    sign = -1 if total < 0 else 1
    magnitude = abs(total)
    exact = [magnitude * w for w in weights]
    parts = [e // weight_sum for e in exact]
    remainders = sorted(
        range(len(weights)), key=lambda i: (exact[i] % weight_sum, -i), reverse=True
    )
    for i in remainders[: magnitude - sum(parts)]:
        parts[i] += 1
    return [sign * p for p in parts]
