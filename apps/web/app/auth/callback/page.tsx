"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { Centered } from "@/components/centered";
import { Notice } from "@/components/ui/primitives";
import { useSession } from "@/lib/session";

/** Landing page after Google sign-in: the refresh cookie is already set. */
export default function AuthCallbackPage() {
  const t = useTranslations("auth");
  const router = useRouter();
  const { status, me } = useSession();
  const done = useRef(false);

  useEffect(() => {
    if (done.current || status === "loading") return;
    done.current = true;
    if (status === "signed_out") router.replace("/sign-in?error=google");
    else router.replace(me && me.memberships.length > 0 ? "/app" : "/onboarding/workspace");
  }, [status, me, router]);

  return (
    <Centered title={t("title")}>
      <Notice>{t("verifying")}</Notice>
    </Centered>
  );
}
