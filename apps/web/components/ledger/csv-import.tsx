"use client";

import { SPEND_CATEGORIES } from "@momentsales/shared";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, Input, Notice, Select } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useRefreshLedger, type ImportPreview, type ImportResult } from "@/lib/ledger";

/** Upload a CSV, match its columns to our fields, then import. Used for spends (e.g. ad
 * platform exports) and for sales made on other channels. */
export function CsvImport({ kind }: { kind: "spends" | "sales" }) {
  const t = useTranslations("ledger");
  const tm = useTranslations("money");
  const refresh = useRefreshLedger();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [defaults, setDefaults] = useState({ category: "ads", vendor: "", product_key: "", channel: "" });
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    try {
      const p = await api<ImportPreview>(`/${kind}/import-csv`, { form });
      setPreview(p);
      setMapping(p.suggested_mapping);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    const relevant = kind === "spends" ? ["category", "vendor", "product_key"] : ["channel", "product_key"];
    const d = Object.fromEntries(Object.entries(defaults).filter(([k, v]) => v && relevant.includes(k)));
    try {
      const r = await api<ImportResult>(`/imports/${preview.import_id}/confirm`, {
        body: { mapping: Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)), defaults: d },
      });
      setResult(r);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const id = `import-${kind}`;
  return (
    <div className="grid gap-4">
      {!preview ? (
        <form onSubmit={upload} className="grid gap-3">
          <Field id={`${id}-file`} label="CSV" help={t("importHelp")}>
            <Input id={`${id}-file`} type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </Field>
          <Button type="submit" variant="outline" disabled={!file || busy} className="w-fit">
            {t("upload")}
          </Button>
        </form>
      ) : result ? (
        <div className="grid gap-2">
          <Notice tone="success">{t("imported", { imported: result.rows_imported, skipped: result.rows_skipped })}</Notice>
          {result.errors.length ? (
            <ul className="list-inside list-disc text-sm text-destructive">
              {result.errors.slice(0, 20).map((e) => (
                <li key={String(e.row)}>{t("rowError", { row: String(e.row), error: String(e.error) })}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : (
        <fieldset className="grid gap-3">
          <legend className="font-medium">
            {t("mapColumns")} · {preview.filename} ({preview.rows_total})
          </legend>
          <div className="grid gap-3 sm:grid-cols-2">
            {preview.fields.map((field) => (
              <Field key={field} id={`${id}-${field}`} label={`${field}${preview.required.includes(field) ? " *" : ""}`}>
                <Select
                  id={`${id}-${field}`}
                  value={mapping[field] ?? ""}
                  onChange={(e) => setMapping({ ...mapping, [field]: e.target.value })}
                >
                  <option value="">{t("notMapped")}</option>
                  {preview.headers.map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </Select>
              </Field>
            ))}
          </div>
          <p className="text-sm font-medium">{t("defaults")}</p>
          <div className="grid gap-3 sm:grid-cols-3">
            {kind === "spends" ? (
              <>
                <Field id={`${id}-d-category`} label={tm("category")}>
                  <Select
                    id={`${id}-d-category`}
                    value={defaults.category}
                    onChange={(e) => setDefaults({ ...defaults, category: e.target.value })}
                  >
                    {SPEND_CATEGORIES.map((c) => (
                      <option key={c} value={c}>
                        {tm(`categories.${c}`)}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field id={`${id}-d-vendor`} label={tm("vendor")}>
                  <Input id={`${id}-d-vendor`} value={defaults.vendor} onChange={(e) => setDefaults({ ...defaults, vendor: e.target.value })} />
                </Field>
              </>
            ) : (
              <Field id={`${id}-d-channel`} label="Channel">
                <Input id={`${id}-d-channel`} value={defaults.channel} onChange={(e) => setDefaults({ ...defaults, channel: e.target.value })} />
              </Field>
            )}
            <Field id={`${id}-d-product`} label={tm("productKey")}>
              <Input
                id={`${id}-d-product`}
                value={defaults.product_key}
                onChange={(e) => setDefaults({ ...defaults, product_key: e.target.value.toLowerCase() })}
              />
            </Field>
          </div>
          <Button type="button" disabled={busy} className="w-fit" onClick={() => void run()}>
            {t("runImport")}
          </Button>
        </fieldset>
      )}
      {error ? <Notice tone="error">{error}</Notice> : null}
    </div>
  );
}
