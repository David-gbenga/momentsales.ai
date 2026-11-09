# ADR-0021: Scoring, compliance and the Top 10 report

- Status: Accepted (Phase 2d)
- Date: 2026-11-09

## Context
Phases 2a–2c store the season, rising searches, competition and verified UK suppliers for each run in `run.demand`. The PRD's deliverable is a Top 10 report (FR-4) with these parts:
- a demand score (FR-3) and a one-line "why"
- a profit forecast (FR-13)
- UK compliance flags and vetoes (FR-14)
- PDF and CSV export, and a shareable read-only link

Owner decisions on 2026-11-09:
- **LLM:** Claude Opus 5.5.
- **Ad cost:** a configurable % of the selling price per category, labelled as an estimate.
- **Sales rank and review velocity:** no data source since eBay and Amazon were dropped, so the part is dropped and the weights are rescaled.
- **Share links:** expiring and revocable.

## Decision
- **Pipeline:** `season → trend → marketplace → supplier → scoring → compliance → report`.

### Scoring (`modules/research/scoring.py`, pure functions)
- **Dedup:** candidates whose plural-stripped token sets are equal, or whose overlap is at least 0.8, merge into the one with more data.
- **Parts (each 0–1)** and their default weights (`RESEARCH_SCORE_WEIGHTS`):

| Part | Weight | How it's computed |
| --- | --- | --- |
| rising | 0.25 | Growth mapped from ≤ 0% to ≥ +100%, halved when crowded (AC-25) |
| competition | 0.15 | 1 − score/100 |
| sales_rank | 0.15 | No source: dropped, weights rescaled |
| seasonal | 0.15 | 1 when in time, 0.5 when delivery is unknown, 0 when too late (AC-21) |
| profit | 0.20 | Profit ÷ price mapped from ≤ 0% to ≥ 40% |
| supplier | 0.10 | Verified, by max delivery days |

- **Missing data:** a missing part scores 0, so no data never beats data.
- **Audience fit:** adds up to ±10 points.
- **Final score:** 0–100, rounded.

### Profit (`modules/research/profit.py`)
- Profit = selling price − unit cost − shipping − Stripe fee − platform fee − ad cost.
- The selling price is the median in-band market price, an estimate.
- The ad cost is selling price × `RESEARCH_AD_COST_BPS`, with per-category overrides; an estimate.
- Break-even ad cost = profit before ads.
- All figures are integer minor units, with every input stored.

### Compliance
- **Scope:** the top 16 by score.
- **UK rules list** (`compliance_rules.py`, versioned):
  - Restricted items are **blocked** (vetoed).
  - Regulated categories (electrical, toy, cosmetic, PPE, children's) **need documents**.
  - Trademark and health-claim terms are flagged.
- **Claude classifier:** one structured-output call returns the same flag set. **Flags = rules ∪ LLM**: the LLM can add flags but never clear a rule's flag.
- **If the LLM is unavailable:** rules only, and the step is degraded.
- **What happens to each flag:**
  - Blocked items are excluded from the Top 10 and listed with the reason.
  - "Needs documents" items stay in the report and can't be listed until the supplier provides documents (enforced with listings, AC-29).
  - An import VAT notice shows when the supplier ships from outside the UK.

### Report
- **Selection:** the top 10 by score that aren't blocked, preferring items with market data. Nothing is padded.
- **"Why" lines:** a batched Claude call writes them from facts only. A guard rejects any number or £ amount that isn't in the facts, and falls back to a template line.
- **Storage:** stored in `reports` and `report_items` (workspace data, RLS forced, immutable). Prices, links, delivery and supplier details keep their snapshot ids (AC-3).

### Exports
- CSV and PDF are generated on request: stdlib `csv`, and WeasyPrint as in the audit pack.
- They're open on every plan (ADR-0016).

### Share links
- **Token:** an opaque token stored as SHA-256 in `report_shares`.
- **Expiry:** default 30 days, max 90. Revocable and audit-logged.
- **Public endpoint:** `GET /public/reports/{token}` works in platform context by token hash, is rate-limited and `no-store`, and leaves out the run cost and workspace details.
- **Permission:** `reports.share` (Owner, Admin, Analyst), gated by the subscription like other writes.

### LLM cost
- Metered in the run's `CostMeter`: an estimate before the call, actual usage after.
- It counts toward the £1 cap. At the cap, the report is still written from what was gathered, and the run is `partial`.

## Consequences
- Scores are explainable: every part, weight and dropped part is stored and shown.
- The sales-rank part can be switched back on, with no schema change, once a licensed marketplace source exists.
- The "Why this product?" follow-up (`POST /reports/{id}/ask`) and Product/ProductOffer tables come later (listings, Phase 3).
