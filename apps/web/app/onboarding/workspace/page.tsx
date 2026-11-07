"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { LAUNCH_COUNTRY, workspaceSchema, type WorkspaceInput } from "@momentsales/shared";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";

import { Centered } from "@/components/centered";
import { Button } from "@/components/ui/button";
import { Field, Input, Notice, Select } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";

interface Country {
  code: string;
  name: string;
  currency: string;
}

export default function CreateWorkspacePage() {
  const t = useTranslations("onboarding");
  const tc = useTranslations("common");
  const router = useRouter();
  const { status, reload, selectWorkspace } = useSession();
  const [error, setError] = useState<string | null>(null);
  const countries = useQuery({
    queryKey: ["countries"],
    queryFn: () => api<Country[]>("/countries", { auth: false, workspace: false }),
  });
  const form = useForm<WorkspaceInput>({
    resolver: zodResolver(workspaceSchema),
    defaultValues: { name: "", legal_name: "" },
  });
  const { errors, isSubmitting } = form.formState;

  useEffect(() => {
    if (status === "signed_out") router.replace("/sign-in");
  }, [status, router]);

  const options = countries.data ?? [LAUNCH_COUNTRY];
  const country = options[0] ?? LAUNCH_COUNTRY;

  async function onSubmit(values: WorkspaceInput) {
    setError(null);
    try {
      const ws = await api<{ id: string }>("/workspaces", {
        body: { name: values.name, legal_name: values.legal_name || null, country: country.code },
        workspace: false,
      });
      await reload();
      selectWorkspace(ws.id);
      router.replace("/app");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : tc("error"));
    }
  }

  return (
    <Centered title={t("title")}>
      <p className="text-muted-foreground">{t("subtitle")}</p>
      <form className="grid gap-4" noValidate onSubmit={form.handleSubmit(onSubmit)}>
        <Field id="ws-name" label={t("name")} error={errors.name?.message}>
          <Input id="ws-name" aria-invalid={!!errors.name} {...form.register("name")} />
        </Field>
        <Field id="ws-legal" label={t("legalName")} error={errors.legal_name?.message}>
          <Input id="ws-legal" autoComplete="organization" {...form.register("legal_name")} />
        </Field>
        <Field id="ws-country" label={t("country")} help={t("countryHelp")}>
          <Select id="ws-country" value={country.code} disabled aria-describedby="ws-country-help">
            {options.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field id="ws-currency" label={t("currency")}>
          <Input id="ws-currency" value={country.currency} readOnly />
        </Field>
        {error ? <Notice tone="error">{error}</Notice> : null}
        <Button type="submit" disabled={isSubmitting || status !== "signed_in"}>
          {isSubmitting ? t("creating") : t("create")}
        </Button>
      </form>
    </Centered>
  );
}
