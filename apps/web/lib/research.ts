"use client";

/** Research runs, season calendar and audience profile (Phase 2, ADR-0018). */
import type { components } from "@momentsales/shared/api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api, tokenStore } from "@/lib/api";
import { useWorkspace } from "@/lib/ledger";

type S = components["schemas"];
export type Run = S["RunOut"];
export type RunCreate = S["RunCreate"];
export type RunPage = S["RunPage"];
export type Calendar = S["CalendarOut"];
export type Moment = S["MomentOut"];
export type Audience = S["AudienceOut"];
export type AudienceIn = S["AudienceIn"];

export const TERMINAL: ReadonlySet<Run["status"]> = new Set(["completed", "partial", "failed"]);
export const SEASONS = ["spring", "summer", "autumn", "winter"] as const;

/** Season agent output stored on the run (`run.season`). */
export interface SeasonResult {
  current: string;
  next: string;
  detected: string;
  overridden: boolean;
  window_weeks: number;
  window_end: string;
  assumed_delivery_days: number;
  moments: Moment[];
  /** null = the holidays provider was unavailable ("not available"). */
  public_holidays: { date: string; name: string; snapshot_id: string }[] | null;
  sources: { provider: string; url: string; fetched_at: string; snapshot_id: string }[];
}

export function seasonOf(run: Run | undefined): SeasonResult | null {
  return (run?.season as SeasonResult | null | undefined) ?? null;
}

export function useRuns() {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["research", "runs", ws.id],
    queryFn: () => api<RunPage>("/research-runs?limit=10"),
    enabled: !!ws.id && ws.can("reports.read"),
  });
}

/** Parse a text/event-stream body and call `onRun` for each `run` event. */
async function readEvents(body: ReadableStream<Uint8Array>, onRun: (run: Run) => void) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const lines = block.split("\n");
      const event = lines.find((l) => l.startsWith("event: "))?.slice(7);
      const data = lines.find((l) => l.startsWith("data: "))?.slice(6);
      if (event === "run" && data) onRun(JSON.parse(data) as Run);
      if (event === "end") return;
      cut = buffer.indexOf("\n\n");
    }
  }
}

/** A run with live progress: Server-Sent Events while it runs, with polling as the
 * fallback (the stream needs the bearer token, so it uses fetch, not EventSource). */
export function useRun(runId: string | null) {
  const ws = useWorkspace();
  const qc = useQueryClient();
  const key = ["research", "run", ws.id, runId];
  const query = useQuery({
    queryKey: key,
    queryFn: () => api<Run>(`/research-runs/${runId}`),
    enabled: !!ws.id && !!runId && ws.can("reports.read"),
    refetchInterval: (q) => (q.state.data && TERMINAL.has(q.state.data.status) ? false : 3000),
  });
  const finished = !!query.data && TERMINAL.has(query.data.status);

  useEffect(() => {
    if (!runId || !ws.id || finished) return;
    const controller = new AbortController();
    const headers: Record<string, string> = { "X-Requested-With": "momentsales", "X-Workspace-Id": ws.id };
    const token = tokenStore.get();
    if (token) headers.Authorization = `Bearer ${token}`;
    fetch(`/api/v1/research-runs/${runId}/events`, { headers, signal: controller.signal, credentials: "same-origin" })
      .then(async (res) => {
        if (!res.ok || !res.body) return;
        await readEvents(res.body, (run) => {
          qc.setQueryData(["research", "run", ws.id, runId], run);
        });
        await qc.invalidateQueries({ queryKey: ["research", "runs", ws.id] });
      })
      .catch(() => undefined); // polling carries on
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restart only when the run changes
  }, [runId, ws.id, finished]);

  return query;
}

export function useSeasonCalendar() {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["research", "calendar", ws.id],
    queryFn: () => api<Calendar>("/season-calendar"),
    enabled: !!ws.id,
    staleTime: 60 * 60 * 1000,
  });
}

export function useAudience() {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["research", "audience", ws.id],
    queryFn: () => api<Audience>("/audience-profile"),
    enabled: !!ws.id && ws.can("reports.read"),
  });
}

/** "2026-12-25" -> "Fri, 25 Dec 2026" (UK format, no timezone shift). */
export function formatDay(iso: string): string {
  const [y = 1970, m = 1, d = 1] = iso.split("-").map(Number);
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(y, m - 1, d)));
}
