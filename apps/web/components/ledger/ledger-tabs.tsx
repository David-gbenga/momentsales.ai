"use client";

import { formatMoney, parseSpendArgs, parseTouchArgs } from "@momentsales/shared";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Tabs } from "radix-ui";
import { useState } from "react";

import { CsvImport } from "@/components/ledger/csv-import";
import { ExportPanel } from "@/components/ledger/export-panel";
import { PnlView } from "@/components/ledger/pnl-view";
import { ReceiptUpload } from "@/components/ledger/receipt-upload";
import { SpendForm } from "@/components/ledger/spend-form";
import { TouchForm } from "@/components/ledger/touch-form";
import { Button } from "@/components/ui/button";
import { Badge, Card, Field, Input, Notice } from "@/components/ui/primitives";
import { DataTable, Num, Td } from "@/components/ui/table";
import { api, ApiError, newIdempotencyKey } from "@/lib/api";
import {
  useLedgerQuery,
  useRefreshLedger,
  useWorkspace,
  type LedgerPage,
  type ReceiptPage,
  type SpendPage,
  type TouchPage,
} from "@/lib/ledger";

const TABS = ["spends", "touches", "receipts", "entries", "pnl", "export"] as const;
type Tab = (typeof TABS)[number];

export function LedgerTabs() {
  const t = useTranslations("ledger");
  const tm = useTranslations("money");
  const ws = useWorkspace();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const requested = params.get("tab") as Tab | null;
  const tab: Tab = requested && TABS.includes(requested) ? requested : "spends";
  const args = params.get("args") ?? "";
  const visible = TABS.filter((name) => name !== "export" || ws.can("ledger.export"));

  return (
    <section aria-labelledby="ledger-title" className="grid gap-4">
      <h1 id="ledger-title" className="text-2xl font-semibold tracking-tight">
        {t("title")}
      </h1>
      {!ws.can("ledger.write") ? <Notice>{tm("readOnly")}</Notice> : null}
      <Tabs.Root value={tab} onValueChange={(v) => router.replace(`${pathname}?tab=${v}`)}>
        <Tabs.List aria-label={t("title")} className="flex flex-wrap gap-1 border-b">
          {visible.map((name) => (
            <Tabs.Trigger
              key={name}
              value={name}
              className="min-h-11 rounded-t-md px-3 text-sm font-medium text-muted-foreground data-[state=active]:border-b-2 data-[state=active]:border-primary data-[state=active]:text-foreground"
            >
              {t(`tabs.${name}`)}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="spends" className="grid gap-6 pt-4">
          {ws.can("ledger.write") ? (
            <Card className="grid gap-4">
              <h2 className="text-lg font-semibold">{t("newSpend")}</h2>
              <SpendForm key={args} draft={tab === "spends" ? parseSpendArgs(args) : {}} />
            </Card>
          ) : null}
          <SpendList />
          {ws.can("ledger.write") ? (
            <Card className="grid gap-4">
              <h2 className="text-lg font-semibold">{t("importCsv")}</h2>
              <CsvImport kind="spends" />
            </Card>
          ) : null}
        </Tabs.Content>
        <Tabs.Content value="touches" className="grid gap-6 pt-4">
          {ws.can("touches.write") ? (
            <Card className="grid gap-4">
              <h2 className="text-lg font-semibold">{t("newTouch")}</h2>
              <TouchForm key={args} draft={tab === "touches" ? parseTouchArgs(args) : {}} />
            </Card>
          ) : null}
          <TouchList />
        </Tabs.Content>
        <Tabs.Content value="receipts" className="grid gap-6 pt-4">
          {ws.can("receipts.upload") ? (
            <Card>
              <ReceiptUpload />
            </Card>
          ) : null}
          <ReceiptList />
        </Tabs.Content>
        <Tabs.Content value="entries" className="pt-4">
          <EntryList />
        </Tabs.Content>
        <Tabs.Content value="pnl" className="pt-4">
          <PnlView />
        </Tabs.Content>
        {ws.can("ledger.export") ? (
          <Tabs.Content value="export" className="pt-4">
            <ExportPanel />
          </Tabs.Content>
        ) : null}
      </Tabs.Root>
    </section>
  );
}

function SpendList() {
  const t = useTranslations("ledger");
  const tm = useTranslations("money");
  const ws = useWorkspace();
  const refresh = useRefreshLedger();
  const spends = useLedgerQuery<SpendPage>("spends", "/spends?limit=50", "ledger.read");
  const [voiding, setVoiding] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function doVoid(id: string) {
    setError(null);
    try {
      await api(`/spends/${id}/void`, { body: { reason }, headers: { "Idempotency-Key": newIdempotencyKey() } });
      setVoiding(null);
      setReason("");
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  const items = spends.data?.items ?? [];
  return (
    <div className="grid gap-3">
      {spends.data ? (
        <p className="text-sm">
          <Badge>
            {t("missingReceipt")}: {spends.data.missing_receipts}
          </Badge>
        </p>
      ) : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{tm("none")}</p>
      ) : (
        <DataTable
          caption={t("tabs.spends")}
          captionHidden
          head={[tm("date"), tm("vendor"), tm("category"), tm("amount"), tm("productKey"), t("source"), ""]}
        >
          {items.map((s) => (
            <tr key={s.id} className={s.voided ? "text-muted-foreground" : undefined}>
              <Td>{s.spent_on}</Td>
              <Td>{s.vendor}</Td>
              <Td>{tm(`categories.${s.category}`)}</Td>
              <Num>
                {formatMoney(s.amount_minor, s.currency)}
                {s.currency !== "GBP" ? ` (${formatMoney(s.base_amount_minor)})` : ""}
              </Num>
              <Td>{s.product_key ?? ""}</Td>
              <Td>{s.source}</Td>
              <Td>
                {s.voided ? (
                  <Badge>{t("voided")}</Badge>
                ) : s.receipt_id ? (
                  <Badge>{t("hasReceipt")}</Badge>
                ) : (
                  <Badge className="border-destructive/50 text-destructive">{t("missingReceipt")}</Badge>
                )}
                {!s.voided && ws.can("ledger.write") ? (
                  voiding === s.id ? (
                    <span className="mt-2 grid gap-2">
                      <Field id={`void-${s.id}`} label={t("voidReason")}>
                        <Input id={`void-${s.id}`} value={reason} onChange={(e) => setReason(e.target.value)} />
                      </Field>
                      <Button size="sm" variant="destructive" disabled={reason.trim().length < 3} onClick={() => void doVoid(s.id)}>
                        {t("voidConfirm")}
                      </Button>
                    </span>
                  ) : (
                    <Button size="sm" variant="ghost" onClick={() => setVoiding(s.id)}>
                      {t("void")}
                    </Button>
                  )
                ) : null}
              </Td>
            </tr>
          ))}
        </DataTable>
      )}
    </div>
  );
}

function TouchList() {
  const t = useTranslations("ledger");
  const tm = useTranslations("money");
  const touches = useLedgerQuery<TouchPage>("touches", "/touches?limit=50", "ledger.read");
  const s = touches.data?.summary;
  return (
    <div className="grid gap-3">
      {s ? (
        <p className="text-sm" role="status">
          {t("humanSummary", { manual: s.manual_count, minutes: s.manual_minutes, auto: s.auto_count })}
        </p>
      ) : null}
      {touches.data?.items.length ? (
        <DataTable caption={t("tabs.touches")} captionHidden head={[tm("date"), tm("category"), t("what"), t("minutes"), ""]}>
          {touches.data.items.map((x) => (
            <tr key={x.id}>
              <Td>{new Date(x.created_at).toLocaleString("en-GB")}</Td>
              <Td>{tm(`touchCategories.${x.category}`)}</Td>
              <Td>{x.description}</Td>
              <Num>{x.minutes}</Num>
              <Td>
                <Badge>{x.auto ? t("auto") : t("manual")}</Badge>
              </Td>
            </tr>
          ))}
        </DataTable>
      ) : (
        <p className="text-sm text-muted-foreground">{tm("none")}</p>
      )}
    </div>
  );
}

function ReceiptList() {
  const t = useTranslations("ledger");
  const tm = useTranslations("money");
  const receipts = useLedgerQuery<ReceiptPage>("receipts", "/receipts?limit=50", "ledger.read");
  const items = receipts.data?.items ?? [];
  async function open(id: string) {
    const r = await api<{ download_url: string }>(`/receipts/${id}`);
    window.open(r.download_url, "_blank", "noopener");
  }
  return items.length ? (
    <DataTable caption={t("tabs.receipts")} captionHidden head={[tm("date"), "File", "OCR", t("sha256"), ""]}>
      {items.map((r) => (
        <tr key={r.id}>
          <Td>{new Date(r.created_at).toLocaleDateString("en-GB")}</Td>
          <Td>{r.original_name}</Td>
          <Td>{r.ocr_status}</Td>
          <Td className="font-mono text-xs break-all">{r.sha256.slice(0, 16)}…</Td>
          <Td>
            <Button size="sm" variant="ghost" onClick={() => void open(r.id)}>
              {t("download")}
            </Button>
          </Td>
        </tr>
      ))}
    </DataTable>
  ) : (
    <p className="text-sm text-muted-foreground">{tm("none")}</p>
  );
}

function EntryList() {
  const t = useTranslations("ledger");
  const tm = useTranslations("money");
  const entries = useLedgerQuery<LedgerPage>("ledger", "/ledger?limit=100", "ledger.read");
  const items = entries.data?.items ?? [];
  return items.length ? (
    <DataTable caption={t("tabs.entries")} captionHidden head={[tm("date"), t("type"), t("description"), tm("amount"), tm("productKey")]}>
      {items.map((e) => (
        <tr key={e.id}>
          <Td>{e.occurred_on}</Td>
          <Td>{e.type}</Td>
          <Td>{e.description}</Td>
          <Num>{formatMoney(e.amount_minor, e.currency)}</Num>
          <Td>{e.product_key ?? ""}</Td>
        </tr>
      ))}
    </DataTable>
  ) : (
    <p className="text-sm text-muted-foreground">{tm("none")}</p>
  );
}
