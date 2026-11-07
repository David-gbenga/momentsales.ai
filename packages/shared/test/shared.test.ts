import { describe, expect, it } from "vitest";

import { COMMANDS, can, invitationSchema, matchCommands, parseCommand, waitlistSchema } from "../src";

describe("permissions", () => {
  it("mirrors the PRD role rules", () => {
    expect(can("owner", "billing.manage")).toBe(true);
    expect(can("admin", "billing.manage")).toBe(false);
    expect(can("analyst", "listings.publish")).toBe(false);
    expect(can("analyst", "payouts.read")).toBe(false);
    expect(can("accountant", "ledger.read")).toBe(true);
    expect(can("accountant", "ledger.write")).toBe(false);
    expect(can(null, "workspace.read")).toBe(false);
  });
});

describe("command grammar", () => {
  it("lists every FR-7 command", () => {
    expect(COMMANDS.map((c) => c.name)).toEqual([
      "/stripe connect",
      "/touch",
      "/spend",
      "/receipt",
      "/sale",
      "/export",
    ]);
  });

  it("autocompletes by prefix with or without the slash", () => {
    expect(matchCommands("/sp").map((c) => c.name)).toEqual(["/spend"]);
    expect(matchCommands("s").map((c) => c.name)).toEqual(["/stripe connect", "/spend", "/sale"]);
    expect(matchCommands("/")).toHaveLength(COMMANDS.length);
  });

  it("parses arguments", () => {
    const { command, args } = parseCommand('/spend 24.99 GBP shipping vendor:"Royal Mail"');
    expect(command?.name).toBe("/spend");
    expect(args).toBe('24.99 GBP shipping vendor:"Royal Mail"');
    expect(parseCommand("/stripe connect").command?.name).toBe("/stripe connect");
    expect(parseCommand("/unknown x").command).toBeNull();
  });
});

describe("schemas", () => {
  it("requires waitlist consent", () => {
    expect(waitlistSchema.safeParse({ email: "a@example.com", consent: true }).success).toBe(true);
    expect(waitlistSchema.safeParse({ email: "a@example.com", consent: false }).success).toBe(false);
    expect(waitlistSchema.safeParse({ email: "nope", consent: true }).success).toBe(false);
  });

  it("never invites an owner", () => {
    expect(invitationSchema.safeParse({ email: "a@example.com", role: "owner" }).success).toBe(false);
    expect(invitationSchema.safeParse({ email: "a@example.com", role: "analyst" }).success).toBe(true);
  });
});
