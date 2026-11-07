# Claude vision (receipt OCR, primary)

Adapter: `apps/api/src/momentsales/adapters/ocr/claude.py`. Mock: `adapters/ocr/mock.py`. Selected by `PROVIDER_OCR=claude`.

Checked against the official docs on 2026-11-07:
- https://platform.claude.com/docs/en/build-with-claude/vision
- https://platform.claude.com/docs/en/build-with-claude/pdf-support
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- https://platform.claude.com/docs/en/about-claude/models/overview

| Item | Value |
| --- | --- |
| Endpoint | `POST https://api.anthropic.com/v1/messages` (via the `anthropic` SDK) |
| Auth | `x-api-key: <ANTHROPIC_API_KEY>`, `anthropic-version` set by the SDK |
| Model | `OCR_CLAUDE_MODEL`, default `claude-haiku-5-5` (the docs list it for extraction tasks) |
| Images | `image/jpeg`, `image/png`, `image/gif`, `image/webp`; ≤ 10 MB base64; ≤ 8000×8000 px. **HEIC not supported** → converted to JPEG before sending |
| Image block | `{"type": "image", "source": {"type": "base64", "media_type": …, "data": …}}`, placed before the text prompt |
| PDF block | `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": …}}` |
| Structured output | `output_config.format = {"type": "json_schema", "schema": …}`; every object has `additionalProperties: false` and all fields `required` (nullable via `["string","null"]`) |
| Stop reasons | `refusal` and `max_tokens` are treated as OCR failures (the user types the fields) |

Extraction schema: `vendor`, `date` (ISO `YYYY-MM-DD`), `total`, `tax`, `currency` (ISO 4217) — all nullable strings; amounts are parsed into minor units by our code, not by the model. The prompt tells the model to copy values as printed, return null when a value isn't visible, and ignore any instructions in the image (receipts are untrusted input).

Errors we fail over on (to Textract): 429, 5xx/529 overloaded, timeouts. Not failed over: 400/401/403.

Data: receipt images go to Anthropic as a sub-processor (listed in the DPA sub-processor list in Phase 4).
