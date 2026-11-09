# Anthropic Claude (research LLM)

The `llm` provider (FR-1, ADR-0021). Adapter: `adapters/llm/claude.py`. It has one live adapter plus a mock (`PROVIDER_MODE=mock`), because the LLM is not a critical FR-15 category: when it's unavailable, the agents fall back to rules and templates and mark the step degraded.

Checked 2026-11-09 against the Claude API reference used by this project (Messages API, structured outputs, adaptive thinking and effort, prompt caching, current model table).

## Model and settings
- **`LLM_MODEL`:** default `claude-opus-5-5` (owner decision). The `claude-sonnet-5-5` and `claude-haiku-5-5` IDs work too.
- **Credentials:** `ANTHROPIC_API_KEY`, which the OCR adapter already uses.
- **Price at the time of writing (Claude API, first party):** Opus 5.5 is $4 per MTok of input and $20 per MTok of output. Prompt cache reads are cheaper.
- **Price settings (GBP micros per MTok):**
  - `LLM_PRICE_INPUT_MICROS_PER_MTOK`, default 3,000,000 (£3)
  - `LLM_PRICE_OUTPUT_MICROS_PER_MTOK`, default 15,000,000 (£15)
  - Both are converted at about $1 = £0.75. Check them against invoices and update them when prices change.

## How we call it
- **Endpoint:** `client.messages.create(...)` on `anthropic.AsyncAnthropic`, sending:
  - `output_config={"format": {"type": "json_schema", "schema": ...}, "effort": ...}`
  - adaptive thinking, which is always on for Opus 5.5
  - a frozen system prompt from `prompts/`, with a cache breakpoint
  - the facts as a JSON user message
- **Calls per run:** two at most.
  - **Compliance classifier:** effort `medium`, `max_tokens` 8,000.
  - **"Why" lines:** effort `low`, `max_tokens` 4,000.
- **Stop reasons:**
  - `refusal` → `LlmError(retryable=False)`
  - `max_tokens` → `LlmError(retryable=True)`
  - Anything other than `end_turn` is treated as an error.
- **Errors:**
  - `RateLimitError`, 5xx and connection errors are retryable.
  - Other 4xx errors aren't.
  - The SDK already retries twice by itself.
- **Cost:**
  - Before the call, `ensure(estimate)`: input of about 4 bytes per token from the payload size, plus `max_tokens` × the output price.
  - After the call, `charge(actual)` from `usage.input_tokens`, `cache_read_input_tokens` and `output_tokens`. Cache reads are charged at 10% of the input price.
  - Tool calls record the model, the tokens and the cost.

## Data sent
- **Only:** product names, categories, and the stored facts of each recommendation (growth, crowded flag, competition score, retail moment, delivery days, profit estimate).
- **Never:** seller identity, orders, customers, workspace names or credentials.
- **Storage:** prompts and responses are kept only in the run's own agent step, never in the shared cache.

## Guardrails (FR-1)
- **No invented numbers in "why" lines:** the LLM never supplies prices, sizes or dates. Every "why" line goes through a guard that rejects any number or £ amount that isn't in the facts sent, and falls back to a template sentence.
- **Compliance answers come only from the product name and category,** with "unsure" allowed. Their results are combined with a deterministic UK rules list (`modules/research/compliance_rules.py`), so the LLM can add flags but never remove a rule's flag.

## Terms
Anthropic's commercial terms cover API use in a SaaS product. Outputs are labelled as AI-written in the report's method note.

## Mock
`MockLlmAdapter` returns deterministic, labelled answers. Compliance uses keyword heuristics, and the "why" lines are template sentences built from the facts. Cost is £0.
