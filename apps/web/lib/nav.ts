import type { Action } from "@momentsales/shared";
import {
  BarChart3,
  BookOpenText,
  CreditCard,
  FileText,
  Home,
  type LucideIcon,
  Plug,
  Search,
  Settings,
  ShoppingBag,
  Store,
  Tags,
} from "lucide-react";

export type SectionKey =
  | "home"
  | "research"
  | "reports"
  | "listings"
  | "store"
  | "sales"
  | "ledger"
  | "integrations"
  | "billing"
  | "settings";

export interface NavItem {
  key: SectionKey;
  href: string;
  icon: LucideIcon;
  /** Shown only to roles the API allows (the UI never shows what the API forbids). */
  permission: Action;
}

export const NAV: readonly NavItem[] = [
  { key: "home", href: "/app", icon: Home, permission: "workspace.read" },
  { key: "research", href: "/app/research", icon: Search, permission: "research.run" },
  { key: "reports", href: "/app/reports", icon: FileText, permission: "reports.read" },
  { key: "listings", href: "/app/listings", icon: Tags, permission: "listings.draft" },
  { key: "store", href: "/app/store", icon: Store, permission: "store.manage" },
  { key: "sales", href: "/app/sales", icon: ShoppingBag, permission: "orders.read" },
  { key: "ledger", href: "/app/ledger", icon: BookOpenText, permission: "ledger.read" },
  { key: "integrations", href: "/app/integrations", icon: Plug, permission: "workspace.read" },
  { key: "billing", href: "/app/billing", icon: CreditCard, permission: "billing.usage.read" },
  { key: "settings", href: "/app/settings", icon: Settings, permission: "workspace.read" },
];

export const PLACEHOLDER_SECTIONS = new Set<SectionKey>([
  "research",
  "reports",
  "listings",
  "store",
  "sales",
  "ledger",
  "integrations",
  "billing",
]);

export const HOME_ICON = BarChart3;
