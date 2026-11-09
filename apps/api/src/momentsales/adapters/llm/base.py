"""LLM port (FR-1, ADR-0021): one structured-output call returning JSON that matches a
schema, with token usage and cost in GBP micros for the run's cost meter.

The LLM never supplies prices, sizes or dates (FR-1); callers send it stored facts only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

Effort = Literal["low", "medium", "high"]


class LlmError(Exception):
    def __init__(self, provider: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class LlmResult:
    data: dict[str, Any]
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_micros: int
    fetched_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LlmPrices:
    """GBP micros per million tokens."""

    input_micros_per_mtok: int
    output_micros_per_mtok: int

    def cost(self, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> int:
        """Rounded up; cache reads at 10% of the input price."""
        total = (
            input_tokens * self.input_micros_per_mtok
            + cache_read_tokens * self.input_micros_per_mtok // 10
            + output_tokens * self.output_micros_per_mtok
        )
        return -(-total // 1_000_000)

    def estimate(self, payload_chars: int, max_tokens: int) -> int:
        """Upper-bound estimate before a call: about 4 characters per input token."""
        return self.cost(payload_chars // 4 + 1500, max_tokens)


class LlmPort(Protocol):
    name: str
    model: str
    prices: LlmPrices

    async def complete_json(
        self,
        *,
        task: str,
        system: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        effort: Effort,
        max_tokens: int,
    ) -> LlmResult: ...
