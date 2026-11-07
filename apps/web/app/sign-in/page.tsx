"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { magicLinkSchema } from "@momentsales/shared";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import type { z } from "zod";

import { Centered } from "@/components/centered";
import { Button } from "@/components/ui/button";
import { Field, Input, Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { IS_MOCK_MODE } from "@/lib/utils";

type FormValues = z.infer<typeof magicLinkSchema>;

function SignInForm() {
  const t = useTranslations("auth");
  const tc = useTranslations("common");
  const params = useSearchParams();
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(
    params.get("error") === "google" ? t("googleError") : null,
  );
  const form = useForm<FormValues>({
    resolver: zodResolver(magicLinkSchema),
    defaultValues: { email: "" },
  });
  const { errors, isSubmitting } = form.formState;

  async function onSubmit(values: FormValues) {
    setError(null);
    try {
      await api("/auth/magic-link", { body: values, auth: false, workspace: false });
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : tc("error"));
    }
  }

  return (
    <Centered title={t("title")}>
      <p className="text-muted-foreground">{t("subtitle")}</p>
      {IS_MOCK_MODE ? <Notice>{tc("mockMode")}</Notice> : null}
      {sent ? (
        <Notice tone="success">{t("sent")}</Notice>
      ) : (
        <form className="grid gap-4" noValidate onSubmit={form.handleSubmit(onSubmit)}>
          <Field id="email" label={t("emailLabel")} error={errors.email?.message}>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              aria-invalid={!!errors.email}
              aria-describedby={errors.email ? "email-error" : undefined}
              {...form.register("email")}
            />
          </Field>
          {error ? <Notice tone="error">{error}</Notice> : null}
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? t("sending") : t("sendLink")}
          </Button>
        </form>
      )}
      <div className="flex items-center gap-3 text-sm text-muted-foreground" aria-hidden>
        <span className="h-px flex-1 bg-border" />
        {t("or")}
        <span className="h-px flex-1 bg-border" />
      </div>
      <Button variant="outline" asChild>
        <a href="/api/v1/auth/google/start">{t("google")}</a>
      </Button>
    </Centered>
  );
}

export default function SignInPage() {
  return (
    <Suspense>
      <SignInForm />
    </Suspense>
  );
}
