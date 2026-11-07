"use client";

import { can, COMMANDS, matchCommands, type CommandSpec, type Role } from "@momentsales/shared";
import { Command } from "cmdk";
import { Lock } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/primitives";

function isTyping(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

/** Global command bar: "/" or Ctrl/⌘+K. Phase 0 lists FR-7 commands with autocomplete. */
export function CommandBar({ role }: { role: Role | null }) {
  const t = useTranslations("command");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<CommandSpec | null>(null);

  const changeOpen = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setSelected(null);
      setQuery("");
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        changeOpen(!open);
      } else if (e.key === "/" && !open && !isTyping(e.target)) {
        e.preventDefault();
        setQuery("/");
        setOpen(true);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("ms:open-command-bar", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("ms:open-command-bar", onOpen);
    };
  }, [open]);

  const matches = matchCommands(query || "/", COMMANDS);

  return (
    <Command.Dialog
      open={open}
      onOpenChange={changeOpen}
      label={t("title")}
      shouldFilter={false}
      overlayClassName="fixed inset-0 z-40 bg-black/40"
      contentClassName="fixed inset-x-4 top-[12vh] z-50 mx-auto max-w-xl overflow-hidden rounded-xl border bg-card text-card-foreground shadow-xl"
    >
      <Command.Input
        value={query}
        onValueChange={(v) => {
          setQuery(v);
          setSelected(null);
        }}
        placeholder={t("placeholder")}
        aria-label={t("placeholder")}
        className="w-full border-b bg-transparent px-4 py-3 text-base outline-none"
      />
      <Command.List className="max-h-[50vh] overflow-y-auto p-2">
        <Command.Empty className="p-4 text-sm text-muted-foreground">{t("empty")}</Command.Empty>
        {matches.map((cmd) => {
          const allowed = can(role, cmd.permission);
          return (
            <Command.Item
              key={cmd.name}
              value={cmd.name}
              onSelect={() => setSelected(cmd)}
              className="flex cursor-pointer items-center justify-between gap-3 rounded-md px-3 py-2.5 text-sm data-[selected=true]:bg-accent"
            >
              <span className="grid">
                <span className="font-mono font-medium">{cmd.name}</span>
                <span className="text-muted-foreground">{cmd.summary}</span>
              </span>
              <span className="flex items-center gap-2">
                {!allowed ? <Lock aria-label={t("notAllowed")} className="size-4" /> : null}
                <Badge>{t("comingIn", { phase: cmd.availableIn })}</Badge>
              </span>
            </Command.Item>
          );
        })}
      </Command.List>
      {selected ? (
        <div role="status" className="border-t p-4 text-sm">
          <p className="font-medium">
            <span className="font-mono">{selected.name}</span> —{" "}
            {t("comingIn", { phase: selected.availableIn })}
          </p>
          {!can(role, selected.permission) ? (
            <p className="mt-1 text-destructive">{t("notAllowed")}</p>
          ) : null}
          {selected.fields.length > 0 ? (
            <>
              <p className="mt-3 text-muted-foreground">{t("fields")}</p>
              <ul className="mt-1 list-inside list-disc">
                {selected.fields.map((f) => (
                  <li key={f.name}>
                    {f.label}
                    {f.required ? ` (${t("required")})` : ""}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </Command.Dialog>
  );
}
