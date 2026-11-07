"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  can,
  invitableRoles,
  invitationSchema,
  ROLE_DESCRIPTIONS,
  ROLE_LABELS,
  ROLES,
  type InvitationInput,
  type Role,
} from "@momentsales/shared";
import type { components } from "@momentsales/shared/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Card, Field, Input, Notice, Select } from "@/components/ui/primitives";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";

type Member = components["schemas"]["MemberOut"];
type Invitation = components["schemas"]["InvitationOut"];

export function TeamSettings() {
  const t = useTranslations("settings");
  const tc = useTranslations("common");
  const { workspace, role, me } = useSession();
  const qc = useQueryClient();
  const wsId = workspace?.workspace_id ?? null;
  const canRead = can(role, "members.read");
  const canInvite = can(role, "members.invite");
  const canChange = can(role, "members.change_role");
  const canRemove = can(role, "members.remove");
  const [notice, setNotice] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const members = useQuery({
    queryKey: ["members", wsId],
    queryFn: () => api<Member[]>("/members"),
    enabled: !!wsId && canRead,
  });
  const invitations = useQuery({
    queryKey: ["invitations", wsId],
    queryFn: () => api<Invitation[]>("/invitations"),
    enabled: !!wsId && canRead,
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["members", wsId] });
    void qc.invalidateQueries({ queryKey: ["invitations", wsId] });
  };
  const onError = (err: unknown) =>
    setNotice({ tone: "error", text: err instanceof ApiError ? err.message : tc("error") });

  const changeRole = useMutation({
    mutationFn: ({ id, role: next }: { id: string; role: Role }) =>
      api(`/members/${id}`, { method: "PATCH", body: { role: next } }),
    onSuccess: refresh,
    onError,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/members/${id}`, { method: "DELETE" }),
    onSuccess: refresh,
    onError,
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/invitations/${id}`, { method: "DELETE" }),
    onSuccess: refresh,
    onError,
  });

  const form = useForm<InvitationInput>({
    resolver: zodResolver(invitationSchema),
    defaultValues: { email: "", role: "analyst" },
  });
  const { errors, isSubmitting } = form.formState;
  async function invite(values: InvitationInput) {
    setNotice(null);
    try {
      await api("/invitations", { body: values });
      setNotice({ tone: "success", text: t("inviteSent", { email: values.email }) });
      form.reset({ email: "", role: values.role });
      refresh();
    } catch (err) {
      onError(err);
    }
  }

  return (
    <section aria-labelledby="team-title" className="grid gap-4">
      <div>
        <h2 id="team-title" className="text-lg font-semibold">
          {t("team")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("teamHelp")}</p>
      </div>
      {!canRead ? <Notice>{t("noTeamAccess")}</Notice> : null}
      {notice ? <Notice tone={notice.tone}>{notice.text}</Notice> : null}

      {canRead ? (
        <Card className="p-0">
          <table className="w-full text-sm">
            <caption className="sr-only">{t("team")}</caption>
            <thead className="border-b text-left text-muted-foreground">
              <tr>
                <th scope="col" className="p-3 font-medium">
                  {t("inviteEmail")}
                </th>
                <th scope="col" className="p-3 font-medium">
                  {t("inviteRole")}
                </th>
                <th scope="col" className="p-3">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {members.data?.map((m) => {
                const isMe = m.user_id === me?.user.id;
                return (
                  <tr key={m.id} className="border-b last:border-0">
                    <td className="p-3 break-all">
                      {m.email} {isMe ? <span className="text-muted-foreground">({t("you")})</span> : null}
                    </td>
                    <td className="p-3">
                      {canChange ? (
                        <Select
                          aria-label={t("changeRole", { email: m.email })}
                          value={m.role}
                          onChange={(e) => changeRole.mutate({ id: m.id, role: e.target.value as Role })}
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r}>
                              {ROLE_LABELS[r]}
                            </option>
                          ))}
                        </Select>
                      ) : (
                        ROLE_LABELS[m.role as Role]
                      )}
                    </td>
                    <td className="p-3 text-right">
                      {canRemove && !isMe ? (
                        <Button variant="outline" size="sm" onClick={() => remove.mutate(m.id)}>
                          {t("remove")}
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      ) : null}

      {canRead && invitations.data && invitations.data.length > 0 ? (
        <div className="grid gap-2">
          <h3 className="font-medium">{t("pending")}</h3>
          <ul className="grid gap-2">
            {invitations.data.map((inv) => (
              <li key={inv.id} className="flex items-center justify-between gap-3 rounded-md border p-3 text-sm">
                <span className="break-all">
                  {inv.email} · {ROLE_LABELS[inv.role as Role]}
                </span>
                {canInvite ? (
                  <Button variant="ghost" size="sm" onClick={() => revoke.mutate(inv.id)}>
                    {t("revoke")}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {canInvite ? (
        <Card>
          <h3 className="mb-4 font-medium">{t("invite")}</h3>
          <form className="grid gap-4 sm:grid-cols-[1fr_12rem_auto] sm:items-end" noValidate onSubmit={form.handleSubmit(invite)}>
            <Field id="inv-email" label={t("inviteEmail")} error={errors.email?.message}>
              <Input id="inv-email" type="email" autoComplete="off" aria-invalid={!!errors.email} {...form.register("email")} />
            </Field>
            <Field id="inv-role" label={t("inviteRole")}>
              <Select id="inv-role" {...form.register("role")}>
                {invitableRoles.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABELS[r]}
                  </option>
                ))}
              </Select>
            </Field>
            <Button type="submit" disabled={isSubmitting}>
              {t("sendInvite")}
            </Button>
          </form>
          <dl className="mt-4 grid gap-1 text-sm text-muted-foreground">
            {invitableRoles.map((r) => (
              <div key={r}>
                <dt className="inline font-medium text-foreground">{ROLE_LABELS[r]}: </dt>
                <dd className="inline">{ROLE_DESCRIPTIONS[r]}</dd>
              </div>
            ))}
          </dl>
        </Card>
      ) : null}
    </section>
  );
}
