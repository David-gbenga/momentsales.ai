"""Contract tests for the Claude LLM adapter (ADR-0021, docs/integrations/anthropic-llm.md)."""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from momentsales.adapters.llm.base import LlmError, LlmPrices
from momentsales.adapters.llm.claude import ClaudeLlmAdapter
from momentsales.adapters.llm.mock import MockLlmAdapter

PRICES = LlmPrices(input_micros_per_mtok=3_000_000, output_micros_per_mtok=15_000_000)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def reply(
    data: object = None,
    stop: str = "end_turn",
    status: int = 200,
    usage: dict[str, int] | None = None,
) -> httpx2.Response:
    if status != 200:
        return httpx2.Response(
            status, json={"type": "error", "error": {"type": "x", "message": "x"}}
        )
    return httpx2.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [
                {"type": "text", "text": json.dumps(data if data is not None else {"ok": True})}
            ],
            "stop_reason": stop,
            "usage": usage
            or {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 500},
        },
    )


def adapter_with(response: httpx2.Response) -> tuple[ClaudeLlmAdapter, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "api.anthropic.com" and request.url.path == "/v1/messages"
        seen.append(request)
        return response

    client = anthropic.AsyncAnthropic(
        api_key="sk-ant-test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return ClaudeLlmAdapter(client, "claude-opus-5-5", PRICES), seen


async def call(adapter: ClaudeLlmAdapter) -> Any:
    return await adapter.complete_json(
        task="compliance",
        system="Frozen system prompt.",
        payload={"products": [{"id": "1", "name": "heated throw"}]},
        schema=SCHEMA,
        effort="medium",
        max_tokens=8000,
    )


async def test_request_shape_usage_and_cost() -> None:
    adapter, seen = adapter_with(reply({"ok": True}))
    result = await call(adapter)
    assert result.data == {"ok": True} and result.model == "claude-opus-5-5"
    body = json.loads(seen[-1].content)
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 8000
    assert body["output_config"] == {
        "format": {"type": "json_schema", "schema": SCHEMA},
        "effort": "medium",
    }
    assert body["system"] == [
        {"type": "text", "text": "Frozen system prompt.", "cache_control": {"type": "ephemeral"}}
    ]
    assert json.loads(body["messages"][0]["content"]) == {
        "products": [{"id": "1", "name": "heated throw"}]
    }
    assert "thinking" not in body and "temperature" not in body
    # 1000 in x £3 + 500 cache reads x £0.30 + 200 out x £15, per million tokens.
    assert (result.input_tokens, result.output_tokens, result.cache_read_tokens) == (1000, 200, 500)
    assert result.cost_micros == 3000 + 150 + 3000


def test_prices_round_up_and_estimate() -> None:
    assert PRICES.cost(1, 0) == 3
    assert PRICES.cost(0, 1) == 15
    assert PRICES.cost(0, 0) == 0
    assert PRICES.estimate(4000, 1000) == PRICES.cost(1000 + 1500, 1000)


@pytest.mark.parametrize(
    ("response", "retryable"),
    [
        (reply(stop="refusal"), False),
        (reply(stop="max_tokens"), True),
        (reply(status=429), True),
        (reply(status=529), True),
        (reply(status=400), False),
        (
            httpx2.Response(
                200,
                json={
                    "id": "m",
                    "type": "message",
                    "role": "assistant",
                    "model": "m",
                    "content": [{"type": "text", "text": "not json"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            ),
            True,
        ),
    ],
)
async def test_errors(response: httpx2.Response, retryable: bool) -> None:
    adapter, _ = adapter_with(response)
    with pytest.raises(LlmError) as err:
        await call(adapter)
    assert err.value.retryable is retryable


async def test_mock_tasks() -> None:
    mock = MockLlmAdapter()
    comp = await mock.complete_json(
        task="compliance",
        system="",
        payload={
            "products": [{"id": "a", "name": "Kitchen knife set"}, {"id": "b", "name": "mug"}]
        },
        schema={},
        effort="low",
        max_tokens=10,
    )
    assert [p["restricted"] for p in comp.data["products"]] == [True, False]
    reasons = await mock.complete_json(
        task="reasons",
        system="",
        payload={"items": [{"id": "a", "facts": {"growth_pct": 66, "delivery_max_days": 4}}]},
        schema={},
        effort="low",
        max_tokens=10,
    )
    assert "66%" in reasons.data["items"][0]["why"] and comp.cost_micros == 0
    with pytest.raises(LlmError):
        await mock.complete_json(
            task="?", system="", payload={}, schema={}, effort="low", max_tokens=1
        )
