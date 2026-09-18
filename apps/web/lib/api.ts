"use client";

import { fetchEventSource } from "@microsoft/fetch-event-source";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  instance?: string;
  request_id?: string;
  errors?: { loc?: string[]; msg?: string; type?: string }[];
}

export class ApiError extends Error {
  status: number;
  problem?: Problem;
  constructor(message: string, status: number, problem?: Problem) {
    super(message);
    this.status = status;
    this.problem = problem;
  }
  get requestId() {
    return this.problem?.request_id;
  }
}

export function getCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const m = document.cookie.match(
    new RegExp("(?:^|; )" + name.replace(/[.$?*|{}()[\]\\/+^]/g, "\\$&") + "=([^;]*)"),
  );
  return m ? decodeURIComponent(m[1]) : undefined;
}

let refreshing: Promise<boolean> | null = null;

export async function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch(`${API_URL}/v1/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "X-CSRF-Token": getCookie("csrf_token") ?? "" },
    })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        setTimeout(() => (refreshing = null), 0);
      });
  }
  return refreshing;
}

export interface ApiInit {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  retry?: boolean;
  raw?: boolean;
}

export async function api<T = unknown>(path: string, init: ApiInit = {}): Promise<T> {
  const { method = "GET", body, headers = {}, signal, retry = true, raw = false } = init;
  const res = await fetch(`${API_URL}/v1${path}`, {
    method,
    credentials: "include",
    signal,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      "X-CSRF-Token": getCookie("csrf_token") ?? "",
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await refreshSession()) return api<T>(path, { ...init, retry: false });
  }
  if (!res.ok) {
    let problem: Problem | undefined;
    try {
      problem = await res.json();
    } catch {
      /* not json */
    }
    throw new ApiError(problem?.detail ?? problem?.title ?? res.statusText, res.status, problem);
  }
  if (raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export interface SseHandlers {
  onEvent: (event: string, data: Record<string, unknown>) => void;
  onError?: (err: unknown) => void;
  onClose?: () => void;
}

export async function apiStream(path: string, body: unknown, handlers: SseHandlers, signal?: AbortSignal) {
  await fetchEventSource(`${API_URL}/v1${path}`, {
    method: "POST",
    credentials: "include",
    signal,
    openWhenHidden: true,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": getCookie("csrf_token") ?? "" },
    body: JSON.stringify(body),
    async onopen(res) {
      if (!res.ok) {
        let problem: Problem | undefined;
        try {
          problem = await res.json();
        } catch {
          /* ignore */
        }
        throw new ApiError(problem?.detail ?? res.statusText, res.status, problem);
      }
    },
    onmessage(ev) {
      if (!ev.event) return;
      let data: Record<string, unknown> = {};
      try {
        data = ev.data ? JSON.parse(ev.data) : {};
      } catch {
        data = { raw: ev.data };
      }
      handlers.onEvent(ev.event, data);
    },
    onerror(err) {
      handlers.onError?.(err);
      throw err; // do not retry
    },
    onclose() {
      handlers.onClose?.();
    },
  });
}

export function problemFields(err: unknown): Record<string, string> {
  if (!(err instanceof ApiError) || !err.problem?.errors) return {};
  const out: Record<string, string> = {};
  for (const e of err.problem.errors) {
    const key = (e.loc ?? []).filter((p) => p !== "body").join(".");
    if (key && e.msg) out[key] = e.msg;
  }
  return out;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong";
}
