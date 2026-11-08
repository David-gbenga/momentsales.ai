# DataForSEO (market data, primary)

The primary `market_data` provider (FR-15, ADR-0019). The Trend agent uses it for Google Trends interest and rising related queries. The Marketplace agent uses it for the Google results overview: ad counts, shopping offers and the results count. Adapter: `adapters/market_data/dataforseo.py`; mock: `adapters/market_data/mock.py`.

Checked 2026-11-08 against docs.dataforseo.com (Google Trends Explore live, SERP Google organic live advanced, auth and errors appendix), the dataforseo.com pricing pages, the Google Trends limits help page, and the terms of service (updated 12 June 2026).

## Auth
- HTTP Basic: `Authorization: Basic base64(login:password)`. The login and API password are on the dashboard's API Access tab. The API password is not the account password.
- Credentials can't go in the URL, so stored snapshot URLs never contain secrets.
- Settings: `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`, `DATAFORSEO_BASE_URL` (default `https://api.dataforseo.com/v3`).

## Endpoints used
Every request body is a JSON array holding one task object.

### Google Trends Explore, live
`POST /v3/keywords_data/google_trends/explore/live`

Request fields we send:
- `keywords`: up to 5, each up to 100 characters. Commas are removed. Symbols such as `- + = ~ ! : * ( ) [ ] < > | "` are not allowed, so the adapter strips them.
- `location_name`: `"United Kingdom"`.
- `language_code`: `"en"`.
- `type`: `"web"`.
- `time_range`: `past_12_months` for interest over time, `past_90_days` for related queries.
- `item_types`: one per request, as the docs recommend: `["google_trends_graph"]` or `["google_trends_queries_list"]`. A queries list needs exactly 1 keyword.

Response fields we read:
- `status_code` (top level and per task), `tasks[0].result[0].items[]`.
- `google_trends_graph`:
  - `data[]` holds `date_from`, `date_to`, `timestamp`, `missing_data` and `values[]` (one value per keyword, 0–100, `null` when there's too little data).
  - Values are relative within one request, so we only compare a keyword with itself.
- `google_trends_queries_list`: `data.rising[]` with `query` and `value`, the rise as a percentage without the % sign.

### SERP, Google organic, live advanced
`POST /v3/serp/google/organic/live/advanced`

Request fields we send:
- `keyword`
- `location_name: "United Kingdom"`
- `language_code: "en"`
- `device: "desktop"`
- `depth: 10`

Search operators cost 5×, so the adapter never sends them.

Response fields we read, from `result[0]`:
- `se_results_count`: the number of Google results.
- `items[]` by `type`:
  - `paid`: counted as text ads.
  - `shopping`: its `items[]` are shopping ads. Each has `title`, `source`, `url`, and `price` with `current` and `currency`.
  - `popular_products`: its `items[]` have `title`, `seller`, `url` and `price`.

The docs gave no verbatim sample for `shopping` or `popular_products`. The contract fixture follows the documented field list, and the parser treats every field as optional. **Check against a live sample before going live.**

## Errors and limits
- Most errors come back as HTTP 200 with a body `status_code`:
  - `20000` means OK.
  - Retryable (the request can fail over): `40202` (per-minute rate limit), `40209` (too many simultaneous requests), and `50000`, `50301`, `50303`, `50401`, `50402`.
  - Not retryable: `40100` (unauthorised), `40200`/`40210` (payment or funds), `40203` (daily cost limit), and `405xx` (invalid fields).
- HTTP errors: 401 and 402 are not retryable. 429 and 5xx are retryable.
- Limits:
  - Live endpoints allow up to 10,000 calls a minute and 30 at once.
  - Google Trends live is limited to about 250 tasks in a 60-second window.
  - Google Trends also has a shared limit of 500,000 requests a day across all DataForSEO users.
  - A run makes at most about 20 calls.

## Prices (USD, checked 2026-11-08)
- Google Trends Explore: **live $0.011 per task** (up to 5 keywords), standard $0.0027 (queued, up to about 45 minutes). We use live so a run finishes in minutes.
- SERP Google organic live: **$0.002 per results page** at depth 10. Standard is $0.0006 and priority $0.0012.
- Pay as you go: a $50 minimum top-up, and the balance doesn't expire. New accounts get $1 of trial credit.
- The response's own `cost` (USD) is recorded in each step's tool calls.
- The run meter charges the GBP estimates in settings: `COST_DATAFORSEO_TRENDS_MICROS` (default 9,000, about £0.009) and `COST_DATAFORSEO_SERP_MICROS` (default 2,000). These are internal estimates for the cost cap only, never shown as prices. Update them from real invoices.
- **Monthly budget:** about £40 (`MARKET_DATA_MONTHLY_BUDGET_MICROS`).

## Terms (to confirm before going live)
- **What the terms say:**
  - Section 7.1 says data from search engines "shall not be used to compete with or adversely affect" the search engine providers. Showing derived signals (growth rate, competition) to sellers doesn't compete with Google.
  - Nothing found on caching, storing, sharing results with our customers, or attribution.
  - The API itself expires stored results after 30 days (`40403`).
- **Not read yet:** the DPA and privacy policy.
- **Before switching `PROVIDER_MODE=live`, the owner must get written confirmation.** Draft email:

  > Subject: Use of Google Trends and SERP API results in a SaaS product
  >
  > Hello, we're building momentsales.ai, a UK SaaS that helps small online sellers find seasonal products. We'd like to use the Google Trends Explore and SERP (Google organic) live endpoints, and to (1) show sellers signals we compute from the results (growth rate, ad counts, price range of shopping offers, with links), and (2) cache a result for up to 24 hours and reuse it for other customers who search the same term. No personal data is involved. Could you confirm this is allowed under your terms, and whether any attribution is required? Thank you.

## Mock
`PROVIDER_MODE=mock` uses `MockMarketDataAdapter("dataforseo")`:
- Deterministic series and offers, labelled `mock` in every snapshot URL (`mock://market-data/...`).
- It charges the same cost estimates, so the run cap can be tested.
