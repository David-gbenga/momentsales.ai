"use client";

import { useTranslations } from "next-intl";
import { notFound, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Centered } from "@/components/centered";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { IS_MOCK_MODE } from "@/lib/utils";

/** Stand-in for Stripe's hosted onboarding when PROVIDER_MODE=mock (local only). */
function MockOnboard() {
  const t = useTranslations("mockStripe");
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  async function finish() {
    try {
      const res = await api<{ redirect_url: string }>("/dev/mock-stripe/onboard", {
        body: { account: params.get("account") },
        auth: false,
        workspace: false,
      });
      const next = new URL(res.redirect_url);
      window.location.assign(next.pathname + next.search);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <Centered title={t("onboardTitle")}>
      <p className="text-sm text-muted-foreground">{t("onboardBody")}</p>
      <p className="font-mono text-sm">{params.get("account")}</p>
      <Button onClick={() => void finish()}>{t("finish")}</Button>
      {error ? <Notice tone="error">{error}</Notice> : null}
    </Centered>
  );
}

export default function MockStripeOnboardPage() {
  if (!IS_MOCK_MODE) notFound();
  return (
    <Suspense>
      <MockOnboard />
    </Suspense>
  );
}
