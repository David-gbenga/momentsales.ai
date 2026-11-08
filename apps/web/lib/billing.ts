"use client";

/** Billing queries (FR-10). Every member can read the subscription (banners, gate);
 * usage needs billing.usage.read and invoices billing.invoices.read. */
import {
  FULL_ACCESS_STATUSES,
  NO_PLAN_STATUSES,
  READ_ONLY_STATUSES,
} from "@momentsales/shared";
import type { components } from "@momentsales/shared/api";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/ledger";

type S = components["schemas"];
export type Subscription = S["SubscriptionOut"];
export type Usage = S["UsageOut"];
export type Invoice = S["InvoiceOut"];
export type Catalog = S["CatalogOut"];
export type CatalogPlan = S["PlanOut"];

/** `pollUntilActive`: poll every second until the plan is usable (after Checkout). */
export function useSubscription(pollUntilActive = false) {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["billing", "subscription", ws.id],
    queryFn: () => api<Subscription>("/billing/subscription"),
    enabled: !!ws.id,
    refetchInterval: (q) => (pollUntilActive && !hasFullAccess(q.state.data?.status) ? 1000 : false),
  });
}

export function useUsage() {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["billing", "usage", ws.id],
    queryFn: () => api<Usage>("/billing/usage"),
    enabled: !!ws.id && ws.can("billing.usage.read"),
  });
}

export function useInvoices() {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["billing", "invoices", ws.id],
    queryFn: () => api<Invoice[]>("/billing/invoices"),
    enabled: !!ws.id && ws.can("billing.invoices.read"),
  });
}

export function useCatalog() {
  return useQuery({
    queryKey: ["billing", "plans"],
    queryFn: () => api<Catalog>("/plans", { auth: false, workspace: false }),
    staleTime: 5 * 60_000,
  });
}

export function useRefreshBilling() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["billing"] });
}

export const hasFullAccess = (status: string | undefined) =>
  !!status && FULL_ACCESS_STATUSES.has(status);
export const isReadOnly = (status: string | undefined) =>
  !!status && READ_ONLY_STATUSES.has(status);
export const needsPlan = (status: string | undefined) =>
  !!status && NO_PLAN_STATUSES.has(status);

/** The 402 body when a plan limit is hit (problem+json extras). */
export interface PlanLimit {
  limit: "research_runs" | "live_listings" | "seats";
  used: number;
  allowed: number;
  upgrade_url: string;
}

export function planLimitOf(err: unknown): PlanLimit | null {
  if (
    !(err instanceof ApiError) ||
    err.type !== "plan_limit_reached" ||
    !err.problem
  )
    return null;
  const p = err.problem as unknown as Partial<PlanLimit>;
  if (!p.limit || p.allowed === undefined) return null;
  return {
    limit: p.limit,
    used: p.used ?? 0,
    allowed: p.allowed,
    upgrade_url: p.upgrade_url ?? "/app/billing",
  };
}
