# SerpApi (market data, secondary)

The secondary `market_data` provider (FR-15, ADR-0019). It can be switched on by config (`PROVIDER_MARKET_DATA=serpapi`), and the registry fails over to it automatically when DataForSEO has a retryable error. It runs on separate infrastructure from DataForSEO. Adapter: `adapters/market_data/serpapi.py`.

Checked 2026-11-08 against these serpapi.com pages: google-trends-api, google-trends-related-queries, search-api, google-ads, inline-shopping, shopping-results, pricing, legal (updated 27 Aug 2026), account-api, and api-status-and-error-codes.

## Auth
- The `api_key` query parameter (`SERPAPI_API_KEY`).
- The key is **removed from every URL we store** in snapshots or the cache, and a test checks this.
- `SERPAPI_BASE_URL` defaults to `https://serpapi.com`.

## Endpoints used
All are `GET /search.json`.

### Google Trends, interest over time
Request:
- `engine=google_trends`
- `data_type=TIMESERIES`
- `q`: up to 5 comma-separated queries, each up to 100 characters
- `geo=GB`
- `date=today 12-m`
- `tz=0`

Response: `interest_over_time.timeline_data[]`. Each entry has `date`, `timestamp` (a Unix-time string) and `values[]`. Each value has `query`, `value` (text) and `extracted_value` (a number). We use `extracted_value`. A point marked `partial_data` (the current, unfinished week) is dropped.

### Google Trends, related queries
Request:
- `engine=google_trends`
- `data_type=RELATED_QUERIES`
- `q`: **a single query only**
- `geo=GB`
- `date=today 3-m`

Response: `related_queries.rising[]`. Each entry has `query`, `value` (text such as `"+250%"` or `"Breakout"`) and `extracted_value` (a number). We use `extracted_value`; `Breakout` is kept as a flag.

### Google Search
Request:
- `engine=google`
- `q`
- `gl=uk` (SerpApi uses `uk`, not `gb`)
- `hl=en`
- `google_domain=google.co.uk`

Response:
- `ads[]`: counted as text ads.
- `shopping_results[]`: shopping ads. Each has `title`, `source`, `link`, `price` (text) and `extracted_price` (a number). The currency comes only from the price text (`£` → GBP). A price in any other currency, or with no currency, is not used.
- `search_information.total_results`.

## Errors and limits
- The error body is `{"error": "..."}`.
- HTTP 400, 401, 403 and 404 are not retryable. 429 (over the hourly limit or out of searches) and 500 or 503 are retryable.
- An HTTP 200 can still carry `error`. "Google hasn't returned any results for this query." is treated as an empty result. Any other `error` is not retryable.
- Throughput is per plan per hour: 50 an hour on Free, 200 on Starter.
- Only successful searches are counted. Cached searches (SerpApi caches identical requests for 1 hour) are free.

## Prices (USD a month, checked 2026-11-08)
| Plan | Price | Searches a month | Searches an hour | Legal Shield |
| --- | --- | --- | --- | --- |
| Free | $0 | 250 | 50 | No |
| Starter | $25 | 1,000 | 200 | No |
| Developer | $75 | 5,000 | 1,000 | No |
| Production | $150 | 15,000 | 3,000 | Yes |

- The pricing page doesn't say whether unused searches roll over. Its FAQ suggests they reset each billing cycle.
- A run on SerpApi makes about 20 searches. At $25 for 1,000 that's $0.025 each, so the meter charges `COST_SERPAPI_SEARCH_MICROS` (default 20,000, about £0.02) per search. This is an internal estimate for the cost cap only.

## Terms (to confirm before going live)
- **What the terms say:**
  - ToS section 2 says you may not "reproduce, duplicate, copy, sell, resell or exploit any portion of the Service" without written permission.
  - Nothing found on caching or attribution.
  - SerpApi keeps search data for 31 days.
- **Before switching `PROVIDER_MODE=live`, the owner must get written confirmation.** Draft email:

  > Subject: Showing results derived from SerpApi to our SaaS customers
  >
  > Hello, we're building momentsales.ai, a UK SaaS for small online sellers. We'd like to use the Google Trends and Google Search engines to compute signals (growth rate, number of ads, price range of shopping results, with links) and show them to our paying customers, and to reuse a result for up to 24 hours for other customers who search the same term. We don't resell raw results or offer API access. Is this covered by your terms (section 2), and is any attribution required? Thank you.

## Mock
`MockMarketDataAdapter("serpapi")` uses the same deterministic data as the primary's mock. AC-34 tests run the same research flow with `PROVIDER_MARKET_DATA=serpapi`.
