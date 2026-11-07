"""Ledger, receipts and P&L through the API (AC-8, AC-9, AC-28, AC-34 for OCR)."""

from __future__ import annotations

import json
import uuid
from datetime import date

import httpx
import pytest
from helpers import Session, create_workspace, sign_in

from momentsales.core.config import get_settings

TODAY = date.today().isoformat()  # noqa: DTZ011
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def receipt_bytes(**fields: str) -> bytes:
    return PNG + b"MOCK-OCR:" + json.dumps(fields).encode()


async def owner_ws(client: httpx.AsyncClient) -> tuple[Session, str]:
    owner = await sign_in(client)
    return owner, await create_workspace(owner, "Ledger Shop")


async def post(
    s: Session, ws: str, path: str, body: object, key: str | None = None
) -> httpx.Response:
    headers = s.headers(ws)
    if key:
        headers["Idempotency-Key"] = key
    return await s.client.post(f"/api/v1{path}", json=body, headers=headers)


async def get(s: Session, ws: str, path: str) -> httpx.Response:
    return await s.client.get(f"/api/v1{path}", headers=s.headers(ws))


def spend(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "amount": "24.99",
        "currency": "GBP",
        "spent_on": TODAY,
        "vendor": "Royal Mail",
        "category": "shipping",
        "payment_method": "card",
    }
    body.update(over)
    return body


async def test_ac8_spend_with_receipt_ocr_prefill(client: httpx.AsyncClient) -> None:
    owner, ws = await owner_ws(client)
    # /spend 24.99 GBP shipping vendor:Royal Mail -> saved, flagged as missing a receipt
    r = await post(owner, ws, "/spends", spend(), key="spend-1")
    assert r.status_code == 201, r.text
    spend_id = r.json()["id"]
    page = (await get(owner, ws, "/spends")).json()
    assert page["missing_receipts"] == 1

    content = receipt_bytes(vendor="Royal Mail", date=TODAY, total="£24.99", tax="£0.00")
    r = await owner.client.post(
        "/api/v1/receipts",
        files={"file": ("receipt.png", content, "image/png")},
        headers=owner.headers(ws),
    )
    assert r.status_code == 201, r.text
    receipt_id = r.json()["id"]
    import hashlib

    assert r.json()["sha256"] == hashlib.sha256(content).hexdigest()

    # OCR ran (inline in tests) and pre-filled the fields; nothing saved yet
    detail = (await get(owner, ws, f"/receipts/{receipt_id}")).json()
    assert detail["ocr_status"] == "done"
    assert detail["ocr"]["vendor"] == "Royal Mail"
    assert detail["ocr"]["total_minor"] == 2499
    assert detail["ocr"]["currency"] == "GBP"
    assert detail["download_url"]

    # Confirming attaches it to the spend: the missing-receipt flag clears
    r = await post(owner, ws, f"/receipts/{receipt_id}/confirm", {"spend_id": spend_id})
    assert r.status_code == 201, r.text
    page = (await get(owner, ws, "/spends")).json()
    assert page["missing_receipts"] == 0
    assert page["items"][0]["receipt_id"] == receipt_id

    # The signed link downloads the original, unaltered
    dl = await owner.client.get(detail["download_url"])
    assert dl.status_code == 200
    assert dl.content == content


async def test_receipt_confirm_creates_spend_from_checked_values(
    client: httpx.AsyncClient,
) -> None:
    owner, ws = await owner_ws(client)
    r = await owner.client.post(
        "/api/v1/receipts",
        files={"file": ("r.pdf", b"%PDF-1.7\n" + b"x" * 20, "application/pdf")},
        headers=owner.headers(ws),
    )
    receipt_id = r.json()["id"]
    # No fixture marker: the mock reads nothing, so every field is left for the user
    detail = (await get(owner, ws, f"/receipts/{receipt_id}")).json()
    assert detail["ocr"]["vendor"] is None and detail["ocr"]["total_minor"] is None
    r = await post(
        owner,
        ws,
        f"/receipts/{receipt_id}/confirm",
        {"spend": spend(vendor="Canva", category="software", amount="10.99")},
    )
    assert r.status_code == 201, r.text
    page = (await get(owner, ws, "/spends")).json()
    assert page["missing_receipts"] == 0
    assert page["items"][0]["source"] == "receipt"


