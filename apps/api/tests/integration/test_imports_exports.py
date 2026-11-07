"""CSV imports (FR-13 ad spend, J7 sales) and the audit pack (AC-10)."""

from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import date

import httpx
import pytest
from helpers import Session, add_member, create_workspace, sign_in

from momentsales.modules.exports.service import PdfUnavailable, render_pdf

API = "/api/v1"
TODAY = date.today()  # noqa: DTZ011


def _pdf_available() -> bool:
    try:
        render_pdf("<p>x</p>")
    except PdfUnavailable:
        return False
    return True


PDF = _pdf_available()


async def shop(client: httpx.AsyncClient) -> tuple[Session, str]:
    owner = await sign_in(client)
    return owner, await create_workspace(owner, "Import Shop")


async def upload(s: Session, ws: str, path: str, content: bytes) -> httpx.Response:
    return await s.client.post(
        f"{API}{path}", files={"file": ("export.csv", content, "text/csv")}, headers=s.headers(ws)
    )


ADS_CSV = (
    "Day,Campaign name,Amount spent (GBP),Product\n"
    f"{TODAY.isoformat()},Hoodie launch,12.40,hoodie\n"
    f"{TODAY.strftime('%d/%m/%Y')},Hoodie retarget,7.60,hoodie\n"
    "not a date,Broken row,1.00,hoodie\n"
).encode()


