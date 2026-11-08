# Nager.Date (public holidays)

Used by the Season agent for public holidays inside a run's look-ahead window (ADR-0018). Adapter: `adapters/holidays/nager.py`; mock: `adapters/holidays/mock.py`.

Checked 2026-11-08 against https://nagerholidays.com/Api. The old https://date.nager.at/Api now redirects there.

## Endpoint
- `GET https://nagerholidays.com/api/v4/Holidays/{CountryCode}/{Year}`
  - `CountryCode`: ISO 3166-1 alpha-2, e.g. `GB`.
  - `Year`: e.g. `2026`.
- The docs page documents v4 only. The older v3 path (`https://date.nager.at/api/v3/PublicHolidays/{Year}/{CountryCode}`) still answers but is undocumented, so we don't use it.

## Response (v4 "Response Model")
A JSON array of holiday objects with these fields:
- `date`
- `name`
- `countryCode`
- `subdivisionCodes`
- `nationalHoliday`
- `holidayTypes`: values listed as Public, Bank, School, Authorities, Optional, Observance.

We read `date`, `name` and `nationalHoliday`. The whole response is stored as a `source_snapshots` row (provider `nager`, kind `public_holidays`) with the request URL and fetch time.

## Auth, limits and terms
- No API key.
- The docs page states "No Rate Limits".
- Using the API means agreeing to https://nagerholidays.com/legal/termsofservice. Read those terms before production.
- Nager.Date is open source (https://github.com/nager/Nager.Date).
- No attribution requirement is stated on the docs page.

## Settings
- `PROVIDER_HOLIDAYS=nager`
- `NAGER_BASE_URL=https://nagerholidays.com/api/v4`
- `PROVIDER_MODE=mock` uses the mock, which computes the England and Wales bank-holiday pattern locally (plus a short AU list).

## Failure handling
Any network error, non-200 status or unexpected body raises `HolidaysError`. The Season step finishes as `degraded`, `public_holidays` is `null`, and the UI shows "Not available". The run is not failed.
