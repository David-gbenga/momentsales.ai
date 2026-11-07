"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  compact,
  TOUCH_CATEGORIES,
  touchFormSchema,
  type TouchDraft,
  type TouchFormInput,
} from "@momentsales/shared";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Field, Input, Notice, Select } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useRefreshLedger } from "@/lib/ledger";

export function TouchForm({ draft = {} }: { draft?: TouchDraft }) {
  const t = useTranslations("money");
  const tl = useTranslations("ledger");
  const refresh = useRefreshLedger();
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const form = useForm({
    resolver: zodResolver(touchFormSchema),
    defaultValues: {
      category: draft.category ?? "pricing",
      minutes: draft.minutes ?? 0,
      description: draft.description ?? "",
      note: "",
    },
  });
  const { errors, isSubmitting } = form.formState;

  async function save(values: TouchFormInput) {
    setNotice(null);
    try {
      await api("/touches", { body: compact(values) });
      setNotice({ tone: "success", text: tl("touchSaved") });
      form.reset({ ...values, description: "", minutes: 0, note: "" });
      await refresh();
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof ApiError ? err.message : String(err) });
    }
  }

  return (
    <form onSubmit={form.handleSubmit(save)} className="grid gap-4" noValidate>
      <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
        <Field id="touch-category" label={t("category")}>
          <Select id="touch-category" {...form.register("category")}>
            {TOUCH_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {t(`touchCategories.${c}`)}
              </option>
            ))}
          </Select>
        </Field>
        <Field id="touch-minutes" label={tl("minutes")} error={errors.minutes?.message}>
          <Input id="touch-minutes" type="number" min={0} max={1440} inputMode="numeric" {...form.register("minutes")} />
        </Field>
      </div>
      <Field id="touch-description" label={tl("what")} error={errors.description?.message}>
        <Input
          id="touch-description"
          aria-invalid={!!errors.description || undefined}
          aria-describedby={errors.description ? "touch-description-error" : undefined}
          {...form.register("description")}
        />
      </Field>
      <Field id="touch-note" label={t("note")}>
        <Input id="touch-note" {...form.register("note")} />
      </Field>
      {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}
      <Button type="submit" disabled={isSubmitting} className="w-fit">
        {isSubmitting ? t("saving") : t("save")}
      </Button>
    </form>
  );
}
