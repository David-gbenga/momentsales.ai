"use client";

import { formatMoney } from "@momentsales/shared";
import { useTranslations } from "next-intl";

import { Badge, Notice } from "@/components/ui/primitives";
import { DataTable, Td } from "@/components/ui/table";
import { demandOf, formatDay, formatGrowth, isStale, measured, type Candidate, type Run } from "@/lib/research";

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
      {!demand.trend_available ? (
        <Notice>{t("unavailable")}</Notice>
      ) : (
        <DataTable
          caption={t("caption")}
          head={[t("search"), t("growth"), t("competition"), t("prices"), t("source")]}
        >
          {rows.map((c) => (
            <Row key={c.keyword} c={c} />
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

function Row({ c }: { c: Candidate }) {
  const t = useTranslations("research.demand");
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
          t("notAvailable")
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
          t("notAvailable")
        )}
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
