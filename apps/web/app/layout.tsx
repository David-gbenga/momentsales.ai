import type { Metadata, Viewport } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getMessages } from "next-intl/server";

import { Providers } from "@/components/providers";
import { LOCALE } from "@/i18n/request";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "momentsales.ai", template: "%s · momentsales.ai" },
  description:
    "Find what's about to sell, launch a shop in a weekend, and keep books your accountant will accept.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const messages = await getMessages();
  return (
    <html lang={LOCALE} suppressHydrationWarning>
      <body className="min-h-dvh antialiased">
        <NextIntlClientProvider locale={LOCALE} messages={messages}>
          <Providers>{children}</Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
