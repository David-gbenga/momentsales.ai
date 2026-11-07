"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { waitlistSchema, type WaitlistInput } from "@momentsales/shared";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Field, Input, Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";

interface Consent {
  version: string;
  text: string;
}

export function WaitlistForm() {
  const t = useTranslations("landing");
  const tc = useTranslations("common");
  const [done, setDone] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const consent = useQuery({
    queryKey: ["waitlist-consent"],
    queryFn: () => api<Consent>("/waitlist/consent", { auth: false, workspace: false }),
  });
  const form = useForm<WaitlistInput>({
    resolver: zodResolver(waitlistSchema),
    defaultValues: { email: "", consent: false as unknown as true },
  });
  const { errors, isSubmitting } = form.formState;

  async function onSubmit(values: WaitlistInput) {
    setServerError(null);
    try {
      await api("/waitlist", {
        body: { ...values, consent_version: consent.data?.version, source: "landing" },
        auth: false,
        workspace: false,
      });
      setDone(true);
    } catch (err) {
      setServerError(err instanceof ApiError ? err.message : tc("error"));
    }
  }

  if (done) {
    return (
      <div className="mt-6">
        <Notice tone="success">{t("checkEmail")}</Notice>
      </div>
    );
  }

  return (
    <form className="mt-6 grid gap-4" noValidate onSubmit={form.handleSubmit(onSubmit)}>
      <Field id="wl-email" label={t("emailLabel")} error={errors.email?.message}>
        <Input
          id="wl-email"
          type="email"
          autoComplete="email"
          aria-invalid={!!errors.email}
          aria-describedby={errors.email ? "wl-email-error" : undefined}
          {...form.register("email")}
        />
      </Field>
      <div className="grid gap-1.5">
        <label className="flex items-start gap-3 text-sm" htmlFor="wl-consent">
          <input
            id="wl-consent"
            type="checkbox"
            className="mt-0.5 size-5 shrink-0 accent-[var(--color-primary)]"
            aria-invalid={!!errors.consent}
            aria-describedby={errors.consent ? "wl-consent-error" : undefined}
            disabled={!consent.data}
            {...form.register("consent")}
          />
          <span>{consent.data?.text ?? tc("loading")}</span>
        </label>
        {errors.consent ? (
          <p id="wl-consent-error" role="alert" className="text-sm text-destructive">
            {errors.consent.message}
          </p>
        ) : null}
      </div>
      {serverError ? <Notice tone="error">{serverError}</Notice> : null}
      <Button type="submit" disabled={isSubmitting || !consent.data}>
        {isSubmitting ? t("joining") : t("join")}
      </Button>
    </form>
  );
}
