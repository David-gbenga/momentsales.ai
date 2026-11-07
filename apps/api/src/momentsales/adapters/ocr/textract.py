"""AWS Textract AnalyzeExpense (secondary OCR). See docs/integrations/aws-textract.md.

Synchronous calls accept one-page PDFs only. boto3 is sync, so calls run in a thread.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

from momentsales.adapters.ocr.base import (
    OcrError,
    OcrResult,
    clean_vendor,
    parse_amount,
    parse_currency,
    parse_date,
)

if TYPE_CHECKING:
    from types_boto3_textract import TextractClient

NOT_RETRYABLE = frozenset(
    {
        "UnsupportedDocumentException",
        "DocumentTooLargeException",
        "BadDocumentException",
        "InvalidParameterException",
        "AccessDeniedException",
    }
)
SUPPORTED = frozenset({"image/jpeg", "image/png", "application/pdf"})


def summary_fields(response: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    """First value per field type, with its currency code if Textract gave one."""
    fields: dict[str, tuple[str, str | None]] = {}
    for doc in response.get("ExpenseDocuments", []):
        for f in doc.get("SummaryFields", []):
            kind = (f.get("Type") or {}).get("Text")
            value = (f.get("ValueDetection") or {}).get("Text")
            if kind and value and kind not in fields:
                fields[kind] = (value, (f.get("Currency") or {}).get("Code"))
    return fields


class TextractOcrAdapter:
    name = "textract"

    def __init__(self, client: TextractClient) -> None:
        self._client = client

    async def extract(self, content: bytes, mime: str) -> OcrResult:
        if mime not in SUPPORTED:
            raise OcrError(self.name, f"unsupported type {mime}", retryable=False)

        def _call() -> dict[str, Any]:
            return dict(self._client.analyze_expense(Document={"Bytes": content}))

        try:
            response = await asyncio.to_thread(_call)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            raise OcrError(self.name, code or "error", retryable=code not in NOT_RETRYABLE) from exc
        except BotoCoreError as exc:
            raise OcrError(self.name, type(exc).__name__, retryable=True) from exc
        fields = summary_fields(response)
        total_text, total_ccy = fields.get("TOTAL") or fields.get("AMOUNT_PAID") or (None, None)
        tax_text = (fields.get("TAX") or (None, None))[0]
        currency = parse_currency(total_ccy, total_text)
        return OcrResult(
            provider=self.name,
            vendor=clean_vendor((fields.get("VENDOR_NAME") or (None, None))[0]),
            date=parse_date((fields.get("INVOICE_RECEIPT_DATE") or (None, None))[0]),
            total_minor=parse_amount(total_text, currency),
            tax_minor=parse_amount(tax_text, currency),
            currency=currency,
            raw={"fields": {k: v[0] for k, v in fields.items()}},
        )
