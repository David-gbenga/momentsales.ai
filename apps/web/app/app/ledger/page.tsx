"use client";

import { useTranslations } from "next-intl";
import { Suspense } from "react";

import { LedgerTabs } from "@/components/ledger/ledger-tabs";
import { Gate } from "@/components/sections/gate";

export default function LedgerPage() {
  const t = useTranslations("ledger");
  return (
    <Gate action="ledger.read" title={t("title")}>
      <Suspense>
        <LedgerTabs />
      </Suspense>
    </Gate>
  );
}
