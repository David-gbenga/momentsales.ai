"use client";

import { formatMoney, ZERO_DECIMAL, type SpendDraft } from "@momentsales/shared";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { SpendForm } from "@/components/ledger/spend-form";
import { Button } from "@/components/ui/button";
import { Card, Field, Input, Notice, Select } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useLedgerQuery, useRefreshLedger, type Receipt, type SpendPage } from "@/lib/ledger";

function toAmount(minor: number | null | undefined, currency: string): string | undefined {
  if (minor === null || minor === undefined) return undefined;
  const digits = ZERO_DECIMAL.has(currency) ? 0 : 2;
  return (minor / 10 ** digits).toFixed(digits);
}

/** OCR fields -> spend draft. Only values the provider actually read are filled in. */
function draftFrom(receipt: Receipt): SpendDraft {
  const ocr = (receipt.ocr ?? {}) as Record<string, string | number | null>;
  const currency = typeof ocr.currency === "string" ? ocr.currency : "GBP";
  return {
    vendor: typeof ocr.vendor === "string" ? ocr.vendor : undefined,
    spent_on: typeof ocr.date === "string" ? ocr.date : undefined,
    amount: toAmount(ocr.total_minor as number | null, currency),
    tax_amount: toAmount(ocr.tax_minor as number | null, currency),
    currency,
  };
}

export function ReceiptUpload({ spendId }: { spendId?: string }) {
  const t = useTranslations("ledger");
  const tm = useTranslations("money");
  const refresh = useRefreshLedger();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receiptId, setReceiptId] = useState<string | null>(null);
  const [linkTo, setLinkTo] = useState(spendId ?? "");
  const [done, setDone] = useState(false);

  const receipt = useQuery({
    queryKey: ["receipts", receiptId],
    queryFn: () => api<Receipt>(`/receipts/${receiptId}`),
    enabled: !!receiptId,
    refetchInterval: (q) => (q.state.data?.ocr_status === "pending" ? 1000 : false),
  });
  const missing = useLedgerQuery<SpendPage>("spends", "/spends?missing_receipt=true&limit=50", "ledger.read");

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    try {
      const created = await api<Receipt>("/receipts", { form });
      setReceiptId(created.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function link() {
    if (!receiptId || !linkTo) return;
    try {
      await api(`/receipts/${receiptId}/confirm`, { body: { spend_id: linkTo } });
      await refresh();
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  const r = receipt.data;
  return (
    <div className="grid gap-4">
      {!receiptId ? (
        <form onSubmit={upload} className="grid gap-3">
          <Field id="receipt-file" label={t("uploadReceipt")} help={t("uploadHelp")}>
            <Input
              id="receipt-file"
              type="file"
              accept="application/pdf,image/jpeg,image/png,image/heic,.heic"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </Field>
          <Button type="submit" disabled={!file || busy} className="w-fit">
            {busy ? t("uploading") : t("upload")}
          </Button>
        </form>
      ) : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
      {receiptId && !r ? <Notice>{t("reading")}</Notice> : null}
      {r ? (
        <Card className="grid gap-4">
          <p className="text-sm text-muted-foreground">
            {r.original_name} · {t("sha256")} <span className="font-mono break-all">{r.sha256}</span>
          </p>
          {done ? (
            <Notice tone="success">{tm("saved")}</Notice>
          ) : r.ocr_status === "pending" ? (
            <Notice>{t("reading")}</Notice>
          ) : (
            <>
              <Notice tone={r.ocr_status === "done" ? "info" : "error"}>
                {r.ocr_status === "done" ? t("ocrDone") : r.ocr_status === "skipped" ? t("ocrSkipped") : t("ocrFailed")}
              </Notice>
              {!spendId ? (
                <SpendForm
                  key={r.id}
                  draft={draftFrom(r)}
                  receiptId={r.id}
                  submitLabel={t("confirmReceipt")}
                  onSaved={() => setDone(true)}
                />
              ) : null}
              <div className="grid gap-2 border-t pt-4">
                <Field id="receipt-link" label={t("linkToSpend")}>
                  <Select id="receipt-link" value={linkTo} onChange={(e) => setLinkTo(e.target.value)}>
                    <option value="">—</option>
                    {(missing.data?.items ?? []).map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.spent_on} · {s.vendor} · {formatMoney(s.amount_minor, s.currency)}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Button type="button" variant="outline" className="w-fit" disabled={!linkTo} onClick={() => void link()}>
                  {t("attachReceipt")}
                </Button>
              </div>
            </>
          )}
        </Card>
      ) : null}
    </div>
  );
}
