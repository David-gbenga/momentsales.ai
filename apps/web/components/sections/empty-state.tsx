"use client";

import { can } from "@momentsales/shared";
import { useTranslations } from "next-intl";

import { Badge, Card, Notice } from "@/components/ui/primitives";
import { NAV, type SectionKey } from "@/lib/nav";
import { useSession } from "@/lib/session";

/** Placeholder screen that explains what the section will do and when it arrives. */
export function EmptyState({ section }: { section: Exclude<SectionKey, "settings"> }) {
  const t = useTranslations("sections");
  const tc = useTranslations("command");
  const { role } = useSession();
  const item = NAV.find((n) => n.key === section);
  return (
    <section aria-labelledby="section-title" className="grid max-w-2xl gap-4">
      <h1 id="section-title" className="text-2xl font-semibold tracking-tight">
        {t(`${section}.title`)}
      </h1>
      {item && !can(role, item.permission) ? (
        <Notice tone="error">{tc("notAllowed")}</Notice>
      ) : (
        <Card className="grid gap-3">
          <p>{t(`${section}.body`)}</p>
          <Badge className="w-fit">{t("comingIn", { phase: t(`${section}.phase`) })}</Badge>
        </Card>
      )}
    </section>
  );
}
