"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Badge, Card, Field, Input, Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { monthStart, ukToday, useLedgerQuery, useRefreshLedger, type ExportOut } from "@/lib/ledger";

export function ExportPanel() {
  const t = useTranslations("ledger");
  const refresh = useRefreshLedger();
  const [start, setStart] = useState(monthStart());
  const [end, setEnd] = useState(ukToday());
  const [current, setCurrent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const history = useLedgerQuery<ExportOut[]>("exports", "/exports", "ledger.export");
  const detail = useQuery({
    queryKey: ["exports", current],
    queryFn: () => api<ExportOut>(`/exports/${current}`),
    enabled: !!current,
    refetchInterval: (q) => (q.state.data && ["pending", "running"].includes(q.state.data.status) ? 1500 : false),
  });

  async function build(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const created = await api<ExportOut>("/exports", { body: { period_start: start, period_end: end } });
      setCurrent(created.id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  const d = detail.data;
  return (
    <div className="grid gap-6">
      <Card className="grid gap-4">
        <h2 className="text-lg font-semibold">{t("exportTitle")}</h2>
        <p className="text-sm text-muted-foreground">{t("exportHelp")}</p>
        <form onSubmit={build} className="flex flex-wrap items-end gap-4">
          <Field id="export-start" label={t("from")}>
            <Input id="export-start" type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
          </Field>
          <Field id="export-end" label={t("to")}>
            <Input id="export-end" type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
          </Field>
          <Button type="submit">{t("exportStart")}</Button>
        </form>
        {error ? <Notice tone="error">{error}</Notice> : null}
        {d ? (
          <div className="grid gap-2" role="status">
            {d.status === "completed" ? (
              <>
                <p className="flex flex-wrap items-center gap-2 font-medium">
                  {t("exportReady")}
                  <Badge>{d.audit_chain_ok ? t("chainOk") : t("chainBad")}</Badge>
                </p>
                <ul className="grid gap-1 text-sm">
                  {(d.files ?? []).map((f) => (
                    <li key={f.name}>
                      <a className="underline" href={f.url ?? "#"} download={f.name}>
                        {f.name}
                      </a>{" "}
                      <span className="text-muted-foreground">({Math.ceil(f.bytes / 1024)} KB)</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : d.status === "failed" ? (
              <Notice tone="error">{d.error ?? t("exportFailed")}</Notice>
            ) : (
              <p>{t("exportBuilding")}</p>
            )}
          </div>
        ) : null}
      </Card>
      {history.data?.length ? (
        <ul className="grid gap-2 text-sm">
          {history.data.map((e) => (
            <li key={e.id} className="flex flex-wrap items-center gap-2">
              <span>
                {e.period_start} → {e.period_end}
              </span>
              <Badge>{e.status}</Badge>
              {e.status === "completed" ? (
                <Button size="sm" variant="ghost" onClick={() => setCurrent(e.id)}>
                  {t("download")}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
