"use client";

import { useTranslations } from "next-intl";
import { Suspense } from "react";

import { AvasamCard } from "@/components/integrations/avasam-card";
import { StripeCard } from "@/components/integrations/stripe-card";

export default function IntegrationsPage() {
  const t = useTranslations("integrations");
  return (
    <section aria-labelledby="integrations-title" className="grid max-w-2xl gap-4">
      <h1 id="integrations-title" className="text-2xl font-semibold tracking-tight">
        {t("title")}
      </h1>
      <Suspense>
        <StripeCard />
      </Suspense>
      <AvasamCard />
    </section>
  );
}
