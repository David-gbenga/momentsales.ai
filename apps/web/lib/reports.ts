"use client";

import type { components } from "@momentsales/shared/api";
import { formatMoney } from "@momentsales/shared";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/ledger";

type S = components["schemas"];
export type Report = S["ReportOut"];
export type PublicReport = S["PublicReportOut"];
export type ReportItem = S["ReportItemOut"];
export type ReportListItem = S["ReportListItem"];
export type Share = S["ShareOut"];
export type ShareCreated = S["ShareCreatedOut"];

/** Loosely typed JSON parts of an item (stored as the agents wrote them). */
interface Offer {
  title?: string;
  merchant?: string | null;
  price_minor?: number | null;
  currency?: string | null;
  url?: string | null;
}

export function offersOf(item: ReportItem): Offer[] {
  return (item.offers as Offer[]).filter((o) => !!o.url);
}

export function bestPrice(item: ReportItem): { price: string; platform: string | null } | null {
  const best = item.best_offer as Offer | null;
  if (!best || best.price_minor == null || !best.currency) return null;
  return { price: formatMoney(best.price_minor, best.currency), platform: best.merchant ?? null };
}

export function priceRange(item: ReportItem): string | null {
  const m = item.market as { price_min_minor?: number | null; price_max_minor?: number | null; currency?: string | null } | null;
  if (!m || m.price_min_minor == null || m.price_max_minor == null || !m.currency) return null;
  if (m.price_min_minor === m.price_max_minor) return formatMoney(m.price_min_minor, m.currency);
  return `${formatMoney(m.price_min_minor, m.currency)}–${formatMoney(m.price_max_minor, m.currency)}`;
}

interface Match {
  provider?: string;
  delivery_min_days?: number | null;
  delivery_max_days?: number | null;
  warehouse_country?: string | null;
  landed_cost_minor?: number | null;
}

export function supplierOf(item: ReportItem): { status: string; match: Match | null } {
  const s = item.supplier as { status?: string; match?: Match | null } | null;
  return { status: s?.status ?? "not_checked", match: s?.match ?? null };
}

export function profitOf(item: ReportItem): { profit: string; breakEven: string } | null {
  const p = item.profit as { profit_minor?: number; break_even_ad_cost_minor?: number } | null;
  if (!p || p.profit_minor == null || p.break_even_ad_cost_minor == null) return null;
  return { profit: formatMoney(p.profit_minor, "GBP"), breakEven: formatMoney(p.break_even_ad_cost_minor, "GBP") };
}

export function growthPct(item: ReportItem): number | null {
  const g = (item.trend as { growth_rate?: number | null } | null)?.growth_rate;
  return g == null ? null : Math.round(g * 100);
}

export function competitionOf(item: ReportItem): number | null {
  const c = (item.market as { competition_score?: number | null } | null)?.competition_score;
  return c ?? null;
}

export function complianceOf(item: ReportItem): { status: string; documents: string[] } {
  const c = item.compliance as { status?: string; required_documents?: string[] };
  return { status: c.status ?? "not_checked", documents: c.required_documents ?? [] };
}

export function useReport(id: string) {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["report", ws.id, id],
    queryFn: () => api<Report>(`/reports/${id}`),
    enabled: !!ws.id && ws.can("reports.read"),
  });
}

export function useReports() {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["reports", ws.id],
    queryFn: () => api<ReportListItem[]>("/reports"),
    enabled: !!ws.id && ws.can("reports.read"),
  });
}

export function useShares(reportId: string, enabled: boolean) {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["shares", ws.id, reportId],
    queryFn: () => api<Share[]>(`/reports/${reportId}/shares`),
    enabled: enabled && !!ws.id,
  });
}
