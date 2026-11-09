"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { use } from "react";

import { ReportBody } from "@/components/reports/report-body";
import { Notice } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import type { PublicReport } from "@/lib/reports";

/** A shared, read-only Top 10 report (no sign-in, no run cost, no workspace details). */
export default function SharedReportPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const t = useTranslations("reports");
  const { data, error } = useQuery({
    queryKey: ["public-report", token],
    queryFn: () => api<PublicReport>(`/public/reports/${encodeURIComponent(token)}`, { auth: false, workspace: false }),
    retry: false,
  });
  return (
    <div className="mx-auto grid max-w-4xl gap-6 px-4 py-8">
      <Link href="/" className="text-lg font-semibold tracking-tight">
        momentsales.ai
      </Link>
      <main id="main" className="grid gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">
          {data ? t("title", { count: data.items.length }) : t("pageTitle")}
        </h1>
        <p className="text-sm text-muted-foreground">{t("sharedNote")}</p>
        {error ? <Notice tone="error">{t("linkInvalid")}</Notice> : null}
        {!data && !error ? <p className="text-sm text-muted-foreground">{t("loading")}</p> : null}
        {data ? <ReportBody report={data} /> : null}
      </main>
    </div>
  );
}
