/**
 * Ledger helpers shared by the web app (and later mobile): money formatting, command-bar
 * argument parsing for /spend, /touch and /sale, and the form schemas.
 *
 * Parsing only pre-fills a form; nothing is saved until the user confirms it.
 */
import { z } from "zod";

export const BASE_CURRENCY = "GBP";
export const CURRENCIES = ["GBP", "EUR", "USD", "AUD", "CAD", "NZD", "CHF", "SEK", "NOK", "DKK", "PLN", "CNY", "HKD", "SGD", "JPY"] as const;
export const ZERO_DECIMAL = new Set(["JPY"]);
export const SPEND_CATEGORIES = ["stock", "ads", "software", "shipping", "fees", "other"] as const;
export const TOUCH_CATEGORIES = [
  "research_edit",
  "listing_edit",
  "pricing",
  "fulfilment",
  "customer_service",
  "agent_override",
] as const;
export type SpendCategory = (typeof SPEND_CATEGORIES)[number];
export type TouchCategory = (typeof TOUCH_CATEGORIES)[number];

/** 2499 GBP -> "£24.99" (en-GB). Minor units in, display string out. */
export function formatMoney(minor: number, currency: string = BASE_CURRENCY, locale = "en-GB"): string {
  const digits = ZERO_DECIMAL.has(currency) ? 0 : 2;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(minor / 10 ** digits);
}

export const amountPattern = /^\d{1,12}(\.\d{1,4})?$/;
export const productKeyPattern = /^[a-z0-9][a-z0-9-]{0,63}$/;
const isoDate = /^\d{4}-\d{2}-\d{2}$/;

/** "a b:c "d e"" -> ["a", "b:c", "d e"] (double or curly quotes group words). */
export function tokenize(text: string): string[] {
  const tokens: string[] = [];
  const re = /"([^"]*)"|“([^”]*)”|(\S+)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    tokens.push(match[1] ?? match[2] ?? match[3] ?? "");
  }
  return tokens;
}

/** Split `key:value` pairs (values may continue over following plain words until the next
 * key) from positional words. "vendor:Royal Mail ref:5" -> {vendor: "Royal Mail", ref: "5"} */
function splitKeyed(tokens: string[]): { positional: string[]; keyed: Record<string, string> } {
  const positional: string[] = [];
  const keyed: Record<string, string> = {};
  let current: string | null = null;
  for (const token of tokens) {
    const m = /^([a-z_]+):(.*)$/i.exec(token);
    if (m?.[1]) {
      current = m[1].toLowerCase();
      keyed[current] = m[2] ?? "";
    } else if (current !== null && !amountPattern.test(token)) {
      keyed[current] = `${keyed[current] ?? ""} ${token}`.trim();
    } else {
      current = null;
      positional.push(token);
    }
  }
  return { positional, keyed };
}

export interface SpendDraft {
  amount?: string;
  currency?: string;
  category?: SpendCategory;
  vendor?: string;
  spent_on?: string;
  payment_method?: string;
  product_key?: string;
  tax_amount?: string;
  note?: string;
}

/** `/spend 24.99 GBP shipping vendor:Royal Mail` -> a spend draft (AC-8). */
export function parseSpendArgs(args: string): SpendDraft {
  const { positional, keyed } = splitKeyed(tokenize(args));
  const draft: SpendDraft = {};
  for (const word of positional) {
    const upper = word.toUpperCase();
    const lower = word.toLowerCase();
    if (!draft.amount && amountPattern.test(word.replace(/^[£$€]/, ""))) {
      draft.amount = word.replace(/^[£$€]/, "");
      if (word.startsWith("£")) draft.currency = "GBP";
      if (word.startsWith("€")) draft.currency = "EUR";
      if (word.startsWith("$")) draft.currency = "USD";
    } else if ((CURRENCIES as readonly string[]).includes(upper)) {
      draft.currency = upper;
    } else if ((SPEND_CATEGORIES as readonly string[]).includes(lower)) {
      draft.category = lower as SpendCategory;
    } else if (isoDate.test(word)) {
      draft.spent_on = word;
    }
  }
  if (keyed.vendor) draft.vendor = keyed.vendor;
  if (keyed.date && isoDate.test(keyed.date)) draft.spent_on = keyed.date;
  if (keyed.method) draft.payment_method = keyed.method;
  if (keyed.product) draft.product_key = keyed.product.toLowerCase();
  if (keyed.listing) draft.product_key = keyed.listing.toLowerCase();
  if (keyed.vat && amountPattern.test(keyed.vat)) draft.tax_amount = keyed.vat;
  if (keyed.note) draft.note = keyed.note;
  return draft;
}

export interface TouchDraft {
  category?: TouchCategory;
  minutes?: number;
  description?: string;
}

const CATEGORY_ALIASES: Record<string, TouchCategory> = {
  research: "research_edit",
  listing: "listing_edit",
  pricing: "pricing",
  fulfilment: "fulfilment",
  fulfillment: "fulfilment",
  service: "customer_service",
  customer: "customer_service",
  override: "agent_override",
};