async def test_ac9_touch_appears_in_ledger_summary(client: httpx.AsyncClient) -> None:
    owner, ws = await owner_ws(client)
    # /touch pricing 10m "adjusted margin on hoodie"
    r = await post(
        owner,
        ws,
        "/touches",
        {"category": "pricing", "minutes": 10, "description": "adjusted margin on hoodie"},
    )
    assert r.status_code == 201, r.text
    body = (await get(owner, ws, "/touches?auto=false")).json()
    assert body["items"][0]["description"] == "adjusted margin on hoodie"
    assert body["items"][0]["minutes"] == 10
    assert body["summary"]["manual_count"] == 1
    assert body["summary"]["manual_minutes"] == 10
    assert body["summary"]["auto_count"] >= 1  # workspace creation is an automatic touch


async def test_ac28_ads_spend_reduces_product_profit(client: httpx.AsyncClient) -> None:
    owner, ws = await owner_ws(client)
    r = await post(
        owner,
        ws,
        "/sales",
        {
            "sold_on": TODAY,
            "channel": "Market stall",
            "items": [
                {
                    "description": "Hoodie",
                    "quantity": 2,
                    "unit_amount": "30.00",
                    "product_key": "hoodie",
                },
                {"description": "Mug", "quantity": 1, "unit_amount": "12.00", "product_key": "mug"},
            ],
        },
        key="sale-1",
    )
    assert r.status_code == 201, r.text
    assert r.json()["gross_minor"] == 7200

    # Ads need a product
    r = await post(owner, ws, "/spends", spend(category="ads", vendor="Meta", amount="15.00"))
    assert r.status_code == 422
    r = await post(
        owner,
        ws,
        "/spends",
        spend(category="ads", vendor="Meta", amount="15.00", product_key="Hoodie"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["product_key"] == "hoodie"

    pnl = (await get(owner, ws, "/pnl?group_by=product_key")).json()
    lines = {line["product_key"]: line for line in pnl["by_product"]}
    assert lines["hoodie"]["revenue_minor"] == 6000
    assert lines["hoodie"]["ads_minor"] == -1500
    assert lines["hoodie"]["profit_minor"] == 4500
    assert lines["mug"]["profit_minor"] == 1200
    assert pnl["total"]["revenue_minor"] == 7200
    assert pnl["total"]["ads_minor"] == -1500
    assert pnl["total"]["profit_minor"] == 5700

    summary = (await get(owner, ws, "/sales/summary?period=day")).json()
    assert summary["totals"]["orders"] == 1
    assert summary["totals"]["gross_minor"] == 7200


async def test_void_creates_reversing_entry(client: httpx.AsyncClient) -> None:
    owner, ws = await owner_ws(client)
    spend_id = (await post(owner, ws, "/spends", spend(category="stock", amount="40.00"))).json()[
        "id"
    ]
    assert (await get(owner, ws, "/pnl")).json()["total"]["cogs_minor"] == -4000
    r = await post(owner, ws, f"/spends/{spend_id}/void", {"reason": "entered twice"})
    assert r.status_code == 201, r.text
    assert (
        await post(owner, ws, f"/spends/{spend_id}/void", {"reason": "again"})
    ).status_code == 409
    assert (await get(owner, ws, "/pnl")).json()["total"]["cogs_minor"] == 0
    entries = (await get(owner, ws, "/ledger?type=spend")).json()["items"]
    reversal = next(e for e in entries if e["source"] == "spend_void")
    original = next(e for e in entries if e["source"] == "spend")
    assert reversal["reverses_id"] == original["id"]
    assert reversal["amount_minor"] == -original["amount_minor"]
    page = (await get(owner, ws, "/spends")).json()
    assert page["items"][0]["voided"] is True
    assert page["missing_receipts"] == 0  # voided spends need no receipt


async def test_idempotency_key_replays_and_rejects_changed_body(
    client: httpx.AsyncClient,
) -> None:
    owner, ws = await owner_ws(client)
    first = await post(owner, ws, "/spends", spend(), key="same-key")
    again = await post(owner, ws, "/spends", spend(), key="same-key")
    assert first.status_code == again.status_code == 201
    assert again.headers.get("idempotent-replayed") == "true"
    assert again.json()["id"] == first.json()["id"]
    assert len((await get(owner, ws, "/spends")).json()["items"]) == 1
    changed = await post(owner, ws, "/spends", spend(amount="1.00"), key="same-key")
    assert changed.status_code == 422
    assert changed.json()["type"] == "idempotency_key_reused"


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (spend(amount="0"), "greater than zero"),
        (spend(amount="24.999"), "more decimals"),
        (spend(currency="USD"), "base_amount"),
        (spend(currency="XYZ"), "unsupported currency"),
        (spend(product_key="Not Valid!"), "pattern"),
    ],
)
async def test_spend_validation(
    client: httpx.AsyncClient, body: dict[str, object], fragment: str
) -> None:
    owner, ws = await owner_ws(client)
    r = await post(owner, ws, "/spends", body)
    assert r.status_code == 422
    assert fragment in r.text


