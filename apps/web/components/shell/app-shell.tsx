"use client";

import { can, ROLE_LABELS } from "@momentsales/shared";
import { Command as CommandIcon, LogOut, Moon, Sun } from "lucide-react";
import { useTranslations } from "next-intl";
import { useTheme } from "next-themes";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { CommandBar } from "@/components/shell/command-bar";
import { Button } from "@/components/ui/button";
import { Badge, Notice, Select } from "@/components/ui/primitives";
import { NAV } from "@/lib/nav";
import { useSession } from "@/lib/session";
import { cn, IS_MOCK_MODE } from "@/lib/utils";

export function AppShell({ children }: { children: React.ReactNode }) {
  const t = useTranslations("nav");
  const tc = useTranslations("common");
  const { status, me, workspace, role, selectWorkspace, signOut } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const { resolvedTheme, setTheme } = useTheme();

  useEffect(() => {
    if (status === "signed_out") router.replace("/sign-in");
    else if (status === "signed_in" && me && me.memberships.length === 0)
      router.replace("/onboarding/workspace");
  }, [status, me, router]);

  if (status !== "signed_in" || !workspace) {
    return (
      <div className="p-6" role="status">
        {tc("loading")}
      </div>
    );
  }

  const items = NAV.filter((item) => can(role, item.permission));

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[16rem_1fr]">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        {t("skip")}
      </a>
      <aside className="border-b bg-card md:sticky md:top-0 md:h-dvh md:border-r md:border-b-0">
        <div className="flex items-center justify-between gap-2 p-4">
          <Link href="/app" className="font-semibold tracking-tight">
            momentsales.ai
          </Link>
          <Button
            variant="ghost"
            size="icon"
            aria-label={t("theme")}
            onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
          >
            {resolvedTheme === "dark" ? <Sun /> : <Moon />}
          </Button>
        </div>
        <div className="grid gap-1.5 px-4 pb-3">
          <label htmlFor="ws-switch" className="text-xs font-medium text-muted-foreground">
            {t("workspace")}
          </label>
          <Select
            id="ws-switch"
            value={workspace.workspace_id}
            onChange={(e) => selectWorkspace(e.target.value)}
          >
            {me?.memberships.map((m) => (
              <option key={m.workspace_id} value={m.workspace_id}>
                {m.workspace_name}
              </option>
            ))}
          </Select>
          {role ? <Badge className="w-fit">{ROLE_LABELS[role]}</Badge> : null}
        </div>
        <nav aria-label={t("label")} className="px-2 pb-2">
          <ul className="flex gap-1 overflow-x-auto md:grid md:overflow-visible">
            {items.map(({ key, href, icon: Icon }) => {
              const active = href === "/app" ? pathname === "/app" : pathname.startsWith(href);
              return (
                <li key={key}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex min-h-11 items-center gap-3 rounded-md px-3 text-sm whitespace-nowrap hover:bg-accent",
                      active && "bg-accent font-medium text-accent-foreground",
                    )}
                  >
                    <Icon aria-hidden className="size-4" />
                    {t(key)}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="hidden gap-1 p-2 md:grid">
          <Button
            variant="ghost"
            className="justify-start"
            onClick={() => window.dispatchEvent(new Event("ms:open-command-bar"))}
          >
            <CommandIcon aria-hidden /> {t("commandHint")}
            <kbd className="ml-auto rounded border px-1.5 text-xs text-muted-foreground">/</kbd>
          </Button>
          <Button
            variant="ghost"
            className="justify-start"
            onClick={async () => {
              await signOut();
              router.replace("/sign-in");
            }}
          >
            <LogOut aria-hidden /> {t("signOut")}
          </Button>
        </div>
      </aside>
      <main id="main" tabIndex={-1} className="min-w-0 p-4 sm:p-8">
        {IS_MOCK_MODE ? (
          <div className="mb-6">
            <Notice>{tc("mockMode")}</Notice>
          </div>
        ) : null}
        {children}
      </main>
      <CommandBar role={role} />
    </div>
  );
}
