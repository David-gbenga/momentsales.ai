"use client";

import { formatMoney } from "@momentsales/shared";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { BarChartCard } from "@/components/charts/bar-chart";
import { Card, Field, Input } from "@/components/ui/primitives";
import { DataTable, Num, Td } from "@/components/ui/table";
import { monthStart, ukToday, useLedgerQuery, type Pnl, type PnlLine } from "@/lib/ledger";

const ROWS: { key: keyof PnlLine; label: string }[] = [
  { key: "revenue_minor", label: "revenue" },
  { key: "fees_minor", label: "stripeFees" },
  { key: "refunds_minor", label: "refunds" },
  { key: "cogs_minor", label: "cogs" },
  { key: "ads_minor", label: "ads" },
  { key: "other_spend_minor", label: "otherSpend" },
  { key: "adjustments_minor", label: "adjustments" },
];

export function PnlView() {
  const t = useTranslations("ledger");
  const [start, setStart] = useState(monthStart());
  const [end, setEnd] = useState(ukToday());
  const pnl = useLedgerQuery<Pnl>("pnl", `/pnl?start=${start}&end=${end}&group_by=product_key`, "ledger.read");
  const total = pnl.data?.total;
  const lines = pnl.data?.by_product ?? [];
  const label = (line: PnlLine) => line.product_key ?? t("noProduct");

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap gap-4">
        <Field id="pnl-start" label={t("from")}>
          <Input id="pnl-start" type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field id="pnl-end" label={t("to")}>
          <Input id="pnl-end" type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
        </Field>
      </div>
      {total ? (
        <Card className="grid gap-2">
          <h2 className="text-lg font-semibold">{t("pnlTitle")}</h2>
          <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-1 text-sm">
            {ROWS.map((r) => (
              <div key={r.key} className="contents">
                <dt>{t(r.label)}</dt>
                <dd className="text-right tabular-nums">{formatMoney(total[r.key] as number)}</dd>
              </div>
            ))}
            <dt className="border-t pt-1 font-semibold">{t("profit")}</dt>
            <dd className="border-t pt-1 text-right font-semibold tabular-nums" data-testid="pnl-profit">
              {formatMoney(total.profit_minor)}
            </dd>
          </dl>
        </Card>
      ) : null}
      {lines.length ? (
        <>
          <BarChartCard
            title={t("byProduct")}
            data={lines.map((line) => ({ label: label(line), value: line.profit_minor }))}
            valueLabel={t("profit")}
          />
          <DataTable
            caption={t("byProduct")}
            head={[t("byProduct"), t("revenue"), t("stripeFees"), t("refunds"), t("cogs"), t("ads"), t("otherSpend"), t("profit")]}
          >
            {lines.map((line) => (
              <tr key={label(line)}>
                <Td>{label(line)}</Td>
                <Num>{formatMoney(line.revenue_minor)}</Num>
                <Num>{formatMoney(line.fees_minor)}</Num>
                <Num>{formatMoney(line.refunds_minor)}</Num>
                <Num>{formatMoney(line.cogs_minor)}</Num>
                <Num>{formatMoney(line.ads_minor)}</Num>
                <Num>{formatMoney(line.other_spend_minor)}</Num>
                <Num className="font-medium">{formatMoney(line.profit_minor)}</Num>
              </tr>
            ))}
          </DataTable>
        </>
      ) : null}
    </div>
  );
}
