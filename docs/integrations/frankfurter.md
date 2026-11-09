# Frankfurter (FX rates)

The `fx` provider (ADR-0020). It converts supplier costs in other currencies (CJ prices are in USD) to GBP. Adapter: `adapters/fx/frankfurter.py`.

Checked 2026-11-09 against frankfurter.dev, frankfurter.dev/v1 and a live call.

- **Endpoint:** `GET https://api.frankfurter.dev/v1/latest?base=USD&symbols=GBP` (`FRANKFURTER_BASE_URL`, default `https://api.frankfurter.dev/v1`).
- **Response:** `{"amount": 1.0, "base": "USD", "date": "2026-11-06", "rates": {"GBP": 0.74711}}`. The `date` is the date of the European Central Bank's reference rate.
- **Source:** v1 serves European Central Bank reference rates, updated each working day at about 16:00 CET. v1 is deprecated in favour of v2, which blends many central banks, but the docs say v1 "remains available indefinitely". We use v1 because it is ECB-only.
- **Key and limits:** no API key, no quotas, and rate limiting against abuse only. The FAQ says the rates are free for commercial use and encourages storing them locally.
- **Old host:** `api.frankfurter.app` now returns a 301, so it isn't used.
- **Caching:** a rate is public, non-personal data, so it goes in the shared market-data cache (`data_type = fx_rate`) and is reused for 24 hours.
- **Stored with every conversion:** the rate, the ECB rate date, the provider and the snapshot id.
- **Errors:** HTTP 5xx and 429 are retryable. Anything else isn't. If FX is unavailable, a non-GBP cost shows as "not available" and the margin check is "not checked", which means it doesn't pass.
- **Mock:** `MockFxAdapter` returns a fixed USD→GBP rate of 0.75 with a `mock://` URL.
- **Cost:** free (£0).
