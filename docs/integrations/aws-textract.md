# AWS Textract AnalyzeExpense (receipt OCR, secondary)

Adapter: `apps/api/src/momentsales/adapters/ocr/textract.py`. Selected by `PROVIDER_OCR=textract` or as `PROVIDER_OCR_SECONDARY`.

Checked against the official docs on 2026-11-07:
- https://docs.aws.amazon.com/textract/latest/dg/invoices-receipts.html
- https://docs.aws.amazon.com/textract/latest/dg/API_AnalyzeExpense.html
- https://docs.aws.amazon.com/textract/latest/dg/limits-document.html

| Item | Value |
| --- | --- |
| Call | `AnalyzeExpense` (boto3 `textract.analyze_expense(Document={"Bytes": …})`) |
| Formats | JPEG, PNG, PDF, TIFF (no XFA or password-protected PDFs). HEIC is converted to JPEG before OCR (ADR-0014) |
| Sync limits | 10 MB; PDF/TIFF **1 page only** synchronously; ≤ 10000 px per side |
| Multi-page PDF | Needs async `StartExpenseAnalysis` + S3; not used — multi-page PDFs go to the next provider or manual entry |
| Response | `ExpenseDocuments[].SummaryFields[]` with `Type.Text`, `ValueDetection.Text`, `ValueDetection.Confidence`, `Currency.Code` |
| Fields mapped | `VENDOR_NAME` → vendor; `INVOICE_RECEIPT_DATE` → date; `TOTAL` (fallback `AMOUNT_PAID`) → total; `TAX` → tax; `Currency.Code` → currency |
| Errors | `UnsupportedDocumentException`, `DocumentTooLargeException`, `BadDocumentException` → manual entry; throttling/5xx → failover |
| Region | `AWS_REGION` (default `eu-west-2`). London availability **not verified** on the pages read — check https://docs.aws.amazon.com/general/latest/gr/textract.html before going live |

Dates from Textract are free text as printed; our parser accepts ISO and UK day-first formats only, otherwise the date is left empty for the user.
