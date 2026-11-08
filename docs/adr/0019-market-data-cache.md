# ADR-0019: Market data, demand signals and the shared cache

- Status: Accepted (Phase 2b)
- Date: 2026-11-08

## Context
FR-12 asks us to recommend rising demand, not demand that has already peaked:
- a growth rate and a peak ("crowded") flag
- a competition score from 0 to 100, where lower is better
- a shared cache of non-personal market data, reused across workspaces

FR-15 asks for two market-data providers, with automatic failover between them. The owner chose DataForSEO (primary) and SerpApi (secondary). Each run has a £1.00 cost cap, and one provider call costs a fraction of a penny. The LLM arrives in Phase 2d.

Owner decisions on 2026-11-08:
- no Amazon in the MVP
- cached data is reused for up to 24 hours
- candidates come from a seed list plus rising searches
- eBay is dropped (see `docs/integrations/ebay-browse.md`)
- live mode waits until both providers confirm their terms in writing

## Decision
- **Pipeline:** `season → trend → marketplace`.
- **Trend agent:**
  - It builds anchors in this order: the run's category, then the audience interests, then seed terms for the category hints of moments in the window. Seed terms come from the global `category_seed_terms` table, picked in turn from each category.
  - It asks for rising related queries for up to `RESEARCH_MAX_ANCHORS` anchors.
  - It collects at most `RESEARCH_MAX_CANDIDATES` candidates and fetches 12 months of weekly interest for them, in batches of 5.
  - Seed terms are only search candidates. No fact shown to a seller comes from them.
- **Marketplace agent:**
  - Fetches one Google results overview for each of the top `RESEARCH_MARKETPLACE_TOP_N` candidates that have enough data.
  - Computes the competition score from the overview.
  - Shopping offers are demand evidence only. We never buy from them (FR-11).
- **Signals (`modules/research/signals.py`, pure functions):**
  - `growth_rate`: the mean of the last 3 weeks against the mean of the 6 weeks before. Growth is compared only within one keyword's own series, because Google Trends normalises each request separately.
  - `peak` ("crowded"): the recent mean is at least 0.9 × the 52-week maximum.
  - `last_year_lift`: last year's interest at the window's end compared with last year's interest at `as_of`.
  - `insufficient`: set when there are too few points or the interest is below `RESEARCH_MIN_INTEREST`.
  - Competition score: a weighted mean of these parts, scaled 0–1:
    - the results count (log scale)
    - ad density (text ads plus shopping ads)
    - distinct merchants
    - price spread (a tight spread means more competition)

    The weights are in `RESEARCH_COMPETITION_WEIGHTS`. A missing part is dropped and the remaining weights are rescaled; the parts used and missing are stored. If every part is missing, the score is `null` and shown as "not available".
- **Port:** `MarketDataPort` (`interest_over_time`, `related_queries`, `serp_overview`) is provider-neutral, so Google's official Trends API can be added later. It returns Pydantic results carrying `provider`, a secret-free `url`, `fetched_at`, the `raw` response and `cost_micros`.
- **Failover:** `FailoverMarketData` tries the primary, then the secondary on retryable errors (network, 429, 5xx and the provider's rate-limit codes) when failover is `auto`. A result carries the provider that actually answered, and tool calls record the failover.
- **Shared cache:**
  - **Table:** `market_data_cache` is global, with no `workspace_id`, no run id and no user data.
  - **Rows:** each holds the data type, country, category, ISO week, a SHA-256 query key over the normalised query and its parameters, provider, URL, the normalised result, the raw response, cost and `fetched_at`.
  - **Lookup:** a hit is the newest row for the same (data type, country, query key) whose `fetched_at` is within `MARKET_CACHE_MAX_AGE_HOURS` (default 24).
  - **Provider-neutral key:** a result from either provider serves both.
  - **Append-only:** rows are never updated (the app role can't update or delete them). A 90-day retention job comes in Phase 5.
- **Snapshots on a cache hit (AC-3):** the cached raw response is copied into the run's own `source_snapshots`, with the original URL and `fetched_at`. The hit costs £0 and is logged as `cache_hit`.
- **Privacy:**
  - Audience interests may choose which public search terms are looked up.
  - The cache holds only the term and the public result, never who asked.
  - A seller's own data never enters the cache.
- **Cost metering:**
  - The run meter now counts in GBP micros (millionths of a pound): `research_runs.cost_micros` and `agent_steps.cost_micros`. `cost_minor` is the total rounded up to the penny, so the API and UI don't change.
  - `research_runs.cost_by_provider` keeps the spend per provider for FR-15's admin cost view, and a resumed run keeps it.
  - Per-call estimates are settings in GBP micros, used for both `ensure()` and `charge()`. The provider's own USD `cost` is kept in tool calls for checking.
- **Stopping at the cap:**
  - An agent catches the cap (`BudgetExceededError`), stops fetching and returns what it has with `budget_exhausted=True`.
  - The orchestrator saves that step, then finishes the run as `partial`.
  - So data already paid for is kept, and the 2a path (an agent that raises) still works.
- **Monthly budget:** a platform-wide Redis counter per month (`MARKET_DATA_MONTHLY_BUDGET_MICROS`, default £40). When the next call would pass it, agents use the cache only, the step is `degraded`, and a warning is logged.
- **API:** `research_runs.demand` (JSONB) holds the merged candidates. The trend agent writes it, so a partial run keeps the trend results, and the marketplace agent adds market data. `RunOut.demand` is typed (`DemandOut`).

## Consequences
- **Live mode:** stays off until DataForSEO and SerpApi confirm in writing that we may show derived signals and share cached results. Draft emails are in the integration notes.
- **Without eBay:** "number of marketplace listings" (FR-12) is approximated by Google's results count and the number of shopping offers. eBay or Amazon can be added under the registered `marketplace` category.
- **Mock mode:** mock adapters charge the same estimates as the provider they stand in for, so a mock run shows a simulated cost. The UI labels mock data.
- **Next phases:** 2c adds supplier matching, and 2d adds scoring, compliance and the Top 10 report, all reading `run.demand`.
