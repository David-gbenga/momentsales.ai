# Avasam (supplier, secondary)

The secondary `supplier` provider (FR-11, FR-15, ADR-0020). You switch to it in config with `PROVIDER_SUPPLIER=avasam`. Adapter: `adapters/supplier/avasam.py`.

**Per seller, never shared (owner decision, 2026-11-09).** Avasam's seller API only lists products the seller has already sourced into their own Avasam account. It has no catalogue search, and its terms forbid redistributing its data and caching it except in a browser. So:
- Each workspace connects its own keys, which are stored encrypted (`integrations.credentials_encrypted`).
- Matching searches only that seller's products.
- Avasam results are **never written to the shared cache**. They only go into the run's own source snapshots.

Checked 2026-11-09 against: help.avasam.com/docs/seller-api, help.avasam.com/docs/returns-refunds, avasam.com/pricing, avasam.com/faqs, avasam.com/terms-and-conditions (last updated 29/09/23), avasam.com/refunds-cancellation-policy.

## Auth
- **Keys:** a seller creates them in Avasam under Settings → User Management → API keys. Each key pair is a consumer key and a secret key.
- **Token:** `POST https://app.avasam.com/api/auth/request-token` with body `{"consumer_key", "secret_key"}`. The response holds `access_token` and `expires_at`.
- **Header: not documented.** The docs don't say how the token is sent on later calls. The adapter sends `Authorization: Bearer <access_token>`, which still needs confirming with Avasam.
- **Connecting:** `POST /integrations/avasam/connect` checks the keys with a token call before saving them.

## Endpoints used
`AVASAM_BASE_URL` defaults to `https://app.avasam.com`.
- **Product list:** `GET /apiseeker/Products/GetSellerProductList?Page=&Limit=`.
  - Fields: `SKU`, `Title`, `Price` (the seller's cost price), `RetailPrice`, `Image`, `ProductImage[]`, `Category`, `Variations[]`.
- **Stock:** `GET /apiseeker/Products/SellerStockList`, which returns `SKU` and `Stock`.
- **Keyword filtering:** the API can't search by keyword, so the adapter reads up to `AVASAM_MAX_PAGES` pages and filters by keyword locally.

## What the API doesn't return
- **Currency:** not in the response. Avasam is a UK platform with GBP pricing on its site, so the adapter treats prices as GBP. This still needs confirming.
- **Dispatch country, delivery time and shipping cost:**
  - The warehouse country is taken as GB, from Avasam's UK-supplier model on its homepage and FAQ. Note that the pricing page FAQ mentions USA-based suppliers, so this needs confirming.
  - The delivery range comes from Avasam's published service tiers: expedited tracked 1–2 days, standard tracked 2–4 days, standard 3–5 days, with dispatch within 24 hours. These are recorded in the `tracked_uk` verification evidence. The adapter's freight quote returns the standard tracked tier with `source: verification` and **no cost**.
  - Shipping cost is "not available", so the margin check (check 5) is "not checked" and Avasam matches show "no verified supplier" until Avasam confirms where shipping cost comes from.

## Errors and limits
- Order calls return `{"ErrorCode": 0|1, "Message"}`. No general error format is documented.
- HTTP 401 and 403 aren't retryable. 429 and 5xx are retryable. The docs mention a rate limit but give no number.

## Prices
- Free plan £0; paid plans from about £19.99–£24.99 + VAT a month (the pricing page and FAQ disagree).
- No plan lists API access as a feature. Confirm whether the free plan includes it.

## Returns
- Change-of-mind returns: 30 days. The help centre counts from shipping and the FAQ from delivery.
- Faulty items: 60 days.
- The supplier's return address is shown in the seller portal; no public UK address was found.
- These go in the `uk_returns` verification evidence.

## Terms (to confirm before going live)
- **What the T&C say:** clause 5.5(a) forbids automated or systematic data collection without explicit permission. The summary of the remaining T&C says material may not be redistributed or republished, and caching is allowed only in a web browser.
- **What we do:** a seller connects their own keys, so momentsales.ai shows a seller their own Avasam products, through Avasam's official seller API, and caches nothing across workspaces.
- **Draft email (owner to send):**

  > Subject: Seller API use by momentsales.ai
  >
  > Hello, we're building momentsales.ai, a UK SaaS for small online sellers. Our customers would connect their own Avasam seller API keys so we can match products they have sourced on Avasam to seasonal demand, and later place their dropship orders. Could you confirm: (1) this use of the seller API is allowed, and whether we may show product titles, images and cost prices to the seller and product images to their shoppers; (2) whether a catalogue search API is available to partners; (3) how the access token is sent (header name); (4) whether prices are always GBP and where shipping cost and dispatch country come from in the API; (5) whether API access needs a paid plan and its rate limit. Thank you.

## Mock
`MockSupplierAdapter("avasam")` uses the same deterministic items as the CJ mock, priced in GBP. AC-34 tests run the same research flow with `PROVIDER_SUPPLIER=avasam`.
