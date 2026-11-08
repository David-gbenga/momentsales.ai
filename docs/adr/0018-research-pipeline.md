# ADR-0018: Research pipeline

- Status: Accepted (Phase 2a)
- Date: 2026-11-08

## Context
Phase 2 adds seasonal research (FR-1 to FR-4, FR-12):
- An orchestrated set of agents, each with typed inputs and outputs, a tool allow-list, a timeout and a cost budget.
- Live progress.
- Resumable, idempotent runs.
- A per-run cost cap (£1.00, product-owner decision 2026-11-08). This overrides FR-1's old $2.00 default.

The PRD suggests the Claude Agent SDK or LangGraph for the orchestrator. Both are new dependencies, and the pipeline is a fixed sequence of steps.

## Decision
- **Orchestrator:** a plain Python pipeline (`agents/orchestrator.py`) in the API package, run by the worker through the existing job runner (`research.run`, ADR-0005). No new dependency.
  - Agents run in order, one database transaction per step.
  - Each step is an `agent_steps` row, unique per (run, agent), so a retried job resumes after the last finished step.
  - Each agent call is wrapped in `asyncio.wait_for` with the agent's own timeout.
- **Agent contract (`agents/base.py`):**
  - `name`, `tools` (adapter categories it may call), `timeout_seconds`, and `run(ctx) -> AgentResult`.
  - The context gives the agent its run, the UK date, the provider registry, the cost meter, earlier agents' outputs, and `snapshot()`.
  - `snapshot()` stores a raw provider response with its URL and fetch time (`source_snapshots`). Every price, rating, delivery estimate or date shown to a user must reference one (AC-3).
  - A result can be `degraded`: the step finished without some data, which is shown as "not available", and the run continues.
- **Run states:** `queued → running → completed | partial | failed`.
  - `partial`: the cost meter would pass the cap (`BudgetExceededError`), so the run stops and keeps what earlier steps found.
  - `failed`: an agent raised. If nothing was spent on providers, the research run is given back (`entitlements.refund_run`, ADR-0015) and `run_refunded` is set.
- **Cost meter:** agents call `ensure(estimate)` before a paid call and `charge(provider, amount)` after it. Amounts are integer minor units of the cap currency (GBP). Cache hits cost nothing (ADR-0019, Phase 2b). The run stores the total; steps store their share.
- **Metering:** `POST /research-runs` spends one run through `entitlements.consume_run` before the run row is written. A 402 `plan_limit_reached` rolls everything back (AC-15). An optional `Idempotency-Key` returns the same run without spending again.
- **Rules at creation:**
  - Only enabled countries can be chosen (UK at launch). The engine knows AU so hemisphere logic is tested (AC-2), but its flag stays off.
  - `mode=audience` and `audience_source_ids` are refused while `module.audience_research` (L1) is off.
  - A cost cap above the configured maximum is refused.
  - `as_of` (run as if on a date) works only with dev tools, for tests and demos.
- **Live progress:** `GET /research-runs/{id}/events` is Server-Sent Events.
  - The worker publishes a small notice on Redis channel `research-run:{id}`. The stream re-reads the run from the database on each notice and every 5 seconds, and sends the whole run when it changes, then `end`.
  - The browser uses `fetch` streaming, because `EventSource` can't send the bearer token, and falls back to polling `GET /research-runs/{id}`.
- **Sweeper:** Celery Beat's minute sweep re-queues runs still `queued` after 1 minute, or `running` with no update for `RESEARCH_STUCK_AFTER_MINUTES` (default 10).
- **Season engine (FR-2):**
  - Pure functions (`modules/research/season.py`): meteorological seasons by hemisphere, a look-ahead window of 2–12 weeks (default 8), and a user override.
  - Retail moments come from the global `season_moments` table, with yearly date rules: `fixed:MM-DD`, `easter:±N`, `nth_weekday:MONTH:WEEKDAY:N[:+DAYS]`.
  - Seeded with UK moments, plus AU moments for tests. Amazon Prime Day is not seeded: Amazon announces its dates each year, so there is no rule that wouldn't be a guess.
- **Public holidays:** Nager.Date v4 behind a `holidays` adapter with a mock (docs/integrations/nager-date.md). Not a critical FR-15 category, so one live adapter. An outage degrades the Season step.
- **Weather:** not used in the MVP. Meteorological seasons and retail moments carry the seasonal signal. A weather adapter can be added as another Season-agent tool later.
- **Act-by (FR-12):** moment date − (supplier max delivery days + 21 selling days). Until a supplier is matched (Phase 2c), the delivery time is `RESEARCH_ASSUMED_DELIVERY_DAYS` (default 5). The API returns `delivery_source: "assumed"` and the UI says it is an assumption.
- **Audience profile:** `PUT /audience-profile` stores preferences only (age band, gender focus, up to 10 interests, budget band, style). Runs copy it into `params.audience` when `use_audience_profile` is true.

## Consequences
- Phases 2b–2d add agents to `PIPELINE`: trend, marketplace, supplier, scoring, compliance and report. The run, metering, progress and resume behaviour stays the same.
- `research_runs` and `agent_steps` can't be deleted by the app role. `source_snapshots` are kept 90 days (a retention job comes with Phase 5 hardening).
- The dev route `POST /dev/billing/simulate-run` stays for billing tests; real runs go through `POST /research-runs`.