async def test_ad_spend_csv_import_maps_columns_and_reports_bad_rows(
    client: httpx.AsyncClient,
) -> None:
    owner, ws = await shop(client)
    r = await upload(owner, ws, "/spends/import-csv", ADS_CSV)
    assert r.status_code == 201, r.text
    preview = r.json()
    assert preview["rows_total"] == 3
    assert preview["suggested_mapping"]["date"] == "Day"
    assert preview["suggested_mapping"]["amount"] == "Amount spent (GBP)"
    assert preview["suggested_mapping"]["product_key"] == "Product"
    r = await client.post(
        f"{API}/imports/{preview['import_id']}/confirm",
        json={
            "mapping": preview["suggested_mapping"],
            "defaults": {"category": "ads", "vendor": "Meta Ads"},
        },
        headers=owner.headers(ws),
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert (result["rows_imported"], result["rows_skipped"]) == (2, 1)
    assert result["errors"][0]["row"] == 4
    pnl = (await client.get(f"{API}/pnl?group_by=product_key", headers=owner.headers(ws))).json()
    assert pnl["total"]["ads_minor"] == -2000
    spends = (await client.get(f"{API}/spends", headers=owner.headers(ws))).json()["items"]
    assert {s["source"] for s in spends} == {"csv_import"}
    assert {s["note"] for s in spends} == {"Hoodie launch", "Hoodie retarget"}

    # The same file can't be imported twice
    r = await upload(owner, ws, "/spends/import-csv", ADS_CSV)
    assert r.status_code == 409
    # ...and an import can't be confirmed twice
    r = await client.post(
        f"{API}/imports/{preview['import_id']}/confirm",
        json={"mapping": preview["suggested_mapping"], "defaults": {"category": "ads"}},
        headers=owner.headers(ws),
    )
    assert r.status_code == 409


async def test_ads_rows_need_a_product(client: httpx.AsyncClient) -> None:
    owner, ws = await shop(client)
    content = f"Date,Amount\n{TODAY.isoformat()},5.00\n".encode()
    preview = (await upload(owner, ws, "/spends/import-csv", content)).json()
    r = await client.post(
        f"{API}/imports/{preview['import_id']}/confirm",
        json={
            "mapping": {"date": "Date", "amount": "Amount"},
            "defaults": {"category": "ads", "vendor": "TikTok"},
        },
        headers=owner.headers(ws),
    )
    assert r.json()["rows_imported"] == 0
    assert "product key" in r.json()["errors"][0]["error"]


async def test_sales_csv_import(client: httpx.AsyncClient) -> None:
    owner, ws = await shop(client)
    content = (
        "Order date,Item,Qty,Price,SKU,Marketplace\n"
        f"{TODAY.isoformat()},Candle,2,9.50,candle,Etsy\n"
        f"{TODAY.isoformat()},Mug,1,12.00,mug,Etsy\n"
    ).encode()
    preview = (await upload(owner, ws, "/sales/import-csv", content)).json()
    mapping = {
        "date": "Order date",
        "description": "Item",
        "quantity": "Qty",
        "amount": "Price",
        "product_key": "SKU",
        "channel": "Marketplace",
    }
    r = await client.post(
        f"{API}/imports/{preview['import_id']}/confirm",
        json={"mapping": mapping},
        headers=owner.headers(ws),
    )
    assert r.json()["rows_imported"] == 2, r.text
    orders = (await client.get(f"{API}/orders?channel=csv", headers=owner.headers(ws))).json()[
        "items"
    ]
    assert sorted(o["gross_minor"] for o in orders) == [1200, 1900]
    assert all(o["note"].startswith("Etsy") for o in orders)


@pytest.mark.parametrize(
    ("content", "status"),
    [(b"", 422), (b"\n\n", 422), (b"a,b\n" + b"1,2\n" * 5001, 422)],
)
async def test_bad_csv_files(client: httpx.AsyncClient, content: bytes, status: int) -> None:
    owner, ws = await shop(client)
    assert (await upload(owner, ws, "/spends/import-csv", content)).status_code == status


async def test_analyst_cannot_import(client: httpx.AsyncClient) -> None:
    owner, ws = await shop(client)
    analyst = await add_member(owner, ws, "analyst")
    assert (await upload(analyst, ws, "/spends/import-csv", ADS_CSV)).status_code == 403


# --- Audit pack -------------------------------------------------------------------------


@pytest.mark.skipif(
    not PDF and not os.environ.get("CI"), reason="WeasyPrint needs Pango (Docker/CI)"
)
async def test_ac10_audit_pack(client: httpx.AsyncClient) -> None:
    owner, ws = await shop(client)
    h = owner.headers(ws)
    await client.post(
        f"{API}/sales",
        json={
            "sold_on": TODAY.isoformat(),
            "channel": "Stall",
            "items": [{"description": "Hoodie", "unit_amount": "30.00", "product_key": "hoodie"}],
        },
        headers=h,
    )
    spend = (
        await client.post(
            f"{API}/spends",
            json={
                "amount": "24.99",
                "spent_on": TODAY.isoformat(),
                "vendor": "Royal Mail",
                "category": "shipping",
                "payment_method": "card",
            },
            headers=h,
        )
    ).json()
    receipt_content = b"\x89PNG\r\n\x1a\n" + b"\x01" * 40
    receipt = (
        await client.post(
            f"{API}/receipts", files={"file": ("r.png", receipt_content, "image/png")}, headers=h
        )
    ).json()
    await client.post(
        f"{API}/receipts/{receipt['id']}/confirm", json={"spend_id": spend["id"]}, headers=h
    )
    await client.post(
        f"{API}/touches",
        json={"category": "pricing", "minutes": 10, "description": "adjusted margin"},
        headers=h,
    )

    start = TODAY.replace(day=1).isoformat()
    r = await client.post(
        f"{API}/exports", json={"period_start": start, "period_end": TODAY.isoformat()}, headers=h
    )
    assert r.status_code == 202, r.text
    export = (await client.get(f"{API}/exports/{r.json()['id']}", headers=h)).json()
    assert export["status"] == "completed", export
    assert export["audit_chain_ok"] is True
    files = {f["name"]: f for f in export["files"]}
    assert set(files) == {
        "ledger.csv",
        "spends.csv",
        "touches.csv",
        "orders.csv",
        "receipts.zip",
        "audit-log.jsonl",
        "summary.pdf",
        "manifest.json",
    }

    async def download(name: str) -> bytes:
        response = await client.get(files[name]["url"])
        assert response.status_code == 200, name
        return response.content

    import hashlib

    for name in files:
        assert hashlib.sha256(await download(name)).hexdigest() == files[name]["sha256"], name
    ledger_csv = (await download("ledger.csv")).decode()
    assert "sale,30.00,GBP" in ledger_csv and "spend,-24.99,GBP" in ledger_csv
    assert (await download("summary.pdf")).startswith(b"%PDF")
    with zipfile.ZipFile(io.BytesIO(await download("receipts.zip"))) as zf:
        manifest = json.loads(zf.read("receipts-manifest.json"))
        assert manifest[0]["sha256"] == hashlib.sha256(receipt_content).hexdigest()
        assert manifest[0]["hash_matches"] is True
        assert zf.read(manifest[0]["file"]) == receipt_content
    header, *entries = (await download("audit-log.jsonl")).decode().strip().split("\n")
    assert json.loads(header)["chain_verified"] is True
    actions = {json.loads(line)["action"] for line in entries}
    assert {"spend.created", "receipt.linked", "sale.recorded", "touch.recorded"} <= actions
    assert "adjusted margin" in (await download("touches.csv")).decode()


async def test_export_permissions_and_validation(client: httpx.AsyncClient) -> None:
    owner, ws = await shop(client)
    admin = await add_member(owner, ws, "admin")
    accountant = await add_member(owner, ws, "accountant")
    body = {"period_start": TODAY.isoformat(), "period_end": TODAY.isoformat()}
    assert (
        await client.post(f"{API}/exports", json=body, headers=admin.headers(ws))
    ).status_code == 403
    assert (
        await client.post(f"{API}/exports", json=body, headers=accountant.headers(ws))
    ).status_code == 202
    bad = {"period_start": TODAY.isoformat(), "period_end": "2020-01-01"}
    assert (
        await client.post(f"{API}/exports", json=bad, headers=owner.headers(ws))
    ).status_code == 422
    # The accountant reads the ledger but can't change it
    assert (await client.get(f"{API}/ledger", headers=accountant.headers(ws))).status_code == 200
    r = await client.post(
        f"{API}/spends",
        json={
            "amount": "1.00",
            "spent_on": TODAY.isoformat(),
            "vendor": "x",
            "category": "other",
            "payment_method": "card",
        },
        headers=accountant.headers(ws),
    )
    assert r.status_code == 403
