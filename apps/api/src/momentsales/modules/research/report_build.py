"""Building the Top 10 report (FR-4, ADR-0021). Pure functions.

The "why" line may come from the LLM, but only after `why_is_grounded` checks that every
number in it appears in the facts sent; otherwise a template line built from the facts is
used (FR-1: the LLM never invents prices, sizes or dates).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

METHOD_NOTE = (
    "Scores (0-100) combine rising UK search demand (halved when a search is already near "
    "its yearly high), low competition, seasonal fit (zero when the supplier would arrive "
    "too late), forecast profit and supplier delivery speed. Marketplace sales rank is not "
    "scored: no licensed marketplace data source is connected, so its weight is shared "
    "across the other parts. Selling prices are the middle price seen on Google and ad "
    "costs are a configured share of that price: both are estimates. Compliance flags come "
    "from a UK rules list plus an AI check; they are a screening aid, not legal advice. "
    "The one-line reasons are written by AI from the stored facts only."
)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_MONEY = re.compile(r"[£$€]")


def _numbers(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for v in value.values():
            found |= _numbers(v)
    elif isinstance(value, list):
        for v in value:
            found |= _numbers(v)
    elif isinstance(value, bool) or value is None:
        pass
    else:
        found |= set(_NUMBER.findall(str(value)))
    return found


def why_is_grounded(why: str, facts: dict[str, Any], keyword: str) -> bool:
    """False if the line is empty, too long, mentions money, or has a number not in the
    facts (numbers inside the product name are allowed)."""
    text = why.strip()
    if not text or len(text) > 240 or _MONEY.search(text):
        return False
    allowed = _numbers(facts) | set(_NUMBER.findall(keyword))
    return set(_NUMBER.findall(text)) <= allowed


def template_why(facts: dict[str, Any]) -> str:
    parts: list[str] = []
    growth = facts.get("growth_pct")
    if facts.get("crowded"):
        parts.append("searches are already near their yearly high (crowded)")
    elif growth is not None and growth > 0:
        weeks = facts.get("growth_window_weeks") or 3
        parts.append(f"UK searches are up {growth}% in the last {weeks} weeks")
    elif growth is not None:
        parts.append("UK search interest is steady")
    if facts.get("competition_score") is not None:
        parts.append(f"competition scores {facts['competition_score']}/100")
    if facts.get("moment"):
        parts.append(f"it fits {facts['moment']}")
    if facts.get("arrives_too_late"):
        parts.append("but the supplier may deliver too late")
    elif facts.get("delivery_max_days") is not None:
        parts.append(f"a verified UK supplier delivers in up to {facts['delivery_max_days']} days")
    text = "; ".join(parts) or "it matches your research window"
    return text[0].upper() + text[1:] + "."


def facts_for(c: dict[str, Any], moment: str | None, recent_weeks: int = 3) -> dict[str, Any]:
    trend = c.get("trend") or {}
    market = c.get("market") or {}
    supplier = c.get("supplier") or {}
    match = supplier.get("match") or {}
    growth = trend.get("growth_rate")
    return {
        "product": c["keyword"],
        "category": c.get("category"),
        "growth_pct": round(float(growth) * 100) if growth is not None else None,
        "growth_window_weeks": recent_weeks,
        "crowded": bool(trend.get("peak")),
        "competition_score": market.get("competition_score"),
        "competition_scale_max": 100,
        "moment": moment,
        "supplier_verified": supplier.get("status") == "matched",
        "delivery_max_days": match.get("delivery_max_days"),
        "arrives_too_late": supplier.get("arrives_too_late"),
        "needs_safety_documents": (c.get("compliance") or {}).get("status") == "needs_documents",
    }


def moment_for(c: dict[str, Any], moments: list[dict[str, Any]]) -> str | None:
    deadline = (c.get("supplier") or {}).get("deadline")
    key = (c.get("category") or "").lower()
    for m in moments:
        if deadline and m.get("date") == deadline:
            return str(m["name"])
    for m in moments:
        if key and key in [h.lower() for h in m.get("category_hints") or []]:
            return str(m["name"])
    return None


def offers_for(c: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    market = c.get("market") or {}
    offers = [o for o in market.get("offers") or [] if o.get("url")]
    offers = offers[:5]
    priced = [o for o in offers if o.get("price_minor") is not None and o.get("currency") == "GBP"]
    best = min(priced, key=lambda o: o["price_minor"]) if priced else None
    if best is not None:
        best = {**best, "platform": best.get("merchant"), "provider": market.get("provider")}
    return offers, best


def sizes_for(c: dict[str, Any]) -> list[str]:
    match = (c.get("supplier") or {}).get("match") or {}
    names = [str(v["name"]) for v in match.get("variants") or [] if v.get("name")]
    return list(dict.fromkeys(names))[:12]


def flags_for(c: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if (c.get("trend") or {}).get("peak"):
        flags.append("crowded")
    supplier = c.get("supplier") or {}
    if supplier.get("arrives_too_late"):
        flags.append("arrives_too_late")
    status = supplier.get("status")
    if status in ("no_supplier", "no_verified_supplier"):
        flags.append(status)
    compliance = (c.get("compliance") or {}).get("status")
    if compliance == "needs_documents":
        flags.append("needs_documents")
    if (c.get("compliance") or {}).get("import_vat_notice"):
        flags.append("import_vat_notice")
    return flags


def oldest_fetch(c: dict[str, Any]) -> datetime | None:
    stamps: list[datetime] = []
    for part in (c.get("trend"), c.get("market"), ((c.get("supplier") or {}).get("match"))):
        value = (part or {}).get("fetched_at")
        if value:
            stamps.append(datetime.fromisoformat(str(value)))
    return min(stamps) if stamps else None


def pick_top(
    candidates: list[dict[str, Any]], ranking: list[str], size: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The top `size` ranked searches not blocked by compliance, those with market data
    first; plus the blocked ones that were passed over."""
    by_keyword = {c["keyword"]: c for c in candidates}
    ranked = [by_keyword[k] for k in ranking if k in by_keyword]
    blocked = [c for c in ranked if (c.get("compliance") or {}).get("status") == "blocked"]
    eligible = [c for c in ranked if c not in blocked]
    with_market = [c for c in eligible if c.get("market")]
    rest = [c for c in eligible if not c.get("market")]
    return (with_market + rest)[:size], blocked


def summary_for(
    run: Any, demand: dict[str, Any], sources: list[dict[str, Any]], cost_minor: int, today: date
) -> dict[str, Any]:
    season = run.season or {}
    return {
        "params": {
            "country": run.country,
            "window_weeks": run.window_weeks,
            "category": run.params.get("category"),
            "price_band": run.params.get("price_band"),
            "audience": bool(run.params.get("audience")),
        },
        "as_of": demand.get("as_of") or today.isoformat(),
        "season": {
            "current": season.get("current"),
            "next": season.get("next"),
            "window_end": season.get("window_end"),
            "moments": [
                {"name": m.get("name"), "date": m.get("date"), "act_by": m.get("act_by")}
                for m in season.get("moments") or []
            ],
        },
        "method": METHOD_NOTE,
        "sources": sources,
        "cost_minor": cost_minor,
        "cost_currency": run.cost_currency,
        "mock": bool(demand.get("mock")),
        "compliance_source": demand.get("compliance_source"),
        "stopped_at_cap": bool(demand.get("stopped_at_cap")),
    }
