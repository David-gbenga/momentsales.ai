"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  compact,
  formatMoney,
  parseSaleArgs,
  saleFormSchema,
  testCheckoutSchema,
  type SaleFormInput,
  type TestCheckoutInput,
} from "@momentsales/shared";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { CsvImport } from "@/components/ledger/csv-import";
import { Button } from "@/components/ui/button";
import { Badge, Card, Field, Input, Notice, Select } from "@/components/ui/primitives";
import { DataTable, Num, Td } from "@/components/ui/table";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import {
  monthStart,
  ukToday,
  useLedgerQuery,
  useRefreshLedger,
  useWorkspace,
  type OrderPage,
  type SalesSummary,
  type StripeStatus,
} from "@/lib/ledger";

export function SalesView() {
  const t = useTranslations("sales");
  const ws = useWorkspace();
  const params = useSearchParams();
  const [period, setPeriod] = useState<"day" | "week" | "month">("day");
  const summary = useLedgerQuery<SalesSummary>(
    "sales",
    `/sales/summary?period=${period}&start=${monthStart()}&end=${ukToday()}`,
    "orders.read",
    5000,
  );
  const orders = useLedgerQuery<OrderPage>("orders", "/orders?limit=50", "orders.read", 5000);
  const totals = summary.data?.totals;

  return (
    <section aria-labelledby="sales-title" className="grid gap-6">
      <h1 id="sales-title" className="text-2xl font-semibold tracking-tight">
        {t("title")}
      </h1>
      {totals ? (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5" aria-live="polite">
          {(
            [
              ["count", String(totals.orders)],
              ["gross", formatMoney(totals.gross_minor)],
              ["fees", formatMoney(totals.fees_minor)],
              ["refunds", formatMoney(totals.refunds_minor)],
              ["net", formatMoney(totals.net_minor)],
            ] as const
          ).map(([key, value]) => (
            <Card key={key} className="p-4">
              <dt className="text-sm text-muted-foreground">{t(key)}</dt>
              <dd className="text-xl font-semibold tabular-nums" data-testid={`sales-${key}`}>
                {value}
              </dd>
            </Card>
          ))}
        </dl>
      ) : null}
      {summary.data?.buckets.length ? (
        <div className="grid gap-2">
          <Field id="sales-period" label={t("period")}>
            <Select id="sales-period" className="w-40" value={period} onChange={(e) => setPeriod(e.target.value as typeof period)}>
              <option value="day">{t("day")}</option>
              <option value="week">{t("week")}</option>
              <option value="month">{t("month")}</option>
            </Select>
          </Field>
          <DataTable caption={t("period")} captionHidden head={["", t("count"), t("gross"), t("fees"), t("refunds"), t("net")]}>
            {summary.data.buckets.map((b) => (
              <tr key={b.period_start}>
                <Td>{b.period_start}</Td>
                <Num>{b.orders}</Num>
                <Num>{formatMoney(b.gross_minor)}</Num>
                <Num>{formatMoney(b.fees_minor)}</Num>
                <Num>{formatMoney(b.refunds_minor)}</Num>
                <Num>{formatMoney(b.net_minor)}</Num>
              </tr>
            ))}
          </DataTable>
        </div>
      ) : null}

      <OrderTable orders={orders.data} />

      {ws.can("checkout.test") ? <TestCheckout /> : null}
      {ws.can("sales.record") ? (
        <>
          <Card className="grid gap-4">
            <h2 className="text-lg font-semibold">{t("recordSale")}</h2>
            <p className="text-sm text-muted-foreground">{t("recordSaleHelp")}</p>
            <SaleForm key={params.get("args") ?? ""} args={params.get("new") === "sale" ? (params.get("args") ?? "") : ""} />
          </Card>
          <Card className="grid gap-4">
            <h2 className="text-lg font-semibold">{t("import")}</h2>
            <CsvImport kind="sales" />
          </Card>
        </>
      ) : null}
    </section>
  );
}

function OrderTable({ orders }: { orders: OrderPage | undefined }) {
  const t = useTranslations("sales");
  const tm = useTranslations("money");
  if (!orders?.items.length) return <p className="text-sm text-muted-foreground">{tm("none")}</p>;
  return (
    <DataTable caption={t("orders")} head={[t("paidAt"), t("channel"), t("items"), t("status"), t("gross"), t("fees"), t("refunds"), t("net")]}>
      {orders.items.map((o) => (
        <tr key={o.id}>
          <Td>{o.paid_at ? new Date(o.paid_at).toLocaleString("en-GB") : ""}</Td>
          <Td>{o.channel}</Td>
          <Td>{o.items.map((i) => `${i.quantity} × ${i.description}`).join(", ")}</Td>
          <Td>
            <Badge>{t(`statuses.${o.status}`)}</Badge>
          </Td>
          <Num>{formatMoney(o.gross_minor, o.currency)}</Num>
          <Num>{formatMoney(o.fees_minor, o.currency)}</Num>
          <Num>{formatMoney(o.refunded_minor, o.currency)}</Num>
          <Num>{formatMoney(o.net_minor, o.currency)}</Num>
        </tr>
      ))}
    </DataTable>
  );
}

