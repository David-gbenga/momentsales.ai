"use client";

import { useMutation, useQueryClient, type UseMutationResult } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input, Notice, Select } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/ledger";
import { useAudience, type Audience, type AudienceIn } from "@/lib/research";

const AGES = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"] as const;
const GENDERS = ["any", "women", "men"] as const;
const BUDGETS = ["budget", "mid", "premium"] as const;

/** "Describe your audience" (FR-12): optional preferences, no personal data. */
export function AudienceForm() {
  const { data } = useAudience();
  const ws = useWorkspace();
  const qc = useQueryClient();
  // The mutation lives here, not in the keyed fields: a save changes updated_at,
  // which remounts the fields, and its success state must survive that.
  const save = useMutation({
    mutationFn: (body: AudienceIn) => api<Audience>("/audience-profile", { method: "PUT", body }),
    onSuccess: (saved) => qc.setQueryData(["research", "audience", ws.id], saved),
  });
  if (!data) return null;
  // Keyed so a saved profile from elsewhere resets the fields.
  return <AudienceFields key={data.updated_at ?? "new"} initial={data} save={save} />;
}

type SaveMutation = UseMutationResult<Audience, Error, AudienceIn>;

function AudienceFields({ initial, save }: { initial: Audience; save: SaveMutation }) {
  const t = useTranslations("audience");
  const [form, setForm] = useState({
    age_range: initial.age_range ?? "",
    gender_focus: initial.gender_focus ?? "",
    interests: (initial.interests ?? []).join(", "),
    budget_band: initial.budget_band ?? "",
    style: initial.style ?? "",
  });

  const toBody = (): AudienceIn => ({
    age_range: (form.age_range || null) as AudienceIn["age_range"],
    gender_focus: (form.gender_focus || null) as AudienceIn["gender_focus"],
    interests: form.interests
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 10),
    budget_band: (form.budget_band || null) as AudienceIn["budget_band"],
    style: form.style.trim() || null,
  });

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  return (
    <form
      className="grid gap-4"
      aria-labelledby="audience-title"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(toBody());
      }}
    >
      <div>
        <h2 id="audience-title" className="text-lg font-semibold">
          {t("title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("intro")}</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="aud-age" label={t("ageRange")}>
          <Select id="aud-age" value={form.age_range} onChange={set("age_range")}>
            <option value="">{t("any")}</option>
            {AGES.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </Select>
        </Field>
        <Field id="aud-gender" label={t("genderFocus")}>
          <Select id="aud-gender" value={form.gender_focus} onChange={set("gender_focus")}>
            <option value="">{t("notSet")}</option>
            {GENDERS.map((g) => (
              <option key={g} value={g}>
                {t(`gender.${g}`)}
              </option>
            ))}
          </Select>
        </Field>
        <Field id="aud-budget" label={t("budgetBand")}>
          <Select id="aud-budget" value={form.budget_band} onChange={set("budget_band")}>
            <option value="">{t("notSet")}</option>
            {BUDGETS.map((b) => (
              <option key={b} value={b}>
                {t(`budget.${b}`)}
              </option>
            ))}
          </Select>
        </Field>
        <Field id="aud-style" label={t("style")}>
          <Input id="aud-style" maxLength={60} value={form.style} onChange={set("style")} />
        </Field>
      </div>
      <Field id="aud-interests" label={t("interests")} help={t("interestsHelp")}>
        <Input
          id="aud-interests"
          aria-describedby="aud-interests-help"
          value={form.interests}
          onChange={set("interests")}
        />
      </Field>
      <div className="flex items-center gap-3">
        <Button type="submit" variant="outline" disabled={save.isPending}>
          {t("save")}
        </Button>
        {save.isSuccess ? <span className="text-sm">{t("saved")}</span> : null}
      </div>
      {save.error ? (
        <Notice tone="error">{save.error instanceof ApiError ? save.error.message : String(save.error)}</Notice>
      ) : null}
    </form>
  );
}
