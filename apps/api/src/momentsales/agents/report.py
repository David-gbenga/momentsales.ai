"""Report agent (FR-1, FR-4, ADR-0021): writes the Top 10 report.

Picks the top 10 ranked searches that compliance did not block, asks Claude for one-line
reasons built from stored facts (each checked by `why_is_grounded`, with a template line as
the fallback), and stores an immutable `Report` with its `ReportItem`s. Prices, links,
delivery and supplier details keep their snapshot ids (AC-3). Runs after the cost cap too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from momentsales.adapters.llm.base import LlmError
from momentsales.agents.base import AgentContext, AgentResult, BudgetExceededError
from momentsales.agents.llm_calls import call_llm
from momentsales.db.research import Report, ReportItem, SourceSnapshot
from momentsales.modules.providers.registry import ProviderConfigError
from momentsales.modules.research import report_build as rb
from momentsales.modules.research.prompts import REASONS_SCHEMA, REASONS_SYSTEM

MAX_SOURCES = 200


class ReportAgent:
    name: str = "report"
    tools: tuple[str, ...] = ("llm",)
    timeout_seconds: float = 180.0
    runs_after_cap: bool = True

    async def run(self, ctx: AgentContext) -> AgentResult:
        run = ctx.run
        demand = dict(run.demand or {})
        existing = await ctx.session.scalar(select(Report.id).where(Report.run_id == run.id))
        if existing is not None:  # resumed after the report was written
            return AgentResult(
                output={"report_id": str(existing), "items": None}, summary="Report already written"
            )
        candidates: list[dict[str, Any]] = [dict(c) for c in demand.get("candidates") or []]
        top, blocked = rb.pick_top(
            candidates, list(demand.get("ranking") or []), ctx.settings.research_report_size
        )
        moments = list((run.season or {}).get("moments") or [])
        weeks = ctx.settings.research_growth_recent_weeks
        facts = {c["keyword"]: rb.facts_for(c, rb.moment_for(c, moments), weeks) for c in top}

        whys: dict[str, str] = {}
        llm_note: str | None = None
        if top:
            try:
                llm = await ctx.registry.llm(ctx.session)
                result = await call_llm(
                    ctx,
                    llm,
                    task="reasons",
                    system=REASONS_SYSTEM,
                    payload={
                        "items": [
                            {"id": str(i), "facts": facts[c["keyword"]]} for i, c in enumerate(top)
                        ]
                    },
                    schema=REASONS_SCHEMA,
                    effort="low",
                    max_tokens=4000,
                )
                for item in result.data.get("items") or []:
                    raw_id = str(item.get("id", ""))
                    if raw_id.isdigit() and int(raw_id) < len(top):
                        whys[top[int(raw_id)]["keyword"]] = str(item.get("why") or "")
            except BudgetExceededError:
                llm_note = "cost_cap"
            except (LlmError, ProviderConfigError) as exc:
                llm_note = f"llm_error: {str(exc)[:160]}"

        snapshots = (
            await ctx.session.execute(
                select(
                    SourceSnapshot.provider,
                    SourceSnapshot.kind,
                    SourceSnapshot.url,
                    SourceSnapshot.fetched_at,
                )
                .where(SourceSnapshot.run_id == run.id)
                .order_by(SourceSnapshot.fetched_at)
                .limit(MAX_SOURCES)
            )
        ).all()
        sources = [
            {"provider": p, "kind": k, "url": u, "fetched_at": f.isoformat()}
            for p, k, u, f in snapshots
        ]
        report = Report(
            workspace_id=run.workspace_id,
            run_id=run.id,
            summary={
                **rb.summary_for(run, demand, sources, ctx.meter.spent_minor, ctx.as_of),
                "item_count": len(top),
                "why_source": "template" if llm_note else "llm",
            },
            excluded=[
                {
                    "keyword": c["keyword"],
                    "reason": "blocked",
                    "flags": [f["flag"] for f in (c.get("compliance") or {}).get("flags", [])],
                    "notes": [f.get("note") for f in (c.get("compliance") or {}).get("flags", [])],
                }
                for c in blocked
            ],
            generated_at=datetime.now(UTC),
        )
        ctx.session.add(report)
        await ctx.session.flush()
        llm_used = 0
        for rank, c in enumerate(top, start=1):
            kw = c["keyword"]
            why = whys.get(kw, "")
            source = "llm"
            if not rb.why_is_grounded(why, facts[kw], kw):
                why, source = rb.template_why(facts[kw]), "template"
            else:
                llm_used += 1
            offers, best = rb.offers_for(c)
            supplier = c.get("supplier")
            ctx.session.add(
                ReportItem(
                    workspace_id=run.workspace_id,
                    report_id=report.id,
                    rank=rank,
                    keyword=kw,
                    category=c.get("category"),
                    score=int(c["score"]["score"]),
                    score_breakdown=c["score"],
                    why=why,
                    why_source=source,
                    image_url=((supplier or {}).get("match") or {}).get("image_url"),
                    best_offer=best,
                    offers=offers,
                    sizes=rb.sizes_for(c),
                    trend=c.get("trend"),
                    market=c.get("market"),
                    supplier=supplier,
                    profit=c.get("profit"),
                    compliance=c.get("compliance")
                    or {"status": "not_checked", "flags": [], "required_documents": []},
                    flags=rb.flags_for(c),
                    fetched_at=rb.oldest_fetch(c),
                )
            )
        await ctx.session.flush()
        demand["report_id"] = str(report.id)
        run.demand = demand
        text = f"Top {len(top)} report written"
        if blocked:
            text += f"; {len(blocked)} excluded by compliance"
        if llm_note:
            text += " (reasons from templates: AI not available)"
        return AgentResult(
            output={
                "report_id": str(report.id),
                "items": len(top),
                "excluded": len(blocked),
                "llm_reasons": llm_used,
                "llm": llm_note or "ok",
            },
            summary=text[:500],
            degraded=bool(llm_note) or len(top) < ctx.settings.research_report_size,
        )
