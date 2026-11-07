"use client";

/**
 * Browser API helper. Access token lives in memory only; the refresh token is an
 * httpOnly cookie the browser sends to /api/v1/auth/*. On a 401 we refresh once.
 */
import { problemSchema, type Problem } from "@momentsales/shared";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly problem: Problem | null,
  ) {
    super(problem?.detail ?? problem?.title ?? `Request failed (${status})`);
  }

  get type(): string | undefined {
    return this.problem?.type;
  }
}

let accessToken: string | null = null;
let workspaceId: string | null = null;
let refreshing: Promise<string | null> | null = null;

export const tokenStore = {
  get: () => accessToken,
  set: (t: string | null) => {
    accessToken = t;
  },
  setWorkspace: (id: string | null) => {
    workspaceId = id;
  },
  workspace: () => workspaceId,
};

async function parse(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

async function toError(res: Response): Promise<ApiError> {
  const body = await parse(res).catch(() => null);
  const problem = problemSchema.safeParse(body);
  return new ApiError(res.status, problem.success ? problem.data : null);
}

export interface SessionResponse {
  status: "signed_in";
  access_token: string;
  expires_in: number;
  user: { id: string; email: string; name: string | null; totp_enabled: boolean; is_platform_admin: boolean };
}

/** Exchange the refresh cookie for a new access token. Null when signed out. */
export function refreshSession(): Promise<string | null> {
  refreshing ??= (async () => {
    try {
      const res = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-Requested-With": "momentsales" },
      });
      if (!res.ok) {
        accessToken = null;
        return null;
      }
      const body = (await res.json()) as SessionResponse;
      accessToken = body.access_token;
      return accessToken;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Multipart upload (e.g. a receipt). Mutually exclusive with `body`. */
  form?: FormData;
  /** Extra headers, e.g. Idempotency-Key for money writes. */
  headers?: Record<string, string>;
  workspace?: string | null | false;
  auth?: boolean;
}

/** A fresh key per user action; reuse it when retrying the same action. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const doFetch = () => {
    const headers: Record<string, string> = { "X-Requested-With": "momentsales", ...opts.headers };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (opts.auth !== false && accessToken) headers.Authorization = `Bearer ${accessToken}`;
    const ws = opts.workspace === undefined ? workspaceId : opts.workspace;
    if (ws) headers["X-Workspace-Id"] = ws;
    return fetch(`/api/v1${path}`, {
      method: opts.method ?? (opts.body === undefined && !opts.form ? "GET" : "POST"),
      headers,
      credentials: "same-origin",
      body: opts.form ?? (opts.body === undefined ? undefined : JSON.stringify(opts.body)),
    });
  };
  let res = await doFetch();
  if (res.status === 401 && opts.auth !== false && (await refreshSession())) {
    res = await doFetch();
  }
  if (!res.ok) throw await toError(res);
  return (await parse(res)) as T;
}
