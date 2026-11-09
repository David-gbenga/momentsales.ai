"""Claude via the Anthropic SDK (docs/integrations/anthropic-llm.md): structured outputs
(`output_config.format`), adaptive thinking (always on for Opus 5.5), effort per task, and a
frozen system prompt with a cache breakpoint. Facts go in as a JSON user message."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import anthropic

from momentsales.adapters.llm.base import Effort, LlmError, LlmPrices, LlmResult

if TYPE_CHECKING:
    from anthropic.types import MessageParam, OutputConfigParam, TextBlockParam


class ClaudeLlmAdapter:
    name = "claude"

    def __init__(self, client: anthropic.AsyncAnthropic, model: str, prices: LlmPrices) -> None:
        self._client = client
        self.model = model
        self.prices = prices

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
        facts = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        try:
            message = await self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=cast(
                    "list[TextBlockParam]",
                    [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                ),
                messages=cast("list[MessageParam]", [{"role": "user", "content": facts}]),
                output_config=cast(
                    "OutputConfigParam",
                    {"format": {"type": "json_schema", "schema": schema}, "effort": effort},
                ),
            )
        except (
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
        ) as exc:
            raise LlmError(self.name, type(exc).__name__, retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise LlmError(
                self.name, f"HTTP {exc.status_code}", retryable=exc.status_code >= 500
            ) from exc
        if message.stop_reason == "refusal":
            raise LlmError(self.name, "the model declined this request")
        if message.stop_reason != "end_turn":
            raise LlmError(self.name, f"stopped: {message.stop_reason}", retryable=True)
        text = "".join(b.text for b in message.content if b.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LlmError(self.name, "response was not JSON", retryable=True) from exc
        if not isinstance(data, dict):
            raise LlmError(self.name, "response was not a JSON object")
        usage = message.usage
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cost = self.prices.cost(usage.input_tokens, usage.output_tokens, cache_read)
        return LlmResult(
            data=data,
            provider=self.name,
            model=message.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cost_micros=cost,
            fetched_at=datetime.now(UTC),
            raw={"task": task, "request_id": getattr(message, "_request_id", None)},
        )
