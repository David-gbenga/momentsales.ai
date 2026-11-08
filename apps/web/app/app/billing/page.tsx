"use client";

import { Suspense } from "react";

import { BillingView } from "@/components/billing/billing-view";

export default function BillingPage() {
  return (
    <Suspense>
      <BillingView />
    </Suspense>
  );
}
