"use client";

import { useTranslations } from "next-intl";
import { Suspense } from "react";

import { SalesView } from "@/components/sales/sales-view";
import { Gate } from "@/components/sections/gate";

export default function SalesPage() {
  const t = useTranslations("sales");
  return (
    <Gate action="orders.read" title={t("title")}>
      <Suspense>
        <SalesView />
      </Suspense>
    </Gate>
  );
}
