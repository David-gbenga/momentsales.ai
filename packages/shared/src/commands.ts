/**
 * Command-bar grammar for the FR-7 ledger commands.
 * Phase 0 ships the grammar and autocomplete only; each command's form and API arrive
 * in the phase listed in `availableIn`.
 */
import type { Action } from "./permissions";

export interface CommandField {
  name: string;
  label: string;
  required: boolean;
}

export interface CommandSpec {
  name: string;
  summary: string;
  fields: CommandField[];
  availableIn: string;
  permission: Action;
  /** Disabled commands are listed but cannot be run yet. */
  enabled: boolean;
}

const f = (name: string, label: string, required = true): CommandField => ({ name, label, required });

export const COMMANDS: readonly CommandSpec[] = [
  {
    name: "/stripe connect",
    summary: "Start Stripe Connect onboarding",
    fields: [],
    availableIn: "Phase 1",
    permission: "integrations.payments.manage",
    enabled: false,
  },
  {
    name: "/touch",
    summary: "Log a human intervention",
    fields: [
      f("what", "What was done"),
      f("category", "Category"),
      f("minutes", "Time spent (min)"),
      f("object", "Linked run, listing or order", false),
      f("note", "Note", false),
    ],
    availableIn: "Phase 1",
    permission: "ledger.write",
    enabled: false,
  },
  {
    name: "/spend",
    summary: "Log a business expense",
    fields: [
      f("amount", "Amount"),
      f("currency", "Currency"),
      f("date", "Date"),
      f("vendor", "Vendor"),
      f("category", "Category"),
      f("method", "Payment method"),
      f("receipt", "Receipt", false),
      f("vat", "VAT amount", false),
    ],
    availableIn: "Phase 1",
    permission: "ledger.write",
    enabled: false,
  },
  {
    name: "/receipt",
    summary: "Attach a receipt (PDF, JPG, PNG, HEIC up to 10 MB)",
    fields: [f("file", "File"), f("spend", "Link to an existing spend", false)],
    availableIn: "Phase 1",
    permission: "receipts.upload",
    enabled: false,
  },
  {
    name: "/sale",
    summary: "Record a sale made outside Stripe",
    fields: [f("amount", "Amount"), f("date", "Date"), f("channel", "Channel"), f("ref", "Order ref", false)],
    availableIn: "Phase 1",
    permission: "ledger.write",
    enabled: false,
  },
  {
    name: "/export",
    summary: "Build an audit pack",
    fields: [f("period", "Period"), f("format", "Format", false)],
    availableIn: "Phase 1",
    permission: "ledger.export",
    enabled: false,
  },
];

/** Commands whose name starts with what the user typed (leading "/" optional). */
export function matchCommands(input: string, commands: readonly CommandSpec[] = COMMANDS): CommandSpec[] {
  const q = input.trim().toLowerCase().replace(/^\/?/, "/");
  if (q === "/") return [...commands];
  const firstWord = q.split(" ")[0] ?? q;
  return commands.filter(
    (c) => c.name.startsWith(q) || (c.name.split(" ")[0] ?? c.name).startsWith(firstWord),
  );
}

/** Split "/spend 24.99 GBP shipping" into the command and the rest. */
export function parseCommand(input: string): { command: CommandSpec | null; args: string } {
  const text = input.trim();
  const sorted = [...COMMANDS].sort((a, b) => b.name.length - a.name.length);
  const command = sorted.find((c) => text === c.name || text.startsWith(`${c.name} `)) ?? null;
  return { command, args: command ? text.slice(command.name.length).trim() : text };
}
