"""Compliance agent (FR-1, FR-14, ADR-0021): UK product checks for the top-scored searches.

Flags come from the versioned UK rules list plus one batched Claude classification; the LLM
can add flags but never clear a rule's flag. "blocked" products are vetoed from the report.
If the LLM is unavailable or the cost cap is reached, the rules alone decide and the step is
degraded. Runs after the cost cap too.
"""

from __future__ import annotations

from typing import Any

from momentsales.adapters.llm.base import LlmError
from momentsales.agents.base import AgentContext, AgentResult, BudgetExceededError
from momentsales.agents.llm_calls import call_llm
from momentsales.modules.providers.registry import ProviderConfigError
from momentsales.modules.research import compliance_rules
from momentsales.modules.research.prompts import COMPLIANCE_SCHEMA, COMPLIANCE_SYSTEM


def _texts(c: dict[str, Any]) -> list[str]:
    match = (c.get("supplier") or {}).get("match") or {}
    return [c["keyword"], c.get("category") or "", match.get("title") or ""]


class ComplianceAgent:
    name: str = "compliance"
    tools: tuple[str, ...] = ("llm",)
    timeout_seconds: float = 180.0
    runs_after_cap: bool = True

    async def run(self, ctx: AgentContext) -> AgentResult:
        run = ctx.run
        demand = dict(run.demand or {})
        candidates: list[dict[str, Any]] = [dict(c) for c in demand.get("candidates") or []]
        by_keyword = {c["keyword"]: c for c in candidates}
        ranking = [k for k in demand.get("ranking") or [] if k in by_keyword]
        top = [by_keyword[k] for k in ranking[: ctx.settings.research_compliance_top_n]]
        if not top:
            return AgentResult(
                output={"checked": 0}, summary="No ranked searches to check", degraded=True
            )

        llm_answers: dict[str, dict[str, Any]] = {}
        llm_note: str | None = None
        try:
            llm = await ctx.registry.llm(ctx.session)
            result = await call_llm(
                ctx,
                llm,
                task="compliance",
                system=COMPLIANCE_SYSTEM,
                payload={
                    "products": [
                        {
                            "id": str(i),
                            "name": c["keyword"],
                            "category": c.get("category") or "",
                            "supplier_title": ((c.get("supplier") or {}).get("match") or {}).get(
                                "title"
                            )
                            or "",
                        }
                        for i, c in enumerate(top)
                    ]
                },
                schema=COMPLIANCE_SCHEMA,
                effort="medium",
                max_tokens=8000,
            )
            for p in result.data.get("products") or []:
                index = int(p.get("id", -1)) if str(p.get("id", "")).isdigit() else -1
                if 0 <= index < len(top):
                    llm_answers[top[index]["keyword"]] = p
        except BudgetExceededError:
            llm_note = "cost_cap"
        except (LlmError, ProviderConfigError) as exc:
            llm_note = f"llm_error: {str(exc)[:160]}"

        counts = {"blocked": 0, "needs_documents": 0, "ok": 0}
        for c in top:
            result_c = compliance_rules.merge(
                compliance_rules.check(_texts(c)), llm_answers.get(c["keyword"])
            )
            match = (c.get("supplier") or {}).get("match") or {}
            warehouse = match.get("warehouse_country")
            result_c["import_vat_notice"] = bool(warehouse and warehouse != run.country)
            result_c["source"] = "rules+llm" if c["keyword"] in llm_answers else "rules"
            c["compliance"] = result_c
            counts[result_c["status"]] += 1

        demand.update(
            candidates=candidates,
            compliance_source="rules" if llm_note else "rules+llm",
        )
        run.demand = demand
        text = (
            f"Checked {len(top)} products: {counts['blocked']} blocked, "
            f"{counts['needs_documents']} need safety documents"
        )
        if llm_note:
            text += " (AI check not available; rules only)"
        return AgentResult(
            output={"checked": len(top), **counts, "llm": llm_note or "ok"},
            summary=text[:500],
            degraded=llm_note is not None,
        )
