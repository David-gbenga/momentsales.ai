"""One metered LLM call for an agent (ADR-0021): the run's cost cap is checked with an
estimate first, the actual usage is charged after, and the call is recorded."""

from __future__ import annotations

import json
from typing import Any

from momentsales.adapters.llm.base import Effort, LlmPort, LlmResult
from momentsales.agents.base import AgentContext


async def call_llm(
    ctx: AgentContext,
    llm: LlmPort,
    *,
    task: str,
    system: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    effort: Effort,
    max_tokens: int,
) -> LlmResult:
    """Raises BudgetExceededError before calling when the estimate passes the cap, and
    LlmError when the provider fails."""
    size = len(json.dumps(payload)) + len(system)
    ctx.meter.ensure(llm.prices.estimate(size, max_tokens))
    result = await llm.complete_json(
        task=task,
        system=system,
        payload=payload,
        schema=schema,
        effort=effort,
        max_tokens=max_tokens,
    )
    ctx.meter.charge(result.provider, result.cost_micros)
    ctx.record_call(
        "llm",
        task=task,
        provider=result.provider,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cost_micros=result.cost_micros,
    )
    return result
