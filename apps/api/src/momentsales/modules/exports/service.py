"""Audit pack (FR-8, AC-10): CSV ledger, spends, touches and orders; a PDF summary; the
receipts as a ZIP with their hashes; and an audit-log extract whose hash chain is verified.

A `manifest.json` lists every file with its SHA-256. Built in a job; files go to storage
and are downloaded through 5-minute signed links.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import uuid
import zipfile
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.adapters.storage.base import StoragePort
from momentsales.core.logging import get_logger
from momentsales.db.ledger import (
    Export,
    Integration,
    JobStatus,
    LedgerEntry,
    Order,
    OrderItem,
    Receipt,
    Spend,
    SpendReceipt,
    SpendVoid,
)
from momentsales.db.models import AuditLog, Touch, Workspace
from momentsales.modules.audit.service import verify_chain
from momentsales.modules.ledger import service as ledger
from momentsales.modules.ledger.money import BASE_CURRENCY, from_minor

log = get_logger(__name__)
UK = ledger.UK_TZ


class PdfUnavailable(RuntimeError):
    pass


def render_pdf(document: str) -> bytes:
    """HTML -> PDF with WeasyPrint (needs Pango; installed in the Docker images and CI)."""
    try:
        from weasyprint import HTML  # type: ignore[import-untyped]
    except OSError as exc:  # native libraries missing (e.g. a Windows host without GTK)
        raise PdfUnavailable(str(exc)) from exc
    pdf: bytes = HTML(string=document).write_pdf()
    return pdf


def _csv(header: list[str], rows: list[list[Any]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def _money(minor: int, currency: str = BASE_CURRENCY) -> str:
    return from_minor(minor, currency)


async def _ledger_csv(session: AsyncSession, start: date, end: date) -> bytes:
    rows = (
        await session.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.occurred_on >= start, LedgerEntry.occurred_on <= end)
            .order_by(LedgerEntry.occurred_on, LedgerEntry.id)
        )
    ).all()
    return _csv(
        [
            "date",
            "type",
            "amount",
            "currency",
            "description",
            "product_key",
            "order_id",
            "spend_id",
            "source",
            "source_ref",
            "reverses_id",
            "entry_id",
            "recorded_at",
        ],
        [
            [
                e.occurred_on,
                e.type.value,
                _money(e.amount_minor, e.currency),
                e.currency,
                e.description,
                e.product_key or "",
                e.order_id or "",
                e.spend_id or "",
                e.source,
                e.source_ref,
                e.reverses_id or "",
                e.id,
                e.created_at.isoformat(),
            ]
            for e in rows
        ],
    )


async def _spends(
    session: AsyncSession, start: date, end: date
) -> tuple[bytes, list[Spend], dict[uuid.UUID, uuid.UUID], set[uuid.UUID]]:
    spends = list(
        (
            await session.scalars(
                select(Spend)
                .where(Spend.spent_on >= start, Spend.spent_on <= end)
                .order_by(Spend.spent_on, Spend.id)
            )
        ).all()
    )
    ids = [s.id for s in spends]
    links = (
        dict(
            (
                await session.execute(
                    select(SpendReceipt.spend_id, SpendReceipt.receipt_id).where(
                        SpendReceipt.spend_id.in_(ids)
                    )
                )
            ).all()
        )
        if ids
        else {}
    )
    voids = (
        set(
            (
                await session.scalars(select(SpendVoid.spend_id).where(SpendVoid.spend_id.in_(ids)))
            ).all()
        )
        if ids
        else set()
    )
    content = _csv(
        [
            "date",
            "vendor",
            "category",
            "amount",
            "currency",
            "gbp_amount",
            "vat",
            "payment_method",
            "product_key",
            "receipt_id",
            "missing_receipt",
            "voided",
            "source",
            "note",
            "spend_id",
        ],
        [
            [
                s.spent_on,
                s.vendor,
                s.category.value,
                _money(s.amount_minor, s.currency),
                s.currency,
                _money(s.base_amount_minor),
                _money(s.tax_minor, s.currency),
                s.payment_method,
                s.product_key or "",
                links.get(s.id, ""),
                "no" if s.id in links or s.id in voids else "yes",
                "yes" if s.id in voids else "no",
                s.source.value,
                s.note or "",
                s.id,
            ]
            for s in spends
        ],
    )
    return content, spends, links, voids


async def _touches_csv(session: AsyncSession, start: date, end: date) -> bytes:
    day = cast(func.timezone(UK, Touch.created_at), Date)
    rows = (
        await session.scalars(select(Touch).where(day >= start, day <= end).order_by(Touch.id))
    ).all()
    return _csv(
        [
            "recorded_at",
            "category",
            "description",
            "minutes",
            "automatic",
            "object_type",
            "object_id",
            "actor_user_id",
            "touch_id",
        ],
        [
            [
                t.created_at.isoformat(),
                t.category.value,
                t.description,
                t.minutes,
                "yes" if t.auto else "no",
                t.object_type or "",
                t.object_id or "",
                t.actor_user_id,
                t.id,
            ]
            for t in rows
        ],
    )


async def _orders_csv(session: AsyncSession, start: date, end: date) -> bytes:
    day = cast(func.timezone(UK, Order.paid_at), Date)
    orders = list(
        (
            await session.scalars(
                select(Order).where(day >= start, day <= end).order_by(Order.paid_at)
            )
        ).all()
    )
    items: dict[uuid.UUID, list[OrderItem]] = {o.id: [] for o in orders}
    if orders:
        for item in (
            await session.scalars(select(OrderItem).where(OrderItem.order_id.in_(list(items))))
        ).all():
            items[item.order_id].append(item)
    rows = []
    for o in orders:
        order_items: list[OrderItem | None] = list(items[o.id]) or [None]
        for i in order_items:
            rows.append(
                [
                    o.paid_at.isoformat() if o.paid_at else "",
                    o.channel.value,
                    o.external_id,
                    o.status.value,
                    _money(o.gross_minor, o.currency),
                    _money(o.tax_minor, o.currency),
                    _money(o.fees_minor, o.currency),
                    _money(o.refunded_minor, o.currency),
                    o.currency,
                    i.description if i else "",
                    i.quantity if i else "",
                    _money(i.unit_amount_minor, o.currency) if i else "",
                    (i.product_key or "") if i else "",
                    o.id,
                ]
            )
    return _csv(
        [
            "paid_at",
            "channel",
            "external_id",
            "status",
            "gross",
            "tax",
            "stripe_fees",
            "refunded",
            "currency",
            "item",
            "quantity",
            "unit_price",
            "product_key",
            "order_id",
        ],
        rows,
    )


async def _receipts_zip(
    session: AsyncSession, storage: StoragePort, links: dict[uuid.UUID, uuid.UUID]
) -> tuple[bytes, int]:
    receipt_ids = list(set(links.values()))
    receipts = (
        list((await session.scalars(select(Receipt).where(Receipt.id.in_(receipt_ids)))).all())
        if receipt_ids
        else []
    )
    buffer = io.BytesIO()
    manifest = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in receipts:
            content = await storage.get(r.file_key)
            actual = hashlib.sha256(content).hexdigest()
            ext = r.file_key.rsplit(".", 1)[-1]
            name = f"{r.id}.{ext}"
            zf.writestr(name, content)
            manifest.append(
                {
                    "file": name,
                    "original_name": r.original_name,
                    "sha256": r.sha256,
                    "hash_matches": actual == r.sha256,
                    "uploaded_at": r.created_at.isoformat(),
                }
            )
        zf.writestr("receipts-manifest.json", json.dumps(manifest, indent=2))
    return buffer.getvalue(), len(receipts)


async def _audit_extract(
    session: AsyncSession, workspace_id: uuid.UUID, start: date, end: date
) -> tuple[bytes, bool, int]:
    verification = await verify_chain(session, workspace_id)
    day = cast(func.timezone(UK, AuditLog.occurred_at), Date)
    entries = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.workspace_id == workspace_id, day >= start, day <= end)
            .order_by(AuditLog.seq)
        )
    ).all()
    lines = [
        json.dumps(
            {
                "seq": e.seq,
                "occurred_at": e.occurred_at.isoformat(),
                "action": e.action,
                "object_type": e.object_type,
                "object_id": e.object_id,
                "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None,
                "before": e.before,
                "after": e.after,
                "prev_hash": e.prev_hash,
                "hash": e.hash,
            },
            sort_keys=True,
            default=str,
        )
        for e in entries
    ]
    header = json.dumps(
        {
            "chain_verified": verification.ok,
            "chain_entries": verification.entries,
            "first_bad_seq": verification.first_bad_seq,
            "period_entries": len(lines),
        }
    )
    return ("\n".join([header, *lines]) + "\n").encode(), verification.ok, len(lines)


def _summary_html(
    workspace: Workspace,
    integration: Integration | None,
    start: date,
    end: date,
    total: ledger.PnlLine,
    lines: list[ledger.PnlLine],
    spends: list[Spend],
    links: dict[uuid.UUID, uuid.UUID],
    voided: set[uuid.UUID],
    chain_ok: bool,
    touches: dict[str, int],
) -> str:
    e = html.escape
    missing = sum(1 for s in spends if s.id not in links and s.id not in voided)
    rows = "".join(
        f"<tr><td>{e(line.product_key or 'Not linked to a product')}</td>"
        f"<td>{_money(line.revenue)}</td><td>{_money(line.fees + line.refunds)}</td>"
        f"<td>{_money(line.cogs + line.ads + line.other_spend)}</td><td>{_money(line.profit)}</td></tr>"
        for line in lines
    )
    tax_ids = (
        ", ".join(f"{e(k)}: {e(str(v))}" for k, v in (workspace.tax_ids or {}).items())
        or "Not provided"
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body {{ font-family: sans-serif; font-size: 11pt; color: #111; }}
h1 {{ font-size: 18pt; }} table {{ border-collapse: collapse; width: 100%; margin: 8pt 0; }}
td, th {{ border: 1px solid #999; padding: 4pt; text-align: left; }} .muted {{ color: #555; }}
</style></head><body>
<h1>Audit pack: {e(workspace.legal_name or workspace.name)}</h1>
<p>Period {start.isoformat()} to {end.isoformat()} (UK dates). Amounts in {BASE_CURRENCY}.
Generated {datetime.now(UTC).strftime("%Y-%m-%d %H:%M")} UTC by momentsales.ai.</p>
<table>
<tr><th>Business</th><td>{e(workspace.legal_name or workspace.name)}</td></tr>
<tr><th>Country</th><td>{e(workspace.country)}</td></tr>
<tr><th>Tax registrations</th><td>{tax_ids}</td></tr>
<tr><th>Stripe account</th><td>{e(integration.external_id) if integration else "Not connected"}</td></tr>
</table>
<h2>Profit and loss</h2>
<table>
<tr><th>Revenue</th><td>{_money(total.revenue)}</td></tr>
<tr><th>Stripe fees</th><td>{_money(total.fees)}</td></tr>
<tr><th>Refunds</th><td>{_money(total.refunds)}</td></tr>
<tr><th>Cost of goods (stock, shipping)</th><td>{_money(total.cogs)}</td></tr>
<tr><th>Ad spend</th><td>{_money(total.ads)}</td></tr>
<tr><th>Other spend</th><td>{_money(total.other_spend)}</td></tr>
<tr><th>Adjustments (disputes)</th><td>{_money(total.adjustments)}</td></tr>
<tr><th>Profit</th><td><b>{_money(total.profit)}</b></td></tr>
</table>
<h2>By product</h2>
<table><tr><th>Product</th><th>Revenue</th><th>Fees and refunds</th><th>Costs</th><th>Profit</th></tr>{rows}</table>
<h2>Records</h2>
<table>
<tr><th>Spends in period</th><td>{len(spends)}</td></tr>
<tr><th>Spends missing a receipt</th><td>{missing}</td></tr>
<tr><th>Human interventions (/touch)</th><td>{touches["manual_count"]} ({touches["manual_minutes"]} minutes)</td></tr>
<tr><th>Automatic touches</th><td>{touches["auto_count"]}</td></tr>
<tr><th>Audit log hash chain</th><td>{"Verified" if chain_ok else "FAILED verification"}</td></tr>
</table>
<p class="muted">Payouts are cash movements to the bank and are listed in ledger.csv but not counted in profit.
This summary is generated from the ledger; it is not tax advice.</p>
</body></html>"""


