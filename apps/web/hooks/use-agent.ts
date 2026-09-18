"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { useOrgId } from "@/hooks/use-org";
import { api, ApiError } from "@/lib/api";
import type { AgentConfig, AgentOut } from "@/lib/types";

export function useAgent(agentId: string) {
  const orgId = useOrgId();
  return useQuery({
    queryKey: ["agent", orgId, agentId],
    queryFn: () => api<AgentOut>(`/orgs/${orgId}/agents/${agentId}`),
    enabled: !!agentId,
  });
}

export type SaveState = "idle" | "dirty" | "saving" | "saved" | "conflict" | "error";

/**
 * Draft editing with debounced autosave (1 s) and optimistic concurrency via If-Match (AG-3).
 * Local edits are kept in `config`; the server copy is refreshed after each save.
 */
export function useAgentDraft(agentId: string) {
  const orgId = useOrgId();
  const qc = useQueryClient();
  const query = useAgent(agentId);
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [name, setName] = useState<string>("");
  const [state, setState] = useState<SaveState>("idle");
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const etag = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<{ config: AgentConfig; name: string } | null>(null);
  const flushRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    if (query.data && config === null) {
      setConfig(query.data.draft_config);
      setName(query.data.name);
      etag.current = query.data.etag;
    }
  }, [query.data, config]);

  const save = useMutation({
    mutationFn: async (payload: { config: AgentConfig; name: string }) => {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/v1/orgs/${orgId}/agents/${agentId}`,
        {
          method: "PATCH",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            "If-Match": etag.current ?? "*",
            "X-CSRF-Token": document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)?.[1] ?? "",
          },
          body: JSON.stringify({ draft_config: payload.config, name: payload.name }),
        },
      );
      if (!res.ok) {
        let problem;
        try {
          problem = await res.json();
        } catch {
          /* ignore */
        }
        throw new ApiError(problem?.detail ?? res.statusText, res.status, problem);
      }
      return (await res.json()) as AgentOut;
    },
    onSuccess: (data) => {
      etag.current = data.etag;
      setState("saved");
      setSavedAt(new Date());
      qc.setQueryData(["agent", orgId, agentId], data);
      qc.invalidateQueries({ queryKey: ["agents", orgId] });
    },
    onError: (err: ApiError) => {
      if (err.status === 412) setState("conflict");
      else {
        setState("error");
        toast.error(err.message);
      }
    },
  });

  const flush = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    if (pending.current) {
      const p = pending.current;
      pending.current = null;
      setState("saving");
      save.mutate(p);
    }
  }, [save]);

  const update = useCallback(
    (updater: (c: AgentConfig) => AgentConfig, nextName?: string) => {
      setConfig((prev) => {
        if (!prev) return prev;
        const next = updater(structuredClone(prev));
        pending.current = { config: next, name: nextName ?? name };
        return next;
      });
      if (nextName !== undefined) setName(nextName);
      setState((s) => (s === "conflict" ? s : "dirty"));
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(flush, 1000);
    },
    [flush, name],
  );

  const reload = useCallback(async () => {
    const fresh = await qc.fetchQuery({
      queryKey: ["agent", orgId, agentId],
      queryFn: () => api<AgentOut>(`/orgs/${orgId}/agents/${agentId}`),
    });
    setConfig(fresh.draft_config);
    setName(fresh.name);
    etag.current = fresh.etag;
    pending.current = null;
    setState("idle");
  }, [qc, orgId, agentId]);

  const overwrite = useCallback(() => {
    etag.current = "*";
    if (config) {
      pending.current = { config, name };
      flush();
    }
  }, [config, name, flush]);

  flushRef.current = flush;
  // flush a pending autosave when the builder unmounts (navigation within the debounce window)
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      if (pending.current) flushRef.current();
    },
    [],
  );

  return { query, agent: query.data, config, name, update, state, savedAt, reload, overwrite, flush };
}
