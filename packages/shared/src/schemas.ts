/** Zod schemas for forms; they mirror the API's request models (the API re-validates). */
import { z } from "zod";

import { ROLES } from "./permissions";

export const emailSchema = z.email({ message: "Enter a valid email address" }).max(254);

export const waitlistSchema = z.object({
  email: emailSchema,
  consent: z.literal(true, { message: "Tick the box to join the waitlist" }),
});
export type WaitlistInput = z.infer<typeof waitlistSchema>;

export const magicLinkSchema = z.object({ email: emailSchema });

export const totpCodeSchema = z.object({
  code: z.string().regex(/^\d{6}$/, { message: "Enter the 6-digit code" }),
});

export const workspaceSchema = z.object({
  name: z.string().trim().min(2, { message: "At least 2 characters" }).max(120),
  legal_name: z.string().trim().max(200).optional().or(z.literal("")),
});
export type WorkspaceInput = z.infer<typeof workspaceSchema>;

export const invitableRoles = ROLES.filter((r) => r !== "owner");

export const invitationSchema = z.object({
  email: emailSchema,
  role: z.enum(["admin", "analyst", "accountant"]),
});
export type InvitationInput = z.infer<typeof invitationSchema>;

/** RFC 9457 problem+json as returned by the API. */
export const problemSchema = z
  .object({
    type: z.string(),
    title: z.string(),
    status: z.number(),
    detail: z.string().optional(),
  })
  .passthrough();
export type Problem = z.infer<typeof problemSchema>;
