/**
 * Typed API client generated from docs/openapi.json (pnpm gen:api).
 * Same-origin by default: the web app proxies /api/* to FastAPI.
 */
import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./generated/api";

export type { paths, components } from "./generated/api";

export interface ClientOptions {
  baseUrl?: string;
  getAccessToken?: () => string | null;
  getWorkspaceId?: () => string | null;
  fetch?: typeof fetch;
}

export function createApiClient(opts: ClientOptions = {}) {
  const client = createClient<paths>({
    baseUrl: opts.baseUrl ?? "",
    credentials: "same-origin",
    ...(opts.fetch ? { fetch: opts.fetch } : {}),
  });
  const auth: Middleware = {
    onRequest({ request }) {
      const token = opts.getAccessToken?.();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      const ws = opts.getWorkspaceId?.();
      if (ws && !request.headers.has("X-Workspace-Id")) request.headers.set("X-Workspace-Id", ws);
      request.headers.set("X-Requested-With", "momentsales");
      return request;
    },
  };
  client.use(auth);
  return client;
}

export type ApiClient = ReturnType<typeof createApiClient>;
