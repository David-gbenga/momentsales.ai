"use client";

import { can, type Action } from "@momentsales/shared";
import { useTranslations } from "next-intl";

import { Notice } from "@/components/ui/primitives";
import { useSession } from "@/lib/session";

/** Shows children only to roles the API allows (the API enforces it as well). */
export function Gate({ action, title, children }: { action: Action; title: string; children: React.ReactNode }) {
  const tc = useTranslations("command");
  const { role } = useSession();
  if (can(role, action)) return <>{children}</>;
  return (
    <section aria-labelledby="gate-title" className="grid max-w-2xl gap-4">
      <h1 id="gate-title" className="text-2xl font-semibold tracking-tight">
        {title}
      </h1>
      <Notice tone="error">{tc("notAllowed")}</Notice>
    </section>
  );
}
