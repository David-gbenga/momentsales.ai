"use client";

import { formatMoney } from "@momentsales/shared";
import { useTranslations } from "next-intl";

import { Badge, Notice } from "@/components/ui/primitives";
import { DataTable, Td } from "@/components/ui/table";
import {
  demandOf,
  failedChecks,
  formatBps,
  formatDay,
  formatDelivery,
  formatGrowth,
  isStale,
  measured,
  type Candidate,
  type Run,
  type SupplierResult,
} from "@/lib/research";

/** Rising searches (FR-12, ADR-0019): growth, "crowded" badge and competition score.
 * Every number comes from a stored provider response; missing data says "not available". */
export function DemandResults({ run }: { run: Run }) {
  const t = useTranslations("research.demand");
  const demand = demandOf(run);
  if (!demand) return null;
  const rows = measured(demand);
  const unmeasured = demand.candidates.length - rows.length;
  return (
    <section aria-labelledby="demand-title" className="grid gap-3" data-testid="demand-results">
      <div className="grid gap-1">
        <h3 id="demand-title" className="text-base font-semibold">
          {t("title")}
        </h3>
        <p className="text-sm text-muted-foreground">{t("intro")}</p>
      </div>
      {demand.mock ? <Notice>{t("mock")}</Notice> : null}
      {notConnected(demand.candidates) ? <Notice>{t("sup.notConnected")}</Notice> : null}
      {!demand.trend_available ? (
        <Notice>{t("unavailable")}</Notice>
      ) : (
        <DataTable
          caption={t("caption")}
          head={[t("search"), t("growth"), t("competition"), t("prices"), t("supplier"), t("source")]}
        >
          {rows.map((c) => (
            <Row key={c.keyword} c={c} marketChecked={demand.market_available} />
          ))}
        </DataTable>
      )}
      {unmeasured > 0 && demand.trend_available ? (
        <p className="text-sm text-muted-foreground">{t("unmeasured", { count: unmeasured })}</p>
      ) : null}
      <p className="text-xs text-muted-foreground">{t("method")}</p>
    </section>
  );
}

function Row({ c, marketChecked }: { c: Candidate; marketChecked: boolean }) {
  const t = useTranslations("research.demand");
  // Competition is checked for the fastest-growing searches only; a provider outage
  // shows "not available" instead.
  const noMarket = marketChecked ? t("notChecked") : t("notAvailable");
  const trend = c.trend!;
  const market = c.market;
  const growth = formatGrowth(trend.growth_rate);
  const fetched = market?.fetched_at ?? trend.fetched_at;
  return (
    <tr data-testid="demand-row" data-keyword={c.keyword}>
      <Td>
        <span className="font-medium">{c.keyword}</span>
        <span className="mt-1 flex flex-wrap gap-1">
          {trend.peak ? (
            <Badge className="border-amber-600 text-amber-800 dark:text-amber-300" data-testid="badge-crowded">
              {t("crowded")}
            </Badge>
          ) : null}
          {c.source === "rising" ? <Badge>{t("risingSearch")}</Badge> : null}
        </span>
      </Td>
      <Td>{growth ?? t("notAvailable")}</Td>
      <Td>
        {market?.competition_score !== null && market?.competition_score !== undefined ? (
          <span>
            {t("score", { score: market.competition_score })}
            <span className="block text-xs text-muted-foreground">
              {t("ads", { ads: market.paid_ads + market.shopping_ads, sellers: market.merchants })}
            </span>
          </span>
        ) : (
          noMarket
        )}
      </Td>
      <Td>
        {market?.price_min_minor !== null && market?.price_min_minor !== undefined && market.currency ? (
          market.price_min_minor === market.price_max_minor ? (
            formatMoney(market.price_min_minor, market.currency)
          ) : (
            `${formatMoney(market.price_min_minor, market.currency)}–${formatMoney(market.price_max_minor ?? market.price_min_minor, market.currency)}`
          )
        ) : (
          noMarket
        )}
      </Td>
      <Td>
        <SupplierCell supplier={c.supplier ?? null} />
      </Td>
      <Td>
        <span className="text-xs">
          {t("from", { provider: market?.provider ?? trend.provider, when: formatDay(fetched.slice(0, 10)) })}
        </span>
        {isStale(fetched) ? (
          <Badge className="ml-1" data-testid="badge-stale">
            {t("stale")}
          </Badge>
        ) : null}
      </Td>
    </tr>
  );
}

function notConnected(candidates: Candidate[]): boolean {
  return candidates.some((c) => c.supplier?.reasons.includes("supplier_not_connected"));
}

/** Supplier matching (FR-11): landed cost, delivery and warehouse, or why there is none. */
function SupplierCell({ supplier }: { supplier: SupplierResult | null }) {
  const t = useTranslations("research.demand.sup");
  if (!supplier) return <span>{t("not_checked")}</span>;
  const m = supplier.match;
  const failed = failedChecks(supplier);
  const late = supplier.arrives_too_late === true;
  const delivery = m ? formatDelivery(m.delivery_min_days, m.delivery_max_days) : null;
  return (
    <span className="grid gap-1" data-testid="supplier-cell" data-status={supplier.status}>
      <span className="flex flex-wrap gap-1">
        <Badge
          data-testid={`badge-supplier-${supplier.status}`}
          className={supplier.status === "matched" ? "border-emerald-600 text-emerald-800 dark:text-emerald-300" : undefined}
        >
          {t(supplier.status)}
        </Badge>
        {late ? (
          <Badge className="border-amber-600 text-amber-800 dark:text-amber-300" data-testid="badge-too-late">
            {t("tooLate")}
          </Badge>
        ) : null}
      </span>
      {m ? (
        <span className="text-xs text-muted-foreground">
          <span className="block font-medium text-foreground">
            {m.provider.toUpperCase()}
            {m.landed_cost_minor !== null ? ` · ${t("landed", { cost: formatMoney(m.landed_cost_minor, "GBP") })}` : ""}
          </span>
          <span className="block">
            {delivery ? t("delivery", { days: delivery }) : t("noDelivery")}
            {m.warehouse_country ? ` · ${t("warehouse", { country: m.warehouse_country })}` : ""}
          </span>
          <span className="block">
            {m.margin.result === "not_checked"
              ? t("marginNotChecked")
              : t("margin", { pct: formatBps(m.margin.margin_bps) ?? "?" })}
          </span>
          {m.fx ? <span className="block">{t("fx", { rate: m.fx.rate, date: formatDay(m.fx.rate_date) })}</span> : null}
        </span>
      ) : null}
      {failed.length && supplier.status !== "matched" ? (
        <span className="text-xs text-muted-foreground">
          {t("why", { checks: failed.map((f) => (t.has(`reasons.${f}`) ? t(`reasons.${f}`) : f)).join(", ") })}
        </span>
      ) : null}
    </span>
  );
}
