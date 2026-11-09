"use client";

import { useTranslations } from "next-intl";
import { use } from "react";

import { ReportView } from "@/components/reports/report-view";
import { Gate } from "@/components/sections/gate";

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const t = useTranslations("reports");
  return (
    <Gate action="reports.read" title={t("pageTitle")}>
      <ReportView id={id} />
    </Gate>
  );
}
