"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Badge, Card, Notice } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useLedgerQuery, useWorkspace } from "@/lib/ledger";

interface AvasamStatus {
  connected: boolean;
  status: "active" | "disconnected" | null;
  connected_at: string | null;
  mode: "mock" | "live" | null;
}

/** A seller's own Avasam account (ADR-0020). Keys are write-only: never shown again. */
export function AvasamCard() {
  const t = useTranslations("integrations");
  const ws = useWorkspace();
  const qc = useQueryClient();
  const status = useLedgerQuery<AvasamStatus>("avasam", "/integrations/avasam", "workspace.read");
  const canManage = ws.can("integrations.suppliers.manage");
  const [consumerKey, setConsumerKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = ["avasam", ws.id, "/integrations/avasam"];

  async function connect(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const data = await api<AvasamStatus>("/integrations/avasam/connect", {
        method: "POST",
        body: { consumer_key: consumerKey, secret_key: secretKey },
      });
      qc.setQueryData(key, data);
      setConsumerKey("");
      setSecretKey("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      await api<void>("/integrations/avasam", { method: "DELETE" });
      qc.setQueryData(key, { connected: false, status: "disconnected", connected_at: null, mode: null });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const s = status.data;
  return (
    <Card className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{t("avasam")}</h2>
        <Badge data-testid="avasam-status">{t(`status.${s?.connected ? "active" : "none"}`)}</Badge>
      </div>
      <p className="text-sm text-muted-foreground">{t("avasamHelp")}</p>
      {s?.mode === "mock" ? <Notice>{t("avasamMock")}</Notice> : null}
      {!canManage ? (
        <p className="text-sm text-muted-foreground">{t("avasamManageOnly")}</p>
      ) : s?.connected ? (
        <div>
          <Button variant="outline" onClick={() => void disconnect()} disabled={busy}>
            {t("avasamDisconnect")}
          </Button>
        </div>
      ) : (
        <form className="grid gap-3" onSubmit={(e) => void connect(e)}>
          <label className="grid gap-1 text-sm">
            {t("consumerKey")}
            <input
              className="rounded-md border bg-background px-3 py-2"
              value={consumerKey}
              onChange={(e) => setConsumerKey(e.target.value)}
              autoComplete="off"
              required
              maxLength={200}
            />
          </label>
          <label className="grid gap-1 text-sm">
            {t("secretKey")}
            <input
              className="rounded-md border bg-background px-3 py-2"
              type="password"
              value={secretKey}
              onChange={(e) => setSecretKey(e.target.value)}
              autoComplete="off"
              required
              maxLength={200}
            />
          </label>
          <div>
            <Button type="submit" disabled={busy}>
              {t("avasamConnect")}
            </Button>
          </div>
        </form>
      )}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </Card>
  );
}
