"""UK product compliance rules (FR-14, ADR-0021), versioned with the code.

A keyword rule maps words in the product name, category or supplier title to a flag:
- "blocked": restricted or prohibited goods; the product is vetoed from the report;
- "needs_documents": a regulated category; the product can't be listed until the supplier
  provides the named safety documents;
- "warning": trademark or health-claim risk to check before listing.

These are product requirements, not legal advice: confirm with a UK adviser before launch.
The Claude classifier can add flags; it never removes a rule's flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from momentsales.adapters.market_data.base import normalise_keyword

RULES_VERSION = "2026-11-09"
Kind = Literal["blocked", "needs_documents", "warning"]


@dataclass(frozen=True, slots=True)
class Rule:
    flag: str
    kind: Kind
    words: tuple[str, ...]
    documents: tuple[str, ...] = ()
    note: str = ""


RULES: tuple[Rule, ...] = (
    Rule(
        "restricted_item",
        "blocked",
        (
            "knife",
            "knives",
            "blade",
            "machete",
            "sword",
            "crossbow",
            "pepper spray",
            "taser",
            "stun gun",
            "vape",
            "e-cigarette",
            "cbd",
            "nicotine",
            "firework",
            "fireworks",
            "sparkler",
            "airsoft",
            "bb gun",
            "medicine",
            "prescription",
            "weight loss pill",
            "laser pointer",
        ),
        note="Restricted or prohibited for a UK online seller.",
    ),
    Rule(
        "electrical",
        "needs_documents",
        (
            "electric",
            "electrical",
            "heated",
            "charger",
            "charging",
            "battery",
            "rechargeable",
            "plug",
            "usb",
            "led",
            "lamp",
            "heater",
            "hair dryer",
            "straightener",
            "shaver",
            "clippers",
            "projector",
            "speaker",
            "earbuds",
            "smart",
        ),
        ("UKCA or CE marking", "Declaration of conformity", "Electrical safety test report"),
        "Electrical Equipment (Safety) Regulations 2016.",
    ),
    Rule(
        "toy",
        "needs_documents",
        ("toy", "toys", "slime", "plush", "teddy", "doll", "kids", "children", "baby", "fidget"),
        ("UKCA or CE marking", "Toy safety test report (EN 71)", "UK importer details"),
        "Toys (Safety) Regulations 2011.",
    ),
    Rule(
        "cosmetic",
        "needs_documents",
        (
            "lip",
            "lipstick",
            "skincare",
            "serum",
            "cream",
            "moisturiser",
            "bath bomb",
            "makeup",
            "make-up",
            "glitter makeup",
            "nail",
            "perfume",
            "fragrance",
            "shampoo",
        ),
        ("Cosmetic product safety report", "UK Responsible Person", "SCPN notification"),
        "UK Cosmetics Regulation.",
    ),
    Rule(
        "ppe",
        "needs_documents",
        ("helmet", "face mask", "safety glasses", "hi-vis", "high visibility", "gloves ppe"),
        ("UKCA or CE marking", "Declaration of conformity"),
        "PPE Regulation as it applies in Great Britain.",
    ),
    Rule(
        "trademark_risk",
        "warning",
        (
            "disney",
            "marvel",
            "pokemon",
            "pokémon",
            "lego",
            "nike",
            "adidas",
            "apple",
            "iphone",
            "airpods",
            "harry potter",
            "barbie",
            "star wars",
            "minecraft",
            "stanley",
            "dyson",
            "gucci",
            "chanel",
        ),
        note="Possible trademark or counterfeit risk: sell only genuine, licensed goods.",
    ),
    Rule(
        "health_claim",
        "warning",
        ("cure", "detox", "anti-ageing", "anti aging", "treats", "heals", "immune", "slimming"),
        note="Avoid unsupported health claims in listings (CAP Code).",
    ),
)


def _contains(text: str, word: str) -> bool:
    return f" {word} " in f" {text} "


def check(texts: list[str]) -> list[dict[str, Any]]:
    """Every rule whose words appear in the given texts."""
    joined = " ".join(normalise_keyword(t) for t in texts if t)
    hits: list[dict[str, Any]] = []
    for rule in RULES:
        words = [w for w in rule.words if _contains(joined, normalise_keyword(w))]
        if words:
            hits.append(
                {
                    "flag": rule.flag,
                    "kind": rule.kind,
                    "documents": list(rule.documents),
                    "matched": words[:3],
                    "note": rule.note,
                    "source": "rules",
                }
            )
    return hits


LLM_FLAGS: dict[str, Kind] = {
    "restricted": "blocked",
    "trademark_risk": "warning",
    "health_claim_risk": "warning",
}


def merge(rule_hits: list[dict[str, Any]], llm: dict[str, Any] | None) -> dict[str, Any]:
    """Flags = rules + LLM. The LLM can add flags and documents, never remove a rule's."""
    flags = {h["flag"]: h for h in rule_hits}
    documents = [d for h in rule_hits for d in h["documents"]]
    if llm:
        if llm.get("restricted") and "restricted_item" not in flags:
            flags["restricted_item"] = {
                "flag": "restricted_item",
                "kind": "blocked",
                "documents": [],
                "matched": [],
                "note": str(llm.get("reason") or "")[:300],
                "source": "llm",
            }
        for key, flag in (
            ("trademark_risk", "trademark_risk"),
            ("health_claim_risk", "health_claim"),
        ):
            if llm.get(key) and flag not in flags:
                flags[flag] = {
                    "flag": flag,
                    "kind": "warning",
                    "documents": [],
                    "matched": [],
                    "note": str(llm.get("reason") or "")[:300],
                    "source": "llm",
                }
        for category in llm.get("categories") or []:
            name = str(category).lower()
            if name in {"electrical", "toy", "cosmetic", "ppe", "children"} and name not in flags:
                flags[name] = {
                    "flag": name,
                    "kind": "needs_documents",
                    "documents": [str(d) for d in llm.get("required_documents") or []][:5],
                    "matched": [],
                    "note": str(llm.get("reason") or "")[:300],
                    "source": "llm",
                }
        documents += [str(d) for d in llm.get("required_documents") or []]
    kinds = {f["kind"] for f in flags.values()}
    status = (
        "blocked"
        if "blocked" in kinds
        else "needs_documents"
        if "needs_documents" in kinds
        else "ok"
    )
    return {
        "status": status,
        "flags": sorted(flags.values(), key=lambda f: f["flag"]),
        "required_documents": list(dict.fromkeys(documents))[:8],
        "llm_unsure": bool(llm and llm.get("unsure")),
        "rules_version": RULES_VERSION,
    }
