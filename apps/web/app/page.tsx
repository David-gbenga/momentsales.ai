import { CalendarClock, Truck, BookCheck } from "lucide-react";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { WaitlistForm } from "@/components/waitlist-form";

export default async function LandingPage() {
  const t = await getTranslations("landing");
  const points = [
    { icon: CalendarClock, text: t("points.rising") },
    { icon: Truck, text: t("points.local") },
    { icon: BookCheck, text: t("points.books") },
  ];
  return (
    <div className="mx-auto flex min-h-dvh max-w-5xl flex-col px-4 sm:px-6">
      <header className="flex items-center justify-between py-6">
        <span className="text-lg font-semibold tracking-tight">momentsales.ai</span>
        <nav aria-label="Site" className="flex items-center gap-4">
          <Link href="/pricing" className="text-sm font-medium underline-offset-4 hover:underline">
            {t("pricing")}
          </Link>
          <Link href="/sign-in" className="text-sm font-medium underline-offset-4 hover:underline">
            {t("signIn")}
          </Link>
        </nav>
      </header>
      <main id="main" className="grid flex-1 items-center gap-12 py-10 md:grid-cols-[1.2fr_1fr]">
        <section aria-labelledby="promise">
          <p className="text-sm font-medium text-primary">{t("eyebrow")}</p>
          <h1 id="promise" className="mt-3 text-3xl font-semibold tracking-tight text-balance sm:text-5xl">
            {t("promise")}
          </h1>
          <ul className="mt-8 grid gap-4">
            {points.map(({ icon: Icon, text }) => (
              <li key={text} className="flex gap-3">
                <Icon aria-hidden className="mt-0.5 size-5 shrink-0 text-primary" />
                <span className="text-muted-foreground">{text}</span>
              </li>
            ))}
          </ul>
        </section>
        <section aria-labelledby="waitlist-title" className="rounded-xl border bg-card p-6 shadow-sm">
          <h2 id="waitlist-title" className="text-xl font-semibold">
            {t("waitlistTitle")}
          </h2>
          <p className="mt-2 text-sm text-muted-foreground">{t("waitlistBody")}</p>
          <WaitlistForm />
          <p className="mt-4 text-xs text-muted-foreground">{t("privacy")}</p>
        </section>
      </main>
      <footer className="py-6 text-xs text-muted-foreground">© momentsales.ai · UK</footer>
    </div>
  );
}
