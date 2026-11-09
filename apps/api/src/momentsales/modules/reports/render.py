"""CSV and PDF exports of a Top 10 report (FR-4, ADR-0021). The PDF uses WeasyPrint through
`render_pdf` from the audit pack; text is escaped and nothing is fetched remotely."""

from __future__ import annotations

import csv
import io
from html import escape
from typing import Any

from momentsales.db.research import Report, ReportItem
from momentsales.modules.ledger.money import from_minor

CSV_HEADER = [
    "rank",
    "product",
    "category",
    "score",
    "why",
    "best_price",
    "best_price_platform",
    "price_range",
    "purchase_links",
    "sizes",
    "supplier",
    "supplier_status",
    "unit_cost_gbp",
    "shipping_gbp",
    "warehouse",
    "delivery_days",
    "profit_per_sale_gbp",
    "break_even_ad_cost_gbp",
    "growth_pct",
    "competition_score",
    "compliance",
    "flags",
    "data_fetched_at",
]


def money(minor: int | None, currency: str | None = "GBP") -> str:
    if minor is None or not currency:
        return ""
    return f"{from_minor(minor, currency)} {currency}"


def _row(item: ReportItem) -> list[str]:
    market = item.market or {}
    supplier = item.supplier or {}
    match = supplier.get("match") or {}
    profit = item.profit or {}
    trend = item.trend or {}
    best = item.best_offer or {}
    growth = trend.get("growth_rate")
    low, high = market.get("price_min_minor"), market.get("price_max_minor")
    days = (
        f"{match.get('delivery_min_days')}-{match.get('delivery_max_days')}"
        if match.get("delivery_max_days") is not None
        else ""
    )
    return [
        str(item.rank),
        item.keyword,
        item.category or "",
        str(item.score),
        item.why,
        money(best.get("price_minor"), best.get("currency")),
        str(best.get("platform") or ""),
        f"{money(low)} - {money(high)}" if low is not None and high is not None else "",
        " ".join(o["url"] for o in item.offers if o.get("url")),
        "; ".join(item.sizes),
        str(match.get("provider") or ""),
        str(supplier.get("status") or ""),
        money(match.get("unit_cost_gbp_minor")),
        money(match.get("shipping_gbp_minor")),
        str(match.get("warehouse_country") or ""),
        days,
        money(profit.get("profit_minor")),
        money(profit.get("break_even_ad_cost_minor")),
        str(round(float(growth) * 100)) if growth is not None else "",
        str(market.get("competition_score") if market.get("competition_score") is not None else ""),
        str(item.compliance.get("status") or ""),
        " ".join(item.flags),
        item.fetched_at.isoformat() if item.fetched_at else "",
    ]


def to_csv(items: list[ReportItem]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADER)
    for item in items:
        writer.writerow(_row(item))
    return buf.getvalue().encode("utf-8-sig")


def _card(item: ReportItem) -> str:
    row = dict(zip(CSV_HEADER, _row(item), strict=True))
    lines = [
        ("Why", row["why"]),
        ("Best price", f"{row['best_price']} ({row['best_price_platform']})".strip(" ()")),
        ("Price range", row["price_range"]),
        ("Sizes", row["sizes"] or "Not available"),
        (
            "Supplier",
            f"{row['supplier']} {row['supplier_status']} - {row['warehouse']} - "
            f"{row['delivery_days']} days",
        ),
        ("Landed cost", f"{row['unit_cost_gbp']} + {row['shipping_gbp']} shipping"),
        ("Profit per sale (estimate)", row["profit_per_sale_gbp"] or "Not available"),
        ("Break-even ad cost", row["break_even_ad_cost_gbp"] or "Not available"),
        ("Growth / competition", f"{row['growth_pct']}% / {row['competition_score']}/100"),
        ("Compliance", row["compliance"]),
        ("Flags", row["flags"] or "None"),
    ]
    body = "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in lines)
    return (
        f"<section><h2>{item.rank}. {escape(item.keyword)} "
        f"<span class=score>{item.score}/100</span></h2><table>{body}</table></section>"
    )


def to_html(report: Report, items: list[ReportItem]) -> str:
    s: dict[str, Any] = report.summary
    season = s.get("season") or {}
    moments = ", ".join(f"{m.get('name')} ({m.get('date')})" for m in season.get("moments") or [])
    cost = money(s.get("cost_minor"), s.get("cost_currency"))
    excluded = "".join(
        f"<li>{escape(e['keyword'])}: {escape(', '.join(e.get('flags') or []))}</li>"
        for e in report.excluded
    )
    mock = "<p class=mock>Mock data: generated for testing.</p>" if s.get("mock") else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body {{ font-family: DejaVu Sans, sans-serif; font-size: 10pt; color: #111; }}
h1 {{ font-size: 16pt; }} h2 {{ font-size: 12pt; margin: 14pt 0 4pt; }}
.score {{ color: #555; font-weight: normal; }} table {{ border-collapse: collapse; width: 100%; }}
th {{ text-align: left; width: 32%; color: #444; font-weight: normal; }}
td, th {{ padding: 2pt 4pt; vertical-align: top; border-bottom: 0.5pt solid #ddd; }}
.mock {{ color: #8a4b00; }} .small {{ font-size: 8pt; color: #555; }}
</style></head><body>
<h1>Top {len(items)} seasonal products - {escape(str(s.get("params", {}).get("country", "")))}</h1>
{mock}
<p>As of {escape(str(s.get("as_of")))}. Season: {escape(str(season.get("current")))}, next
{escape(str(season.get("next")))}. Moments: {escape(moments)}. Run cost: {escape(cost)}.</p>
{"".join(_card(i) for i in items)}
{f"<h2>Excluded by compliance</h2><ul>{excluded}</ul>" if excluded else ""}
<h2>Method</h2><p class=small>{escape(str(s.get("method") or ""))}</p>
<p class=small>Sources: {len(s.get("sources") or [])} stored provider responses.</p>
</body></html>"""
