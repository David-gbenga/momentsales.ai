"use client";

import { formatMoney } from "@momentsales/shared";
import { useTranslations } from "next-intl";

import { Badge, Card, Notice } from "@/components/ui/primitives";
import { formatDay, isStale } from "@/lib/research";
import {
  bestPrice,
  competitionOf,
  complianceOf,
  growthPct,
  offersOf,
  priceRange,
  profitOf,
  supplierOf,
  type PublicReport,
  type ReportItem,
} from "@/lib/reports";

type Summary = {
  as_of?: string;
  mock?: boolean;
  method?: string;
  sources?: unknown[];
  cost_minor?: number;
  cost_currency?: string;
  season?: { current?: string; next?: string; moments?: { name: string; date: string }[] };
  params?: { country?: string };
};

/** The Top 10 report (FR-4): shared by the app page and the public read-only page.
 * The public view never receives the run cost. */
export function ReportBody({ report }: { report: Pick<PublicReport, "summary" | "excluded" | "items" | "generated_at"> }) {
  const t = useTranslations("reports");
  const s = report.summary as Summary;
  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground" data-testid="report-summary">
        {t("asOf", { date: formatDay(String(s.as_of ?? report.generated_at).slice(0, 10)) })}
        {s.season?.current ? ` · ${t("season", { now: s.season.current, next: s.season.next ?? "" })}` : ""}
        {s.season?.moments?.length ? ` · ${s.season.moments.map((m) => m.name).join(", ")}` : ""}
        {s.cost_minor != null && s.cost_currency ? ` · ${t("cost", { cost: formatMoney(s.cost_minor, s.cost_currency) })}` : ""}
      </p>
      {s.mock ? <Notice>{t("mock")}</Notice> : null}
      {report.items.length < 10 ? <Notice>{t("fewer", { count: report.items.length })}</Notice> : null}
      <ol className="grid gap-3" data-testid="report-items">
        {report.items.map((item) => (
          <li key={item.rank}>
            <ItemCard item={item} />
          </li>
        ))}
      </ol>
      {report.excluded.length ? (
        <section aria-labelledby="excluded-title" className="grid gap-1">
          <h2 id="excluded-title" className="text-base font-semibold">
            {t("excluded")}
          </h2>
          <ul className="text-sm">
            {report.excluded.map((e) => (
              <li key={String(e.keyword)}>
                {String(e.keyword)}: {t("excludedReason")}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <section aria-labelledby="method-title" className="grid gap-1">
        <h2 id="method-title" className="text-base font-semibold">
          {t("method")}
        </h2>
        <p className="text-sm text-muted-foreground">{s.method}</p>
        <p className="text-xs text-muted-foreground">{t("sources", { count: s.sources?.length ?? 0 })}</p>
      </section>
    </div>
  );
}

function ItemCard({ item }: { item: ReportItem }) {
  const t = useTranslations("reports");
  const best = bestPrice(item);
  const range = priceRange(item);
  const supplier = supplierOf(item);
  const profit = profitOf(item);
  const growth = growthPct(item);
  const competition = competitionOf(item);
  const compliance = complianceOf(item);
  const offers = offersOf(item);
  const m = supplier.match;
  const delivery =
    m?.delivery_max_days != null
      ? m.delivery_min_days != null && m.delivery_min_days !== m.delivery_max_days
        ? `${m.delivery_min_days}–${m.delivery_max_days}`
        : String(m.delivery_max_days)
      : null;
  return (
    <Card className="grid gap-3 p-4 sm:grid-cols-[96px_1fr]" data-testid="report-item" data-keyword={item.keyword}>
      {item.image_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={item.image_url} alt="" className="h-24 w-24 rounded-md border object-cover" loading="lazy" />
      ) : (
        <div className="flex h-24 w-24 items-center justify-center rounded-md border text-xs text-muted-foreground">
          {t("noImage")}
        </div>
      )}
      <div className="grid gap-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-base font-semibold">
            {item.rank}. {item.keyword}
          </h3>
          <span className="text-sm font-medium" data-testid="item-score">
            {t("score", { score: item.score })}
          </span>
        </div>
        <p className="text-sm" data-testid="item-why">
          {item.why}
        </p>
        <div className="flex flex-wrap gap-1">
          {item.flags.map((f) => (
            <Badge key={f} data-testid={`flag-${f}`} className={f === "crowded" || f === "arrives_too_late" ? "border-amber-600 text-amber-800 dark:text-amber-300" : undefined}>
              {t.has(`flags.${f}`) ? t(`flags.${f}`) : f}
            </Badge>
          ))}
          {item.fetched_at && isStale(item.fetched_at) ? <Badge>{t("stale")}</Badge> : null}
        </div>
        <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
          <Fact label={t("bestPrice")} value={best ? `${best.price}${best.platform ? ` · ${best.platform}` : ""}` : null} />
          <Fact label={t("priceRange")} value={range} />
          <Fact label={t("sizes")} value={item.sizes.length ? item.sizes.join(", ") : null} />
          <Fact
            label={t("supplier")}
            value={
              m
                ? `${String(m.provider ?? "").toUpperCase()} · ${t(`supplierStatus.${supplier.status}`)}${m.warehouse_country ? ` · ${m.warehouse_country}` : ""}`
                : t(`supplierStatus.${supplier.status}`)
            }
          />
          <Fact label={t("delivery")} value={delivery ? t("days", { days: delivery }) : null} />
          <Fact label={t("landed")} value={m?.landed_cost_minor != null ? formatMoney(m.landed_cost_minor, "GBP") : null} />
          <Fact label={t("profit")} value={profit?.profit ?? null} />
          <Fact label={t("breakEven")} value={profit?.breakEven ?? null} />
          <Fact label={t("growth")} value={growth != null ? `${growth > 0 ? "+" : ""}${growth}%` : null} />
          <Fact label={t("competition")} value={competition != null ? `${competition}/100` : null} />
          <Fact
            label={t("compliance")}
            value={`${t(`complianceStatus.${compliance.status}`)}${compliance.documents.length ? `: ${compliance.documents.join(", ")}` : ""}`}
          />
        </dl>
        {offers.length ? (
          <div className="grid gap-1">
            <span className="text-xs font-medium text-muted-foreground">{t("buyAt")}</span>
            <ul className="flex flex-wrap gap-2 text-sm" data-testid="item-links">
              {offers.map((o, i) => (
                <li key={`${o.url}-${i}`}>
                  <a href={o.url ?? "#"} target="_blank" rel="noopener noreferrer nofollow" className="underline">
                    {o.merchant ?? t("link", { n: i + 1 })}
                    {o.price_minor != null && o.currency ? ` (${formatMoney(o.price_minor, o.currency)})` : ""}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string | null }) {
  const t = useTranslations("reports");
  return (
    <div className="flex gap-2">
      <dt className="text-muted-foreground">{label}:</dt>
      <dd>{value ?? t("notAvailable")}</dd>
    </div>
  );
}
