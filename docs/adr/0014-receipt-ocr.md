# ADR-0014: Receipt OCR with two providers

- Status: Accepted (Phase 1)
- Date: 2026-11-07

## Context
FR-7: receipts are OCR'd to pre-fill vendor, date, total and tax; the user confirms before saving. FR-15: OCR is a critical category with two adapters, one switchable by config, with automatic failover for read-only providers.

## Decision
- `OcrPort.extract(content, mime) → OcrResult{vendor, date, total_minor, tax_minor, currency, provider, raw}`.
- **Primary: Claude vision** (`claude-haiku-5-5` by default, structured JSON output). **Secondary: AWS Textract AnalyzeExpense.** `PROVIDER_OCR` / `PROVIDER_OCR_SECONDARY` / `PROVIDER_OCR_FAILOVER=auto|manual`, overridable in `provider_configs` like email.
- Failover on rate limits, 5xx and timeouts; not on bad documents or auth errors.
- OCR runs in the worker after upload. The result only pre-fills the confirm form; nothing reaches the ledger until a human confirms.
- Amounts are parsed by our code from the text the provider returns (copied as printed); the model never computes totals.
- Receipt content is treated as untrusted input: the prompt tells the model to ignore any instructions in the image.
- HEIC uploads are stored as uploaded; OCR gets a JPEG conversion. (Conversion library pending approval; until then HEIC receipts skip OCR and are entered by hand.)

## Consequences
- With no keys, the mock adapter returns deterministic results from a fixture map keyed by file hash, so tests and demos work offline.