function TestCheckout() {
  const t = useTranslations("sales");
  const tm = useTranslations("money");
  const stripe = useLedgerQuery<StripeStatus>("stripe", "/integrations/stripe", "workspace.read");
  const [idemKey, setIdemKey] = useState(newIdempotencyKey);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const form = useForm({
    resolver: zodResolver(testCheckoutSchema),
    defaultValues: { item: { description: "", quantity: 1, unit_amount: "", product_key: "" } },
  });
  const ready = stripe.data?.charges_enabled;

  async function create(values: TestCheckoutInput) {
    setError(null);
    try {
      const r = await api<{ url: string }>("/checkout/test-sessions", {
        body: { items: [compact(values.item)] },
        headers: { "Idempotency-Key": idemKey },
      });
      setIdemKey(newIdempotencyKey());
      setUrl(r.url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <Card className="grid gap-4">
      <h2 className="flex flex-wrap items-center gap-2 text-lg font-semibold">
        {t("testCheckout")} <Badge>{tm("testMode")}</Badge>
      </h2>
      <p className="text-sm text-muted-foreground">{t("testCheckoutHelp")}</p>
      {!ready ? (
        <Notice>{t("stripeNotReady")}</Notice>
      ) : (
        <form onSubmit={form.handleSubmit(create)} className="grid gap-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-4">
            <Field id="checkout-description" label={t("itemDescription")} error={form.formState.errors.item?.description?.message}>
              <Input id="checkout-description" {...form.register("item.description")} />
            </Field>
            <Field id="checkout-quantity" label={t("quantity")} error={form.formState.errors.item?.quantity?.message}>
              <Input id="checkout-quantity" type="number" min={1} inputMode="numeric" {...form.register("item.quantity")} />
            </Field>
            <Field id="checkout-unit_amount" label={t("unitPrice")} error={form.formState.errors.item?.unit_amount?.message}>
              <Input id="checkout-unit_amount" inputMode="decimal" {...form.register("item.unit_amount")} />
            </Field>
            <Field id="checkout-product_key" label={tm("productKey")} error={form.formState.errors.item?.product_key?.message}>
              <Input id="checkout-product_key" {...form.register("item.product_key")} />
            </Field>
          </div>
          <Button type="submit" className="w-fit" disabled={form.formState.isSubmitting}>
            {t("createCheckout")}
          </Button>
        </form>
      )}
      {url ? (
        <Button asChild variant="outline" className="w-fit">
          <a href={url}>{t("openCheckout")}</a>
        </Button>
      ) : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </Card>
  );
}

function SaleForm({ args }: { args: string }) {
  const t = useTranslations("sales");
  const tm = useTranslations("money");
  const refresh = useRefreshLedger();
  const draft = parseSaleArgs(args);
  const [idemKey, setIdemKey] = useState(newIdempotencyKey);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const form = useForm({
    resolver: zodResolver(saleFormSchema),
    defaultValues: {
      sold_on: draft.sold_on ?? ukToday(),
      channel: draft.channel ?? "",
      order_ref: draft.order_ref ?? "",
      item: {
        description: draft.description ?? "",
        quantity: 1,
        unit_amount: draft.amount ?? "",
        product_key: draft.product_key ?? "",
      },
    },
  });
  const { errors, isSubmitting } = form.formState;

  async function save(values: SaleFormInput) {
    setNotice(null);
    try {
      await api("/sales", {
        body: {
          sold_on: values.sold_on,
          channel: values.channel,
          ...(values.order_ref ? { order_ref: values.order_ref } : {}),
          items: [compact(values.item)],
        },
        headers: { "Idempotency-Key": idemKey },
      });
      setIdemKey(newIdempotencyKey());
      setNotice({ tone: "success", text: t("saleRecorded") });
      form.reset();
      await refresh();
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof ApiError ? err.message : String(err) });
    }
  }

  return (
    <form onSubmit={form.handleSubmit(save)} className="grid gap-4" noValidate>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field id="sale-sold_on" label={tm("date")} error={errors.sold_on?.message}>
          <Input id="sale-sold_on" type="date" {...form.register("sold_on")} />
        </Field>
        <Field id="sale-channel" label={t("channelLabel")} error={errors.channel?.message}>
          <Input id="sale-channel" {...form.register("channel")} />
        </Field>
        <Field id="sale-order_ref" label={t("orderRef")}>
          <Input id="sale-order_ref" {...form.register("order_ref")} />
        </Field>
      </div>
          <div className="grid gap-4 sm:grid-cols-4">
            <Field id="sale-description" label={t("itemDescription")} error={errors.item?.description?.message}>
              <Input id="sale-description" {...form.register("item.description")} />
            </Field>
            <Field id="sale-quantity" label={t("quantity")} error={errors.item?.quantity?.message}>
              <Input id="sale-quantity" type="number" min={1} inputMode="numeric" {...form.register("item.quantity")} />
            </Field>
            <Field id="sale-unit_amount" label={t("unitPrice")} error={errors.item?.unit_amount?.message}>
              <Input id="sale-unit_amount" inputMode="decimal" {...form.register("item.unit_amount")} />
            </Field>
            <Field id="sale-product_key" label={tm("productKey")} error={errors.item?.product_key?.message}>
              <Input id="sale-product_key" {...form.register("item.product_key")} />
            </Field>
          </div>
      {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}
      <Button type="submit" disabled={isSubmitting} className="w-fit">
        {isSubmitting ? tm("saving") : tm("save")}
      </Button>
    </form>
  );
}
