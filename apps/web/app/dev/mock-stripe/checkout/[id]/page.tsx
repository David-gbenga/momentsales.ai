"use client";

import { formatMoney } from "@momentsales/shared";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { notFound, useParams } from "next/navigation";
import { useState } from "react";

import { Centered } from "@/components/centered";
import { Button } from "@/components/ui/button";
import { Field, Input, Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { IS_MOCK_MODE } from "@/lib/utils";

interface MockSession {
  id: string;
  status: string;
  currency: string;
  amount_total: number;
  line_items: { name: string; quantity: number; unit_amount_minor: number }[];
  cancel_url: string;
}

/** Stand-in for Stripe Checkout when PROVIDER_MODE=mock (local only). */
export default function MockStripeCheckoutPage() {
  const t = useTranslations("mockStripe");
  const { id } = useParams<{ id: string }>();
  const [email, setEmail] = useState("shopper@example.com");
  const [error, setError] = useState<string | null>(null);
  const session = useQuery({
    queryKey: ["mock-stripe", id],
    queryFn: () => api<MockSession>(`/dev/mock-stripe/session/${id}`, { auth: false, workspace: false }),
    enabled: IS_MOCK_MODE,
  });
  if (!IS_MOCK_MODE) notFound();

  async function pay(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const res = await api<{ redirect_url: string }>(`/dev/mock-stripe/checkout/${id}/pay`, {
        body: { email },
        auth: false,
        workspace: false,
      });
      const next = new URL(res.redirect_url);
      window.location.assign(next.pathname + next.search);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  const s = session.data;
  const currency = s?.currency.toUpperCase() ?? "GBP";
  return (
    <Centered title={t("checkoutTitle")}>
      <p className="text-sm text-muted-foreground">{t("checkoutBody")}</p>
      {s ? (
        <form onSubmit={pay} className="grid gap-4">
          <ul className="grid gap-1 text-sm">
            {s.line_items.map((i) => (
              <li key={i.name} className="flex justify-between gap-4">
                <span>
                  {i.quantity} × {i.name}
                </span>
                <span className="tabular-nums">{formatMoney(i.quantity * i.unit_amount_minor, currency)}</span>
              </li>
            ))}
          </ul>
          <Field id="mock-email" label={t("email")}>
            <Input id="mock-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Button type="submit" disabled={s.status !== "open"}>
            {t("pay", { amount: formatMoney(s.amount_total, currency) })}
          </Button>
        </form>
      ) : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </Centered>
  );
}
