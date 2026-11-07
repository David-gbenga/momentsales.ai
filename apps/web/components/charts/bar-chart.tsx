"use client";

import { formatMoney } from "@momentsales/shared";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card } from "@/components/ui/primitives";

/** A simple money bar chart. It's decorative for screen readers (aria-hidden): every chart
 * is paired with a data table that carries the same numbers. */
export function BarChartCard({
  title,
  data,
  valueLabel,
}: {
  title: string;
  data: { label: string; value: number }[];
  valueLabel: string;
}) {
  return (
    <Card className="grid gap-2">
      <h2 className="text-lg font-semibold">{title}</h2>
      <div aria-hidden="true" className="h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: "var(--muted-foreground)", fontSize: 12 }} />
            <YAxis
              tickFormatter={(v: number) => formatMoney(v)}
              width={72}
              tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
            />
            <Tooltip
              formatter={(v) => [formatMoney(Number(v)), valueLabel]}
              contentStyle={{ background: "var(--card)", borderColor: "var(--border)" }}
            />
            <Bar dataKey="value" fill="var(--primary)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}
