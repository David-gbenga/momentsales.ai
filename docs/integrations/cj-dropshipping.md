# CJ Dropshipping (supplier, primary)

The primary `supplier` provider (FR-11, FR-15, ADR-0020). Adapter: `adapters/supplier/cj.py`. One **platform** CJ account searches the catalogue for research. Sellers connect their own CJ account for orders in Phase 3.

Checked 2026-11-09 against these developers.cjdropshipping.com pages: introduction, start/development, start/token, start/limit, api2/api/auth, api2/standard/points, api2/standard/ps-code, api2/api/product, api2/api/logistic.

## Auth
- **Base URL:** `CJ_BASE_URL`, default `https://developers.cjdropshipping.com/api2.0/v1`.
- **Get a token:** `POST /authentication/getAccessToken` with body `{"apiKey": CJ_API_KEY}`.
  - The response `data` holds `accessToken`, `accessTokenExpiryDate`, `refreshToken` and `refreshTokenExpiryDate`.
  - Both tokens last 180 days.
  - Calling again within 24 hours returns the same tokens.
  - Limit: 1 call per second.
- **Refresh:** `POST /authentication/refreshAccessToken` with `{"refreshToken"}`.
- **Header:** later calls send `CJ-Access-Token: <accessToken>`.
- **Token cache:** the adapter keeps the token in Redis (`cj:access-token`) until one hour before it expires. The token is never stored in snapshots, URLs or logs.

## Endpoints used
- **Search:** `GET /product/listV2?keyWord=&countryCode=GB&page=1&size=20`.
  - `countryCode` filters to products with inventory in that country.
  - Results come from `data.content[].productList[]`, with fields `id`, `nameEn`, `sku`, `bigImage`, `sellPrice` (a USD string), `warehouseInventoryNum`.
  - Costs **50 points** per call.
- **Variants and GB stock:** `GET /product/variant/query?pid=&countryCode=GB`.
  - Variant fields: `vid`, `variantNameEn`, `variantSku`, `variantSellPrice` (USD), `variantWeight` (g), and `inventories[]` with `countryCode` and `totalInventoryNum`.
  - The adapter sums `totalInventoryNum` where `countryCode == "GB"`.
- **Freight:** `POST /logistic/freightCalculate` with body `{"startCountryCode": "GB", "endCountryCode": "GB", "products": [{"vid", "quantity": 1}]}`.
  - Response `data[]` fields: `logisticName`, `logisticPrice` (USD), `logisticAging` (e.g. `"2-5"`).

## Envelope and errors
- **Envelope:** `{"code", "result", "message", "data", "requestId"}`, sometimes with `pointsInfo`.
- **Success rule:** HTTP 200 and `code` is 200, 0, or absent. `message` is for reference only.
- **Retryable:**
  - `1600200`: too many requests
  - `16900500`: not enough points
  - `1608002`: warehouse data temporarily unavailable
  - HTTP 429, including `code: 429` in the body
  - HTTP 5xx
- **Not retryable:**
  - Auth errors: `1600001` to `1600008`
  - `1600014`: API access disabled
  - `1601000`: user not found
  - `1602001` / `1602000`: product or variant not found
- Suppliers never fail over automatically (FR-15), so "retryable" only affects how an outage is labelled.

## Limits and points
- **Request rate:** 10 requests a second per IP. Per user it depends on account level: Free 1/s, Plus 2/s, Prime 4/s, Advanced 6/s.
- **Daily points:**
  - 50,000 base, plus 100 per USD of the account's recent order value.
  - Most calls cost 10 points; `listV2` costs 50.
  - Points top up every minute (total / 1440).
  - `pointsInfo` from each response is recorded in the run's tool calls.
- **Suspension:** API access is suspended after **30 consecutive days with no transactions**. The platform account needs regular sample orders, or CJ's agreement, before going live.

## Prices
- The docs state no fee for API access.
- Prices are in **USD**. They are converted to GBP with the FX adapter (`docs/integrations/frankfurter.md`), and the rate, source and date are stored with each match.
- In the cost meter CJ calls are £0 (`COST_CJ_CALL_MICROS`, default 0).

## Not yet confirmed (check against a live sample)
- **GB warehouse:** the docs accept `countryCode=GB`, but no example shows GB stock, and the global warehouse list example shows only CN and US.
- **Delivery days:** the unit of `logisticAging` isn't stated. The adapter reads it as days.
- **Tracking:** the freight response doesn't say whether an option is tracked. The adapter marks an option tracked only when its name contains "tracked", and otherwise leaves it unknown. Unknown counts as untracked, which fails check 3 of the Verified UK supplier rule.
- **Returns:** no returns-policy endpoint was found. CJ's UK returns address and policy go in the `supplier_verifications` evidence.

## Terms (to confirm before going live)
- **What the docs say:** the developer docs contain no terms of use and nothing about showing or caching data. CJ's main site terms weren't reviewed in detail.
- **Before `PROVIDER_MODE=live`, the owner must get written confirmation.** Draft email:

  > Subject: Using the CJ API for product research shown to our SaaS customers
  >
  > Hello, we're building momentsales.ai, a UK SaaS that helps small online sellers find seasonal products and dropship them. We'd like to use one CJ API account to search your catalogue for products with UK warehouse stock (`countryCode=GB`) and show the matching product, its image, cost and UK delivery estimate to our customers, reusing a search result for up to 24 hours for other customers who search the same term. Our customers would then connect their own CJ accounts to place orders. Could you confirm: (1) this use is allowed under your terms, and any attribution needed; (2) that you hold stock in a UK warehouse and which shipping methods from it are tracked; (3) the unit of `logisticAging`; (4) whether the 30-day no-transaction suspension applies to an account used only for catalogue search; (5) your UK returns address and returns policy. Thank you.

## Mock
`MockSupplierAdapter("cj")` returns deterministic UK-stock items, untracked and slow options, a low-margin item and no-match cases, in USD so the FX path is tested.
