"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";

import { ReportBody } from "@/components/reports/report-body";
import { Button } from "@/components/ui/button";
import { Badge, Card, Notice, Select } from "@/components/ui/primitives";
import { api, ApiError, downloadFile } from "@/lib/api";
import { useWorkspace } from "@/lib/ledger";
import { formatDay } from "@/lib/research";
import { useReport, useShares, type ShareCreated } from "@/lib/reports";

/** The Top 10 report page: exports and share links around the shared report body. */
export function ReportView({ id }: { id: string }) {
  const t = useTranslations("reports");
  const ws = useWorkspace();
  const { data: report, error } = useReport(id);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function download(kind: "csv" | "pdf") {
    setBusy(kind);
    setProblem(null);
    try {
      await downloadFile(`/reports/${id}/export.${kind}`, `top10-${id}.${kind}`);
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  if (error) return <Notice tone="error">{error.message}</Notice>;
  if (!report) return <p className="text-sm text-muted-foreground">{t("loading")}</p>;
  return (
    <section aria-labelledby="report-title" className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 id="report-title" className="text-2xl font-semibold tracking-tight">
          {t("title", { count: report.items.length })}
        </h1>
        {ws.can("reports.export") ? (
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void download("csv")} disabled={busy !== null}>
              {t("downloadCsv")}
            </Button>
            <Button variant="outline" onClick={() => void download("pdf")} disabled={busy !== null}>
              {t("downloadPdf")}
            </Button>
          </div>
        ) : null}
      </div>
      <Link href={`/app/research?run=${report.run_id}`} className="text-sm underline">
        {t("backToRun")}
      </Link>
      {problem ? <Notice tone="error">{problem}</Notice> : null}
      {ws.can("reports.share") ? <SharePanel reportId={id} /> : null}
      <ReportBody report={report} />
    </section>
  );
}

function SharePanel({ reportId }: { reportId: string }) {
  const t = useTranslations("reports.share");
  const qc = useQueryClient();
  const ws = useWorkspace();
  const shares = useShares(reportId, true);
  const [days, setDays] = useState("30");
  const [created, setCreated] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const key = ["shares", ws.id, reportId];

  async function create() {
    setProblem(null);
    try {
      const share = await api<ShareCreated>(`/reports/${reportId}/shares`, { body: { days: Number(days) } });
      setCreated(share.url);
      await qc.invalidateQueries({ queryKey: key });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function revoke(shareId: string) {
    setProblem(null);
    try {
      await api<void>(`/reports/${reportId}/shares/${shareId}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: key });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : String(err));
    }
  }

  const active = (shares.data ?? []).filter((s) => s.active);
  return (
    <Card className="grid gap-3 p-4" data-testid="share-panel">
      <h2 className="text-base font-semibold">{t("title")}</h2>
      <p className="text-sm text-muted-foreground">{t("help")}</p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="grid gap-1 text-sm">
          {t("expires")}
          <Select value={days} onChange={(e) => setDays(e.target.value)} className="w-40">
            {["7", "30", "90"].map((d) => (
              <option key={d} value={d}>
                {t("days", { days: d })}
              </option>
            ))}
          </Select>
        </label>
        <Button onClick={() => void create()}>{t("create")}</Button>
      </div>
      {created ? (
        <Notice>
          <span className="block">{t("created")}</span>
          <a href={created} className="break-all underline" data-testid="share-url" target="_blank" rel="noopener noreferrer">
            {created}
          </a>
        </Notice>
      ) : null}
      {active.length ? (
        <ul className="grid gap-1 text-sm">
          {active.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-2">
              <Badge>{t("active")}</Badge>
              {t("until", { date: formatDay(s.expires_at.slice(0, 10)) })}
              <Button variant="outline" onClick={() => void revoke(s.id)}>
                {t("revoke")}
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      {problem ? <Notice tone="error">{problem}</Notice> : null}
    </Card>
  );
}