/** `/touch pricing 10m "adjusted margin on hoodie"` -> a touch draft (AC-9). */
export function parseTouchArgs(args: string): TouchDraft {
  const draft: TouchDraft = {};
  const rest: string[] = [];
  for (const word of tokenize(args)) {
    const lower = word.toLowerCase();
    const minutes = /^(\d{1,4})\s*(m|min|mins|minutes)?$/.exec(lower);
    const hours = /^(\d{1,2})h$/.exec(lower);
    if (!draft.category && ((TOUCH_CATEGORIES as readonly string[]).includes(lower) || lower in CATEGORY_ALIASES)) {
      draft.category = (CATEGORY_ALIASES[lower] ?? lower) as TouchCategory;
    } else if (draft.minutes === undefined && minutes && (minutes[2] || rest.length === 0)) {
      draft.minutes = Number(minutes[1]);
    } else if (draft.minutes === undefined && hours) {
      draft.minutes = Number(hours[1]) * 60;
    } else {
      rest.push(word);
    }
  }
  if (rest.length) draft.description = rest.join(" ");
  return draft;
}

export interface SaleDraft {
  amount?: string;
  sold_on?: string;
  channel?: string;
  order_ref?: string;
  description?: string;
  product_key?: string;
}

/** `/sale 30 2026-11-06 Etsy ref:1234` -> a sale draft. */
export function parseSaleArgs(args: string): SaleDraft {
  const { positional, keyed } = splitKeyed(tokenize(args));
  const draft: SaleDraft = {};
  const words: string[] = [];
  for (const word of positional) {
    const bare = word.replace(/^£/, "");
    if (!draft.amount && amountPattern.test(bare)) draft.amount = bare;
    else if (!draft.sold_on && isoDate.test(word)) draft.sold_on = word;
    else words.push(word);
  }
  if (words.length) draft.channel = words.join(" ");
  if (keyed.channel) draft.channel = keyed.channel;
  if (keyed.ref) draft.order_ref = keyed.ref;
  if (keyed.item) draft.description = keyed.item;
  if (keyed.product) draft.product_key = keyed.product.toLowerCase();
  return draft;
}

// --- Form schemas (mirror the API's validation) -------------------------------------------

const amount = z.string().trim().regex(amountPattern, "Enter an amount like 24.99");
const optionalAmount = z.union([z.literal(""), amount]).optional();
const productKey = z
  .string()
  .trim()
  .toLowerCase()
  .refine((v) => v === "" || productKeyPattern.test(v), "Use lowercase letters, numbers and dashes")
  .optional();

export const spendFormSchema = z
  .object({
    amount,
    currency: z.enum(CURRENCIES),
    base_amount: optionalAmount,
    tax_amount: optionalAmount,
    spent_on: z.string().regex(isoDate, "Choose a date"),
    vendor: z.string().trim().min(1, "Who did you pay?").max(255),
    category: z.enum(SPEND_CATEGORIES),
    payment_method: z.string().trim().min(1, "How did you pay?").max(50),
    product_key: productKey,
    note: z.string().max(2000).optional(),
  })
  .superRefine((v, ctx) => {
    if (Number(v.amount) <= 0) ctx.addIssue({ code: "custom", path: ["amount"], message: "Must be more than zero" });
    if (v.currency !== BASE_CURRENCY && !v.base_amount)
      ctx.addIssue({
        code: "custom",
        path: ["base_amount"],
        message: "Enter the amount in GBP shown on your statement",
      });
    if (v.category === "ads" && !v.product_key)
      ctx.addIssue({ code: "custom", path: ["product_key"], message: "Ad spend needs the product it promoted" });
  });
export type SpendFormInput = z.infer<typeof spendFormSchema>;

export const touchFormSchema = z.object({
  category: z.enum(TOUCH_CATEGORIES),
  minutes: z.coerce.number().int().min(0).max(1440),
  description: z.string().trim().min(1, "What did you do?").max(500),
  note: z.string().max(1000).optional(),
});
export type TouchFormInput = z.infer<typeof touchFormSchema>;

export const saleItemSchema = z.object({
  description: z.string().trim().min(1, "What was sold?").max(255),
  quantity: z.coerce.number().int().min(1).max(10000),
  unit_amount: amount,
  product_key: productKey,
});

export const saleFormSchema = z.object({
  sold_on: z.string().regex(isoDate, "Choose a date"),
  channel: z.string().trim().min(1, "Where was it sold?").max(50),
  order_ref: z.string().max(255).optional(),
  item: saleItemSchema,
});
export type SaleFormInput = z.infer<typeof saleFormSchema>;

export const testCheckoutSchema = z.object({ item: saleItemSchema });
export type TestCheckoutInput = z.infer<typeof testCheckoutSchema>;

/** Strip empty optional strings before sending to the API. */
export function compact<T extends Record<string, unknown>>(values: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(values).filter(([, v]) => v !== "" && v !== undefined && v !== null),
  ) as Partial<T>;
}
