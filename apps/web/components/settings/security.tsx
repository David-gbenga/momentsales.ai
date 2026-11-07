"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, Field, Input, Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";

export function SecuritySettings() {
  const t = useTranslations("settings");
  const tc = useTranslations("common");
  const { me, reload } = useSession();
  const [secret, setSecret] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setError(null);
    try {
      const res = await api<{ secret: string; otpauth_uri: string }>("/auth/2fa/enroll", {
        method: "POST",
        workspace: false,
      });
      setSecret(res.secret);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : tc("error"));
    }
  }

  async function confirm(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api("/auth/2fa/verify", { body: { code }, workspace: false });
      setSecret(null);
      setCode("");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : tc("error"));
    }
  }

  const enabled = me?.user.totp_enabled ?? false;
  return (
    <section aria-labelledby="security-title" className="grid gap-4">
      <h2 id="security-title" className="text-lg font-semibold">
        {t("security")}
      </h2>
      <Card className="grid gap-4">
        <h3 className="font-medium">{t("twoFactor")}</h3>
        <p className="text-sm text-muted-foreground">{enabled ? t("twoFactorOn") : t("twoFactorOff")}</p>
        {error ? <Notice tone="error">{error}</Notice> : null}
        {!enabled && !secret ? (
          <Button className="w-fit" onClick={start}>
            {t("enable2fa")}
          </Button>
        ) : null}
        {secret ? (
          <form className="grid gap-4" onSubmit={confirm}>
            <p className="text-sm">{t("scanHelp")}</p>
            <Field id="totp-secret" label={t("secretLabel")}>
              <Input id="totp-secret" readOnly value={secret} className="font-mono" />
            </Field>
            <Field id="totp-code" label="6-digit code">
              <Input
                id="totp-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              />
            </Field>
            <Button type="submit" className="w-fit" disabled={code.length !== 6}>
              {t("confirm2fa")}
            </Button>
          </form>
        ) : null}
      </Card>
    </section>
  );
}
