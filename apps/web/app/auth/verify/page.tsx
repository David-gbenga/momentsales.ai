"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { Centered } from "@/components/centered";
import { Button } from "@/components/ui/button";
import { Field, Input, Notice } from "@/components/ui/primitives";
import { api, ApiError, type SessionResponse } from "@/lib/api";
import { useSession } from "@/lib/session";

type VerifyResponse = SessionResponse | { status: "mfa_required"; mfa_token: string };

function Verify() {
  const t = useTranslations("auth");
  const params = useSearchParams();
  const router = useRouter();
  const { signInWithToken } = useSession();
  const [mfaToken, setMfaToken] = useState<string | null>(params.get("mfa_token"));
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const started = useRef(false);

  async function finish(session: SessionResponse) {
    const me = await signInWithToken(session.access_token);
    router.replace(me && me.memberships.length > 0 ? "/app" : "/onboarding/workspace");
  }

  useEffect(() => {
    const token = params.get("token");
    if (started.current || !token) return;
    started.current = true;
    api<VerifyResponse>("/auth/magic-link/verify", { body: { token }, auth: false, workspace: false })
      .then((res) => (res.status === "mfa_required" ? setMfaToken(res.mfa_token) : finish(res)))
      .catch(() => setError(t("invalidLink")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api<SessionResponse>("/auth/2fa/challenge", {
        body: { mfa_token: mfaToken, code },
        auth: false,
        workspace: false,
      });
      await finish(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("invalidLink"));
    } finally {
      setBusy(false);
    }
  }

  if (error && !mfaToken) {
    return (
      <Centered title={t("title")}>
        <Notice tone="error">{error}</Notice>
        <Link href="/sign-in" className="text-sm underline">
          {t("backToSignIn")}
        </Link>
      </Centered>
    );
  }

  if (mfaToken) {
    return (
      <Centered title={t("mfaTitle")}>
        <form className="grid gap-4" onSubmit={submitCode}>
          <Field id="code" label={t("mfaLabel")} error={error ?? undefined}>
            <Input
              id="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            />
          </Field>
          <Button type="submit" disabled={busy || code.length !== 6}>
            {t("verify")}
          </Button>
        </form>
      </Centered>
    );
  }

  return (
    <Centered title={t("title")}>
      <Notice>{t("verifying")}</Notice>
    </Centered>
  );
}

export default function VerifyPage() {
  return (
    <Suspense>
      <Verify />
    </Suspense>
  );
}