async def test_foreign_currency_spend_uses_entered_gbp_amount(client: httpx.AsyncClient) -> None:
    owner, ws = await owner_ws(client)
    r = await post(
        owner,
        ws,
        "/spends",
        spend(currency="USD", amount="20.00", base_amount="15.87", category="software"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["base_amount_minor"] == 1587
    assert (await get(owner, ws, "/pnl")).json()["total"]["other_spend_minor"] == -1587


@pytest.mark.parametrize(
    ("content", "status"),
    [
        (b"", 422),
        (b"GIF89a" + b"\x00" * 10, 415),
        (b"<html>not a receipt</html>", 415),
    ],
)
async def test_receipt_upload_rejects_bad_files(
    client: httpx.AsyncClient, content: bytes, status: int
) -> None:
    owner, ws = await owner_ws(client)
    r = await owner.client.post(
        "/api/v1/receipts",
        files={"file": ("x.png", content, "image/png")},
        headers=owner.headers(ws),
    )
    assert r.status_code == status, r.text


async def test_receipt_upload_size_limit(client: httpx.AsyncClient) -> None:
    owner, ws = await owner_ws(client)
    big = PNG + b"\x00" * get_settings().receipt_max_bytes
    r = await owner.client.post(
        "/api/v1/receipts", files={"file": ("big.png", big, "image/png")}, headers=owner.headers(ws)
    )
    assert r.status_code == 413


async def test_ocr_fails_over_to_secondary(client: httpx.AsyncClient) -> None:
    """AC-34 (OCR): a primary outage falls back to the secondary automatically."""
    owner, ws = await owner_ws(client)
    content = receipt_bytes(vendor="Tesco", total="£5.40") + b"MOCK-OCR-FAIL:claude"
    r = await owner.client.post(
        "/api/v1/receipts",
        files={"file": ("r.png", content, "image/png")},
        headers=owner.headers(ws),
    )
    detail = (await get(owner, ws, f"/receipts/{r.json()['id']}")).json()
    assert detail["ocr_status"] == "done"
    assert detail["ocr_provider"] == "textract"
    assert detail["ocr"]["vendor"] == "Tesco"


async def test_expired_or_forged_download_link(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/files/not-a-token")
    assert r.status_code == 404
    from momentsales.core.security import sign

    forged = sign({"k": f"{uuid.uuid4()}/../../etc/passwd", "w": str(uuid.uuid4())}, ttl_seconds=60)
    assert (await client.get(f"/api/v1/files/{forged}")).status_code == 404
