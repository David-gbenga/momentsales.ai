import { notFound } from "next/navigation";

import { EmptyState } from "@/components/sections/empty-state";
import { PLACEHOLDER_SECTIONS, type SectionKey } from "@/lib/nav";

export default async function SectionPage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  if (!PLACEHOLDER_SECTIONS.has(section as SectionKey)) notFound();
  return <EmptyState section={section as Exclude<SectionKey, "settings">} />;
}
