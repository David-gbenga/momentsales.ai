"use client";

import { useTranslations } from "next-intl";
import { Suspense } from "react";

import { ResearchView } from "@/components/research/research-view";
import { Gate } from "@/components/sections/gate";

export default function ResearchPage() {
  const t = useTranslations("research");
  return (
    <Gate action="research.run" title={t("title")}>
      <Suspense>
        <ResearchView />
      </Suspense>
    </Gate>
  );
}
