"use client";

import { useSearchParams } from "next/navigation";
import { notFound } from "next/navigation";
import { Suspense, useState } from "react";

import { Centered } from "@/components/centered";
import { Button } from "@/components/ui/button";
import { Field, Input, Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { IS_MOCK_MODE } from "@/lib/utils";

/** Stand-in for Google's consent screen when PROVIDER_MODE=mock (local only). */
function MockGoogle() {
  const params = useSearchParams();
  const [email, setEmail] = useState("demo.seller@example.com");
  const [name, setName] = useState("Demo Seller");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      const res = await api<{ redirect_url: string }>("/dev/mock-google/authorize", {
        body: {
          email,
          name,
          state: params.get("state"),
          nonce: params.get("nonce"),
          redirect_uri: params.get("redirect_uri"),
        },
        auth: false,
        workspace: false,
      });
      window.location.assign(new URL(res.redirect_url).pathname + new URL(res.redirect_url).search);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Mock sign-in failed");
    }
  }

  return (
    <Centered title="Mock Google sign-in">
      <Notice>This page simulates Google in local demo mode. No data leaves your machine.</Notice>
      <form className="grid gap-4" onSubmit={submit}>
        <Field id="mock-email" label="Google account email">
          <Input id="mock-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field id="mock-name" label="Name">
          <Input id="mock-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        {error ? <Notice tone="error">{error}</Notice> : null}
        <Button type="submit">Continue</Button>
      </form>
    </Centered>
  );
}

export default function MockGooglePage() {
  if (!IS_MOCK_MODE) notFound();
  return (
    <Suspense>
      <MockGoogle />
    </Suspense>
  );
}
