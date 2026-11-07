"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { Centered } from "@/components/centered";
import { Notice } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

function Accept() {
  const t = useTranslations("invite");
  const params = useSearchParams();
  const router = useRouter();
  const { status, reload, selectWorkspace } = useSession();
  const [state, setState] = useState<"pending" | "error">("pending");
  const done = useRef(false);

  useEffect(() => {
    if (done.current || status !== "signed_in") return;
    done.current = true;
    api<{ workspace_id: string }>("/invitations/accept", {
      body: { token: params.get("token") },
      workspace: false,
    })
      .then(async (res) => {
        await reload();
        selectWorkspace(res.workspace_id);
        router.replace("/app");
      })
      .catch(() => setState("error"));
  }, [status, params, reload, selectWorkspace, router]);

  if (status === "signed_out") {
    return (
      <Centered title="momentsales.ai">
        <Notice>{t("signInFirst")}</Notice>
        <Link href="/sign-in" className="text-sm underline">
          Sign in
        </Link>
      </Centered>
    );
  }
  return (
    <Centered title="momentsales.ai">
      {state === "error" ? <Notice tone="error">{t("error")}</Notice> : <Notice>{t("accepting")}</Notice>}
    </Centered>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense>
      <Accept />
    </Suspense>
  );
}