async def build(session: AsyncSession, storage: StoragePort, export: Export) -> None:
    """Fill in an export. Idempotent: does nothing once the export completed."""
    if export.status is JobStatus.COMPLETED:
        return
    export.status = JobStatus.RUNNING
    ws = export.workspace_id
    start, end = export.period_start, export.period_end
    workspace = await session.get(Workspace, ws)
    assert workspace is not None
    files: dict[str, tuple[bytes, str]] = {}
    files["ledger.csv"] = (await _ledger_csv(session, start, end), "text/csv")
    spends_csv, spends, links, voided = await _spends(session, start, end)
    files["spends.csv"] = (spends_csv, "text/csv")
    files["touches.csv"] = (await _touches_csv(session, start, end), "text/csv")
    files["orders.csv"] = (await _orders_csv(session, start, end), "text/csv")
    receipts_zip, receipt_count = await _receipts_zip(session, storage, links)
    files["receipts.zip"] = (receipts_zip, "application/zip")
    audit_jsonl, chain_ok, audit_count = await _audit_extract(session, ws, start, end)
    files["audit-log.jsonl"] = (audit_jsonl, "application/x-ndjson")
    total, lines = await ledger.pnl(session, start, end, by_product=True)
    touches = await ledger.touch_summary(session, start, end)
    integration = await session.scalar(select(Integration))
    files["summary.pdf"] = (
        render_pdf(
            _summary_html(
                workspace,
                integration,
                start,
                end,
                total,
                lines,
                spends,
                links,
                voided,
                chain_ok,
                touches,
            )
        ),
        "application/pdf",
    )
    # Storage never overwrites, so each attempt writes to its own folder; a retried job
    # after a partial failure doesn't collide with the earlier files.
    folder = f"{ws}/exports/{export.id}/{uuid.uuid4().hex[:12]}"
    manifest = []
    for name, (content, mime) in files.items():
        key = f"{folder}/{name}"
        await storage.put(key, content, mime)
        manifest.append(
            {
                "name": name,
                "key": key,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    manifest_bytes = json.dumps(
        {
            "export_id": str(export.id),
            "workspace_id": str(ws),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "audit_chain_verified": chain_ok,
            "receipts": receipt_count,
            "audit_entries": audit_count,
            "files": [{k: v for k, v in m.items() if k != "key"} for m in manifest],
        },
        indent=2,
    ).encode()
    manifest_key = f"{folder}/manifest.json"
    await storage.put(manifest_key, manifest_bytes, "application/json")
    manifest.append(
        {
            "name": "manifest.json",
            "key": manifest_key,
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "bytes": len(manifest_bytes),
        }
    )
    export.files = manifest
    export.audit_chain_ok = chain_ok
    export.status = JobStatus.COMPLETED
    export.completed_at = datetime.now(UTC)
    export.error = None
