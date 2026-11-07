"use client";

/** Typed queries for the ledger, sales and payments APIs (keys are per workspace). */
import { can, type Action } from "@momentsales/shared";
import type { components } from "@momentsales/shared/api";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

type S = components["schemas"];
export type Spend = S["SpendOut"];
export type SpendPage = S["SpendPage"];
export type TouchPage = S["TouchPage"];
export type Receipt = S["ReceiptOut"];
export type ReceiptPage = S["ReceiptPage"];
export type LedgerPage = S["LedgerPage"];
export type Pnl = S["PnlOut"];
export type PnlLine = S["PnlLineOut"];
export type SalesSummary = S["SalesSummaryOut"];
export type OrderPage = S["OrderPage"];
export type Order = S["OrderOut"];
export type StripeStatus = S["StripeStatusOut"];
export type ExportOut = S["ExportOut"];
export type ImportPreview = S["PreviewOut"];
export type ImportResult = S["ImportOut"];

/** Current workspace id and a permission check, for query keys and UI gating. */
export function useWorkspace() {
  const { workspace, role } = useSession();
  const id = workspace?.workspace_id ?? null;
  return { id, role, can: (action: Action) => can(role, action) };
}

export function useLedgerQuery<T>(key: string, path: string, action: Action, refetchInterval?: number) {
  const ws = useWorkspace();
  return useQuery({
    queryKey: [key, ws.id, path],
    queryFn: () => api<T>(path),
    enabled: !!ws.id && ws.can(action),
    refetchInterval,
  });
}

/** After a money write, refresh every ledger-derived view. */
export function useRefreshLedger() {
  const qc = useQueryClient();
  return () =>
    Promise.all(
      ["spends", "touches", "receipts", "ledger", "pnl", "sales", "orders", "home", "exports"].map((key) =>
        qc.invalidateQueries({ queryKey: [key] }),
      ),
    );
}

/** Today's date in the UK as YYYY-MM-DD. */
export function ukToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London" }).format(new Date());
}

export function monthStart(day = ukToday()): string {
  return `${day.slice(0, 8)}01`;
}
