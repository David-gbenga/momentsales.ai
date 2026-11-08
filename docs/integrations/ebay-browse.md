# eBay Browse API: not used in the MVP

Planned for Phase 2b as marketplace evidence (FR-1 Marketplace agent), then **dropped by owner decision on 2026-11-08** after reading eBay's terms. The `marketplace` provider category is registered with no adapters, so eBay (or Amazon) can be added later without a rewrite.

Checked 2026-11-08:
- the `item_summary/search` reference on developer.ebay.com
- the API License Agreement (developer.ebay.com/join/api-license-agreement)
- the Buy APIs requirements page

## Findings
- **Endpoint:**
  - `GET https://api.ebay.com/buy/browse/v1/item_summary/search` (sandbox: `api.sandbox.ebay.com`).
  - Needs an application token from the client credentials grant with scope `https://api.ebay.com/oauth/api_scope`.
  - The marketplace is set with the `X-EBAY-C-MARKETPLACE-ID` header (`EBAY_GB`).
  - A search returns at most 10,000 items.
- **Production access:** the Buy APIs requirements page says many Buy APIs are a Limited Release for eBay partners. Production access means applying through the eBay Partner Network, and approval isn't guaranteed.
- **Licence, "Prohibited Use and Derivation of Information":** without eBay's express prior written permission, eBay content may not be used in a way that lets anyone derive:
  - site-wide statistics
  - average selling price for any eBay category
  - aggregated seller data
  - data on seller performance, individually or in aggregate

  The competition-score inputs we planned (listing count, seller count, price spread) fall under this.
- **Licence, age of displayed content:** listing data shown in an app must be no more than 6 hours older than on eBay, and other eBay content no more than 24 hours older.

## What the MVP does instead
The competition score uses Google results data from the `market_data` providers: ad count, shopping-ad and offer counts, distinct merchants, the price spread of shopping offers, and the results count (ADR-0019).

## To add eBay later
1. Join the eBay Partner Network and apply for Buy API production access.
2. Ask eBay for written permission for any aggregated use. Otherwise show only individual listings as evidence links.
3. Keep displayed listings under 6 hours old.
4. Add `adapters/marketplace/ebay.py` under the registered `marketplace` category.
