"""Mock LLM for PROVIDER_MODE=mock: deterministic, labelled answers and £0 cost.

Tasks:
- "compliance": flags restricted items by a few keywords (the UK rules list does the rest);
- "reasons": a plain sentence per item, built only from the facts it was given.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from momentsales.adapters.llm.base import Effort, LlmError, LlmPrices, LlmResult

RESTRICTED_WORDS = ("knife", "vape", "firework", "pepper spray")


def _why(facts: dict[str, Any]) -> str:
    parts: list[str] = []
    if facts.get("crowded"):
        parts.append("interest is already near its yearly high")
    elif facts.get("growth_pct") is not None and facts["growth_pct"] > 0:
        weeks = facts.get("growth_window_weeks") or 3
        parts.append(f"UK searches are up {facts['growth_pct']}% in the last {weeks} weeks")
    if facts.get("moment"):
        parts.append(f"it fits {facts['moment']}")
    if facts.get("arrives_too_late"):
        parts.append("but the supplier may deliver too late")
    elif facts.get("delivery_max_days") is not None:
        parts.append(f"a UK supplier delivers in up to {facts['delivery_max_days']} days")
    text = ", ".join(parts) or "it matches your research window"
    return text[0].upper() + text[1:] + " (mock)."


class MockLlmAdapter:
    name = "mock"

    def __init__(self, model: str = "mock") -> None:
        self.model = model
        self.prices = LlmPrices(0, 0)

    async def complete_json(
        self,
        *,
        task: str,
        system: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        effort: Effort,
        max_tokens: int,
    ) -> LlmResult:
        if task == "compliance":
            data: dict[str, Any] = {
                "products": [
                    {
                        "id": p["id"],
                        "categories": [],
                        "required_documents": [],
                        "restricted": any(w in p["name"].lower() for w in RESTRICTED_WORDS),
                        "trademark_risk": False,
                        "health_claim_risk": False,
                        "unsure": False,
                        "reason": "Mock classifier: keyword check only.",
                    }
                    for p in payload.get("products", [])
                ]
            }
        elif task == "reasons":
            data = {"items": [{"id": i["id"], "why": _why(i["facts"])} for i in payload["items"]]}
        else:
            raise LlmError(self.name, f"unknown mock task {task!r}")
        return LlmResult(
            data=data,
            provider=self.name,
            model=self.model,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cost_micros=0,
            fetched_at=datetime.now(UTC),
            raw={"task": task, "mock": True},
        )
