"use client";

import type { components } from "@momentsales/shared/api";
import type { Role } from "@momentsales/shared";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, refreshSession, tokenStore } from "@/lib/api";
import { safeStorage } from "@/lib/utils";

export type Me = components["schemas"]["MeOut"];
export type Membership = components["schemas"]["MembershipOut"];

type Status = "loading" | "signed_out" | "signed_in";

interface SessionValue {
  status: Status;
  me: Me | null;
  workspace: Membership | null;
  role: Role | null;
  selectWorkspace: (id: string) => void;
  reload: () => Promise<Me | null>;
  signInWithToken: (token: string) => Promise<Me | null>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);
const WS_KEY = "ms.workspace";

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);

  const load = useCallback(async (): Promise<Me | null> => {
    try {
      const data = await api<Me>("/auth/me", { workspace: false });
      setMe(data);
      const stored = safeStorage.get(WS_KEY);
      const pick =
        data.memberships.find((m) => m.workspace_id === stored) ?? data.memberships[0] ?? null;
      setWorkspaceId(pick?.workspace_id ?? null);
      tokenStore.setWorkspace(pick?.workspace_id ?? null);
      setStatus("signed_in");
      return data;
    } catch {
      setMe(null);
      setStatus("signed_out");
      return null;
    }
  }, []);

  useEffect(() => {
    void (async () => {
      if (!tokenStore.get() && !(await refreshSession())) {
        setStatus("signed_out");
        return;
      }
      await load();
    })();
  }, [load]);

  const selectWorkspace = useCallback((id: string) => {
    setWorkspaceId(id);
    tokenStore.setWorkspace(id);
    safeStorage.set(WS_KEY, id);
  }, []);

  const signInWithToken = useCallback(
    async (token: string) => {
      tokenStore.set(token);
      return load();
    },
    [load],
  );

  const signOut = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST", workspace: false });
    } finally {
      tokenStore.set(null);
      tokenStore.setWorkspace(null);
      setMe(null);
      setStatus("signed_out");
    }
  }, []);

  const value = useMemo<SessionValue>(() => {
    const workspace = me?.memberships.find((m) => m.workspace_id === workspaceId) ?? null;
    return {
      status,
      me,
      workspace,
      role: (workspace?.role as Role | undefined) ?? null,
      selectWorkspace,
      reload: load,
      signInWithToken,
      signOut,
    };
  }, [status, me, workspaceId, selectWorkspace, load, signInWithToken, signOut]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}
