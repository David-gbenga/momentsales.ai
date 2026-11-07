"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  BASE_CURRENCY,
  compact,
  CURRENCIES,
  SPEND_CATEGORIES,
  spendFormSchema,
  type SpendDraft,
  type SpendFormInput,
} from "@momentsales/shared";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Field, Input, Notice, Select } from "@/components/ui/primitives";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import { ukToday, useRefreshLedger } from "@/lib/ledger";

export interface SpendFormProps {
  draft?: SpendDraft;
  /** Confirm a receipt: the spend is created by POST /receipts/{id}/confirm instead. */
  receiptId?: string;
  onSaved?: () => void;
  submitLabel?: string;
}

export function SpendForm({ draft = {}, receiptId, onSaved, submitLabel }: SpendFormProps) {
  const t = useTranslations("money");
  const tl = useTranslations("ledger");
  const refresh = useRefreshLedger();
  const [idemKey, setIdemKey] = useState(newIdempotencyKey);
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const form = useForm<SpendFormInput>({
    resolver: zodResolver(spendFormSchema),
    defaultValues: {
      amount: draft.amount ?? "",
      currency: (CURRENCIES as readonly string[]).includes(draft.currency ?? "")
        ? (draft.currency as SpendFormInput["currency"])
        : BASE_CURRENCY,
      base_amount: "",
      tax_amount: draft.tax_amount ?? "",
      spent_on: draft.spent_on ?? ukToday(),
      vendor: draft.vendor ?? "",
      category: draft.category ?? "other",
      payment_method: draft.payment_method ?? "card",
      product_key: draft.product_key ?? "",
      note: draft.note ?? "",
    },
  });
  const { errors, isSubmitting } = form.formState;
  const currency = useWatch({ control: form.control, name: "currency" });
  const category = useWatch({ control: form.control, name: "category" });

  async function save(values: SpendFormInput) {
    setNotice(null);
    const body = compact(values);
    try {
      await api(receiptId ? `/receipts/${receiptId}/confirm` : "/spends", {
        body: receiptId ? { spend: body } : body,
        headers: { "Idempotency-Key": idemKey },
      });
      setIdemKey(newIdempotencyKey());
      setNotice({ tone: "success", text: tl("spendSaved") });
      form.reset({ ...form.getValues(), amount: "", vendor: "", tax_amount: "", base_amount: "", note: "" });
      await refresh();
      onSaved?.();
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof ApiError ? err.message : String(err) });
    }
  }

  const err = (name: keyof SpendFormInput) => errors[name]?.message;
  const aria = (name: keyof SpendFormInput) => ({
    "aria-invalid": !!errors[name] || undefined,
    "aria-describedby": errors[name] ? `spend-${name}-error` : undefined,
  });

  return (
    <form onSubmit={form.handleSubmit(save)} className="grid gap-4" noValidate>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field id="spend-amount" label={t("amount")} error={err("amount")}>
          <Input id="spend-amount" inputMode="decimal" autoComplete="off" {...aria("amount")} {...form.register("amount")} />
        </Field>
        <Field id="spend-currency" label={t("currency")}>
          <Select id="spend-currency" {...form.register("currency")}>
            {CURRENCIES.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </Select>
        </Field>
        <Field id="spend-spent_on" label={t("date")} error={err("spent_on")}>
          <Input id="spend-spent_on" type="date" {...aria("spent_on")} {...form.register("spent_on")} />
        </Field>
      </div>
      {currency !== BASE_CURRENCY ? (
        <Field id="spend-base_amount" label={t("baseAmount")} help={t("baseAmountHelp")} error={err("base_amount")}>
          <Input id="spend-base_amount" inputMode="decimal" {...aria("base_amount")} {...form.register("base_amount")} />
        </Field>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="spend-vendor" label={t("vendor")} error={err("vendor")}>
          <Input id="spend-vendor" {...aria("vendor")} {...form.register("vendor")} />
        </Field>
        <Field id="spend-category" label={t("category")}>
          <Select id="spend-category" {...form.register("category")}>
            {SPEND_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {t(`categories.${c}`)}
              </option>
            ))}
          </Select>
        </Field>
        <Field id="spend-payment_method" label={t("paymentMethod")} error={err("payment_method")}>
          <Input id="spend-payment_method" {...aria("payment_method")} {...form.register("payment_method")} />
        </Field>
        <Field id="spend-tax_amount" label={t("vat")} error={err("tax_amount")}>
          <Input id="spend-tax_amount" inputMode="decimal" {...aria("tax_amount")} {...form.register("tax_amount")} />
        </Field>
      </div>
      <Field
        id="spend-product_key"
        label={t("productKey")}
        help={category === "ads" ? undefined : t("productKeyHelp")}
        error={err("product_key")}
      >
        <Input id="spend-product_key" autoComplete="off" {...aria("product_key")} {...form.register("product_key")} />
      </Field>
      <Field id="spend-note" label={t("note")}>
        <Input id="spend-note" {...form.register("note")} />
      </Field>
      {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}
      <Button type="submit" disabled={isSubmitting} className="w-fit">
        {isSubmitting ? t("saving") : (submitLabel ?? t("save"))}
      </Button>
    </form>
  );
}
