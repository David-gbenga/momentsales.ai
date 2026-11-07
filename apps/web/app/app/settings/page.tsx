"use client";

import { useTranslations } from "next-intl";

import { SecuritySettings } from "@/components/settings/security";
import { TeamSettings } from "@/components/settings/team";

export default function SettingsPage() {
  const t = useTranslations("settings");
  return (
    <div className="grid max-w-3xl gap-8">
      <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
      <TeamSettings />
      <SecuritySettings />
    </div>
  );
}
