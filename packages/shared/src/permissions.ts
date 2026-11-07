/**
 * Role → action matrix, generated from the API (apps/api/.../tenancy/permissions.py).
 * The API enforces it; the UI uses it only to hide or lock controls the API forbids.
 */
import matrix from "./generated/permissions.json";

export const ROLES = ["owner", "admin", "analyst", "accountant"] as const;
export type Role = (typeof ROLES)[number];

export type Action = keyof typeof matrix;

const table = matrix as Record<Action, readonly string[]>;

export function can(role: Role | null | undefined, action: Action): boolean {
  if (!role) return false;
  return table[action]?.includes(role) ?? false;
}

export const ROLE_LABELS: Record<Role, string> = {
  owner: "Owner",
  admin: "Admin",
  analyst: "Analyst",
  accountant: "Accountant",
};

export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  owner: "Everything, including billing, payments and team.",
  admin: "Runs research, listings, store and the ledger. No billing or team changes.",
  analyst: "Runs research and drafts listings. Can't publish or see payouts.",
  accountant: "Read-only access to the ledger, receipts, sales and exports.",
};
