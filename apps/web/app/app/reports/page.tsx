"use client";

import { useTranslations } from "next-intl";

import { ReportsList } from "@/components/reports/reports-list";
import { Gate } from "@/components/sections/gate";

export default function ReportsPage() {
  const t = useTranslations("reports");
  return (
    <Gate action="reports.read" title={t("recent")}>
      <ReportsList heading="h1" />
    </Gate>
  );
}
