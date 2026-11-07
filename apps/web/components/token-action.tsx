"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Notice } from "@/components/ui/primitives";
import { api } from "@/lib/api";

/** POSTs the ?token= from a link once, then shows the outcome. */
export function TokenAction({
  endpoint,
  pending,
  success,
  failure,
}: {
  endpoint: string;
  pending: string;
  success: string;
  failure: string;
}) {
  const params = useSearchParams();
  const token = params.get("token");
  const [state, setState] = useState<"pending" | "ok" | "error">(token ? "pending" : "error");
  const sent = useRef(false);

  useEffect(() => {
    if (sent.current || !token) return;
    sent.current = true;
    api(endpoint, { body: { token }, auth: false, workspace: false })
      .then(() => setState("ok"))
      .catch(() => setState("error"));
  }, [endpoint, token]);

  if (state === "pending") return <Notice>{pending}</Notice>;
  return <Notice tone={state === "ok" ? "success" : "error"}>{state === "ok" ? success : failure}</Notice>;
}
