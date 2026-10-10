"use client";

import { useQuery } from "@tanstack/react-query";
import { BookmarkPlus, Bug, RotateCcw, Send, Square, ThumbsDown, ThumbsUp } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Markdown, SqlCode } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Badge, Card, Spinner } from "@/components/ui/display";
import { Dialog, DialogClose, DialogContent, Switch, Textarea } from "@/components/ui/primitives";
import { useOrgId } from "@/hooks/use-org";
import { api, apiStream, errorMessage } from "@/lib/api";
import type { Citation, ToolName } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface RunEvents {
  run_id?: string;
  route?: { tools: ToolName[]; reason: string };
  tools: Record<string, Record<string, unknown>>;
  citations?: Citation[];
  guardrails: Record<string, unknown>[];
  usage?: Record<string, unknown>;
  debug: Record<string, unknown>[];
  error?: { code: string; message: string };
  no_answer?: boolean;
  latency_ms?: number;
  formatted?: string[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: "streaming" | "done" | "error";
  events: RunEvents;
  message_id?: string;
  feedback?: 1 | -1;
}

export interface PendingConfirm {
  run_id: string;
  sql: string;
  explanation?: string;
  assumptions?: string[];
}

function emptyEvents(): RunEvents {
  return { tools: {}, guardrails: [], debug: [] };
}

export function usePlayground(agentId: string, versionId: string | null) {
  const orgId = useOrgId();
  const [sessionId, setSessionId] = React.useState<string | null>(null);
  const [starters, setStarters] = React.useState<string[]>([]);
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [pending, setPending] = React.useState<PendingConfirm | null>(null);
  const [confirmSql, setConfirmSql] = React.useState(false);
  const [confirmAvailable, setConfirmAvailable] = React.useState(false);
  const abort = React.useRef<AbortController | null>(null);
  const runId = React.useRef<string | null>(null);

  const createSession = React.useCallback(async () => {
    const s = await api<{ session_id: string; starter_questions: string[]; confirm_sql_available: boolean }>(
      `/orgs/${orgId}/agents/${agentId}/playground/sessions`,
      { method: "POST", body: { version_id: versionId } },
    );
    setSessionId(s.session_id);
    setStarters(s.starter_questions);
    setConfirmAvailable(s.confirm_sql_available);
    setMessages([]);
    setPending(null);
    return s.session_id;
  }, [orgId, agentId, versionId]);

  React.useEffect(() => {
    createSession().catch((e) => toast.error(errorMessage(e)));
  }, [createSession]);

  const patchLast = React.useCallback((fn: (m: ChatMessage) => ChatMessage) => {
    setMessages((prev) => {
      const idx = prev.length - 1;
      if (idx < 0 || prev[idx].role !== "assistant") return prev;
      const next = prev.slice();
      next[idx] = fn(next[idx]);
      return next;
    });
  }, []);

  const handleEvent = React.useCallback(
    (event: string, data: Record<string, unknown>) => {
      switch (event) {
        case "run":
          runId.current = String(data.run_id);
          patchLast((m) => ({ ...m, events: { ...m.events, run_id: String(data.run_id) } }));
          break;
        case "token":
          patchLast((m) => ({
            ...m,
            content: data.replace ? String(data.text ?? "") : m.content + String(data.text ?? ""),
          }));
          break;
        case "route":
          patchLast((m) => ({ ...m, events: { ...m.events, route: data as unknown as RunEvents["route"] } }));
          break;
        case "tool_start":
          patchLast((m) => ({
            ...m,
            events: {
              ...m.events,
              tools: { ...m.events.tools, [String(data.tool)]: { ...data, started: true } },
            },
          }));
          break;
        case "tool_result":
          patchLast((m) => ({
            ...m,
            events: {
              ...m.events,
              tools: {
                ...m.events.tools,
                [String(data.tool)]: { ...m.events.tools[String(data.tool)], ...data, done: true },
              },
            },
          }));
          break;
        case "citations":
          patchLast((m) => ({ ...m, events: { ...m.events, citations: data.citations as Citation[] } }));
          break;
        case "guardrail":
          patchLast((m) => ({ ...m, events: { ...m.events, guardrails: [...m.events.guardrails, data] } }));
          break;
        case "debug":
          patchLast((m) => ({ ...m, events: { ...m.events, debug: [...m.events.debug, data] } }));
          break;
        case "usage":
          patchLast((m) => ({
            ...m,
            events: { ...m.events, usage: data, latency_ms: Number(data.latency_ms) },
          }));
          break;
        case "sql_confirm_required":
          setPending({
            run_id: String(data.run_id),
            sql: String(data.sql ?? ""),
            explanation: data.explanation as string | undefined,
            assumptions: data.assumptions as string[] | undefined,
          });
          break;
        case "error":
          patchLast((m) => ({
            ...m,
            status: "error",
            events: { ...m.events, error: data as unknown as RunEvents["error"] },
          }));
          break;
        case "done":
          patchLast((m) => ({
            ...m,
            status: m.status === "error" ? "error" : "done",
            content: (data.answer as string) || m.content,
            message_id: (data.message_id as string) ?? m.message_id,
            events: {
              ...m.events,
              citations: (data.citations as Citation[]) ?? m.events.citations,
              no_answer: !!data.no_answer,
              formatted: data.formatted as string[],
            },
          }));
          break;
      }
    },
    [patchLast],
  );

  const stream = React.useCallback(
    async (path: string, body: unknown) => {
      const ctrl = new AbortController();
      abort.current = ctrl;
      setBusy(true);
      try {
        await apiStream(path, body, { onEvent: handleEvent }, ctrl.signal);
      } catch (e) {
        if (!ctrl.signal.aborted) {
          patchLast((m) =>
            m.status === "streaming"
              ? {
                  ...m,
                  status: "error",
                  events: { ...m.events, error: { code: "network", message: errorMessage(e) } },
                }
              : m,
          );
          toast.error(errorMessage(e));
        }
      } finally {
        setBusy(false);
        patchLast((m) => (m.status === "streaming" ? { ...m, status: "done" } : m));
      }
    },
    [handleEvent, patchLast],
  );

  const send = React.useCallback(
    async (text: string) => {
      const sid = sessionId ?? (await createSession());
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "user", content: text, status: "done", events: emptyEvents() },
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "",
          status: "streaming",
          events: emptyEvents(),
        },
      ]);
      await stream(`/orgs/${orgId}/playground/sessions/${sid}/messages`, {
        message: text,
        confirm_sql: confirmSql,
      });
    },
    [sessionId, createSession, orgId, stream, confirmSql],
  );

  const confirm = React.useCallback(
    async (action: "run" | "edit" | "cancel", sql?: string) => {
      if (!pending || !sessionId) return;
      const p = pending;
      setPending(null);
      patchLast((m) => ({ ...m, status: "streaming" }));
      await stream(`/orgs/${orgId}/playground/sessions/${sessionId}/sql-confirm`, {
        run_id: p.run_id,
        action,
        sql,
      });
    },
    [pending, sessionId, orgId, stream, patchLast],
  );

  const cancel = React.useCallback(async () => {
    abort.current?.abort();
    if (sessionId && runId.current) {
      await api(`/orgs/${orgId}/playground/sessions/${sessionId}/cancel`, {
        method: "POST",
        body: { run_id: runId.current },
      }).catch(() => undefined);
    }
    patchLast((m) => ({
      ...m,
      status: "error",
      events: { ...m.events, error: { code: "cancelled", message: "Cancelled" } },
    }));
    setBusy(false);
  }, [sessionId, orgId, patchLast]);

  const reset = React.useCallback(async () => {
    if (!sessionId) return;
    const s = await api<{ session_id: string }>(`/orgs/${orgId}/playground/sessions/${sessionId}/reset`, {
      method: "POST",
    });
    setSessionId(s.session_id);
    setMessages([]);
    setPending(null);
  }, [sessionId, orgId]);

  const feedback = React.useCallback(
    async (m: ChatMessage, rating: 1 | -1, comment?: string, saveAsEval?: boolean) => {
      if (!m.message_id) return;
      await api(`/orgs/${orgId}/messages/${m.message_id}/feedback`, {
        method: "POST",
        body: { rating, comment, save_as_eval: !!saveAsEval },
      });
      setMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, feedback: rating } : x)));
      toast.success("Thanks for the feedback");
    },
    [orgId],
  );

  const annotate = React.useCallback(
    async (question: string, answer: string) => {
      try {
        await api(`/orgs/${orgId}/agents/${agentId}/annotations`, {
          method: "POST",
          body: { question, answer },
        });
        toast.success("Saved. Similar questions will get this answer without calling the model.");
        return true;
      } catch (e) {
        toast.error(errorMessage(e));
        return false;
      }
    },
    [orgId, agentId],
  );

  return {
    sessionId,
    starters,
    messages,
    busy,
    pending,
    confirmSql,
    setConfirmSql,
    confirmAvailable,
    send,
    confirm,
    cancel,
    reset,
    feedback,
    annotate,
  };
}

/* ---------------------------------------------------------------- UI */
function renderWithCitations(text: string) {
  return text.replace(/\[(\d{1,2})\]/g, (_m, n) => `<sup class="cite">${n}</sup>`);
}

export function ChatPane({
  pg,
  compact,
  selected,
  onSelect,
  whatsappPreview,
}: {
  pg: ReturnType<typeof usePlayground>;
  compact?: boolean;
  selected?: string | null;
  onSelect?: (id: string) => void;
  whatsappPreview?: boolean;
}) {
  const [input, setInput] = React.useState("");
  const [fbOpen, setFbOpen] = React.useState<ChatMessage | null>(null);
  const [fbComment, setFbComment] = React.useState("");
  const [fbEval, setFbEval] = React.useState(false);
  const [ann, setAnn] = React.useState<{ question: string; answer: string } | null>(null);
  const listRef = React.useRef<HTMLDivElement>(null);
  const [editSql, setEditSql] = React.useState<string | null>(null);
  React.useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [pg.messages]);

  const submit = async (text: string) => {
    const t = text.trim();
    if (!t || pg.busy) return;
    setInput("");
    await pg.send(t);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={listRef} className="flex-1 space-y-4 overflow-y-auto p-4">
        {pg.messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <p className="text-sm text-muted-foreground">
              Ask a question to test this agent. Try one of the starter questions:
            </p>
            <div className="flex max-w-lg flex-wrap justify-center gap-2">
              {pg.starters.map((s) => (
                <button
                  key={s}
                  onClick={() => submit(s)}
                  className="rounded-full border border-border bg-card px-3 py-1.5 text-xs hover:border-primary hover:text-primary"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {pg.messages.map((m) => (
          <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
            <div
              onClick={() => m.role === "assistant" && onSelect?.(m.id)}
              className={cn(
                "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm",
                m.role === "user" ? "bg-primary text-primary-foreground" : "border border-border bg-card",
                m.role === "assistant" && onSelect && "cursor-pointer",
                selected === m.id && "ring-2 ring-ring",
              )}
            >
              {m.role === "user" ? (
                <p className="whitespace-pre-wrap">{m.content}</p>
              ) : (
                <>
                  {m.status === "streaming" && !m.content ? (
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Spinner /> {toolLabel(m.events)}
                    </div>
                  ) : whatsappPreview && m.status === "done" ? (
                    <WhatsAppPreview text={m.content} citations={m.events.citations} />
                  ) : (
                    <Markdown>{renderWithCitations(m.content)}</Markdown>
                  )}
                  {m.events.error ? (
                    <p className="mt-2 text-xs text-destructive">{m.events.error.message}</p>
                  ) : null}
                  {m.status === "done" && m.events.citations?.length ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {m.events.citations.map((c) => (
                        <span
                          key={`${c.n}-${c.chunk_id}`}
                          className="rounded-md border border-border bg-muted px-2 py-0.5 text-[11px]"
                          title={c.snippet}
                        >
                          [{c.n}] {c.filename}
                          {c.page_start ? ` · p.${c.page_start}` : ""}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {m.status === "done" && m.message_id ? (
                    <div className="mt-2 flex items-center gap-1 text-muted-foreground">
                      {m.events.route ? (
                        <Badge variant="secondary" className="mr-1">
                          {m.events.route.tools.length ? m.events.route.tools.join(" + ") : "direct"}
                        </Badge>
                      ) : null}
                      {m.events.latency_ms ? (
                        <span className="text-[11px]">{(m.events.latency_ms / 1000).toFixed(1)}s</span>
                      ) : null}
                      <button
                        className={cn("rounded p-1 hover:bg-muted", m.feedback === 1 && "text-success")}
                        aria-label="Good answer"
                        onClick={() => pg.feedback(m, 1)}
                      >
                        <ThumbsUp className="h-3.5 w-3.5" />
                      </button>
                      <button
                        className={cn("rounded p-1 hover:bg-muted", m.feedback === -1 && "text-destructive")}
                        aria-label="Bad answer"
                        onClick={() => setFbOpen(m)}
                      >
                        <ThumbsDown className="h-3.5 w-3.5" />
                      </button>
                      <button
                        className="rounded p-1 hover:bg-muted"
                        aria-label="Save as annotation"
                        title="Save as annotation: reply to similar questions with this exact answer"
                        onClick={() => {
                          const i = pg.messages.findIndex((x) => x.id === m.id);
                          const q = pg.messages
                            .slice(0, i)
                            .reverse()
                            .find((x) => x.role === "user");
                          setAnn({ question: q?.content ?? "", answer: m.content });
                        }}
                      >
                        <BookmarkPlus className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ) : null}
                </>
              )}
            </div>
          </div>
        ))}
        {pg.pending ? (
          <Card className="border-warning/40 p-4">
            <p className="text-sm font-medium">Confirm SQL before running</p>
            <p className="mt-1 text-xs text-muted-foreground">{pg.pending.explanation}</p>
            {editSql !== null ? (
              <Textarea
                className="mt-2 font-mono text-xs"
                rows={6}
                value={editSql}
                onChange={(e) => setEditSql(e.target.value)}
              />
            ) : (
              <SqlCode sql={pg.pending.sql} className="mt-2" />
            )}
            {pg.pending.assumptions?.length ? (
              <ul className="mt-2 list-disc pl-5 text-xs text-muted-foreground">
                {pg.pending.assumptions.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            ) : null}
            <div className="mt-3 flex gap-2">
              {editSql === null ? (
                <>
                  <Button size="sm" onClick={() => pg.confirm("run")}>
                    Run
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setEditSql(pg.pending!.sql)}>
                    Edit
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  onClick={() => {
                    const sql = editSql;
                    setEditSql(null);
                    pg.confirm("edit", sql);
                  }}
                >
                  Run edited SQL
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => pg.confirm("cancel")}>
                Cancel
              </Button>
            </div>
          </Card>
        ) : null}
      </div>
      <form
        className="flex items-end gap-2 border-t border-border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit(input);
        }}
      >
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit(input);
            }
          }}
          rows={compact ? 1 : 2}
          placeholder="Type a message… (Enter to send, Shift+Enter for a new line)"
          className="min-h-[40px] resize-none"
          aria-label="Message"
        />
        {pg.busy ? (
          <Button type="button" variant="outline" onClick={pg.cancel} aria-label="Stop">
            <Square />
          </Button>
        ) : (
          <Button type="submit" disabled={!input.trim()} aria-label="Send">
            <Send />
          </Button>
        )}
      </form>
      <Dialog open={!!fbOpen} onOpenChange={(o) => !o && setFbOpen(null)}>
        {fbOpen ? (
          <DialogContent
            title="What was wrong?"
            description="Your note helps improve the agent. Optionally save this as an evaluation example."
          >
            <Textarea
              value={fbComment}
              onChange={(e) => setFbComment(e.target.value)}
              placeholder="e.g. cited the wrong policy"
              rows={3}
            />
            <label className="mt-3 flex items-center gap-2 text-sm">
              <Switch checked={fbEval} onCheckedChange={setFbEval} /> Save as eval example
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <DialogClose asChild>
                <Button variant="outline">Cancel</Button>
              </DialogClose>
              <Button
                onClick={async () => {
                  await pg.feedback(fbOpen, -1, fbComment || undefined, fbEval);
                  setFbOpen(null);
                  setFbComment("");
                  setFbEval(false);
                }}
              >
                Submit
              </Button>
            </div>
          </DialogContent>
        ) : null}
      </Dialog>
      <Dialog open={!!ann} onOpenChange={(o) => !o && setAnn(null)}>
        {ann ? (
          <DialogContent
            title="Save as annotation"
            description="Questions similar to this one will get this answer verbatim, without calling the model. Edit it into the answer you want."
          >
            <label className="text-sm font-medium" htmlFor="ann-q">
              Question
            </label>
            <Textarea
              id="ann-q"
              value={ann.question}
              onChange={(e) => setAnn({ ...ann, question: e.target.value })}
              rows={2}
            />
            <label className="mt-3 block text-sm font-medium" htmlFor="ann-a">
              Answer
            </label>
            <Textarea
              id="ann-a"
              value={ann.answer}
              onChange={(e) => setAnn({ ...ann, answer: e.target.value })}
              rows={6}
            />
            <div className="mt-4 flex justify-end gap-2">
              <DialogClose asChild>
                <Button variant="outline">Cancel</Button>
              </DialogClose>
              <Button
                disabled={!ann.question.trim() || !ann.answer.trim()}
                onClick={async () => {
                  if (await pg.annotate(ann.question.trim(), ann.answer.trim())) setAnn(null);
                }}
              >
                Save annotation
              </Button>
            </div>
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  );
}

function toolLabel(ev: RunEvents) {
  const running = Object.entries(ev.tools).find(([, v]) => v.started && !v.done)?.[0];
  if (running === "rag") return "Searching documents…";
  if (running === "sql") return "Querying dataset…";
  if (running === "web_search") return "Searching the web…";
  return ev.route ? "Writing answer…" : "Thinking…";
}

function WhatsAppPreview({ text, citations }: { text: string; citations?: Citation[] }) {
  const orgId = useOrgId();
  const q = useQuery({
    queryKey: ["wa-format", text],
    queryFn: () =>
      api<{ chunks: string[] }>(`/orgs/${orgId}/playground/format-whatsapp`, {
        method: "POST",
        body: { text, citations: citations ?? [] },
      }),
  });
  if (!q.data) return <Spinner />;
  return (
    <div className="space-y-2">
      {q.data.chunks.map((c, i) => (
        <pre
          key={i}
          className="whitespace-pre-wrap rounded-lg bg-[#d9fdd3] p-2 font-sans text-[13px] text-black dark:bg-[#005c4b] dark:text-white"
        >
          {c}
        </pre>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- debug panel */
export function DebugPanel({ message }: { message: ChatMessage | null }) {
  const [tab, setTab] = React.useState("route");
  const ev = message?.events;
  const tabs = ["route", "knowledge", "sql", "web", "guardrails", "usage"];
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 overflow-x-auto border-b border-border px-2 py-2 text-xs">
        <Bug className="mr-1 h-4 w-4 text-muted-foreground" />
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "rounded-md px-2 py-1 capitalize",
              tab === t ? "bg-muted font-medium" : "text-muted-foreground hover:bg-muted",
            )}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-3 text-xs">
        {!ev ? (
          <p className="text-muted-foreground">Send a message, then click an answer to inspect it.</p>
        ) : tab === "route" ? (
          <div className="space-y-2">
            <p>
              <b>Tools:</b> {ev.route?.tools.length ? ev.route.tools.join(", ") : "none (direct answer)"}
            </p>
            <p>
              <b>Reason:</b> {ev.route?.reason ?? "—"}
            </p>
            {ev.debug.map((d, i) => (
              <pre key={i} className="overflow-x-auto rounded bg-muted p-2">
                {JSON.stringify(d, null, 1)}
              </pre>
            ))}
            {ev.error ? (
              <p className="text-destructive">
                Error: {ev.error.code} - {ev.error.message}
              </p>
            ) : null}
            {ev.no_answer ? <Badge variant="warning">no-answer policy applied</Badge> : null}
          </div>
        ) : tab === "knowledge" ? (
          <ToolChunks tool={ev.tools.rag} />
        ) : tab === "sql" ? (
          <SqlTab tool={ev.tools.sql} />
        ) : tab === "web" ? (
          <WebTab tool={ev.tools.web_search} />
        ) : tab === "guardrails" ? (
          ev.guardrails.length ? (
            <ul className="space-y-1">
              {ev.guardrails.map((g, i) => (
                <li key={i} className="rounded bg-muted p-2">
                  <b>{String(g.stage)}</b> · {String(g.check)} → {String(g.action)}
                  {g.detail ? (
                    <span className="text-muted-foreground"> ({JSON.stringify(g.detail)})</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground">No guardrail events.</p>
          )
        ) : ev.usage ? (
          <div className="space-y-2">
            <p>
              <b>Tokens:</b> {String(ev.usage.input_tokens)} in / {String(ev.usage.output_tokens)} out ·{" "}
              <b>Cost:</b> ${Number(ev.usage.est_cost_usd).toFixed(5)} · <b>Latency:</b>{" "}
              {(Number(ev.usage.latency_ms) / 1000).toFixed(2)}s
            </p>
            <p className="font-medium">Timings per node</p>
            <ul>
              {Object.entries((ev.usage.timings as Record<string, number>) ?? {}).map(([k, v]) => (
                <li key={k} className="flex justify-between border-b border-border py-0.5">
                  <span>{k}</span>
                  <span>{v == null ? "—" : `${Number(v).toFixed(2)}s`}</span>
                </li>
              ))}
            </ul>
            <p className="font-medium">LLM calls</p>
            <ul>
              {((ev.usage.records as Record<string, unknown>[]) ?? []).map((r, i) => (
                <li key={i} className="flex justify-between border-b border-border py-0.5">
                  <span>
                    {String(r.node ?? r.kind)} · {String(r.model ?? "")}
                  </span>
                  <span>
                    {String(r.input_tokens ?? 0)}/{String(r.output_tokens ?? 0)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="text-muted-foreground">Usage arrives when the answer completes.</p>
        )}
      </div>
    </div>
  );
}

function ToolChunks({ tool }: { tool?: Record<string, unknown> }) {
  if (!tool) return <p className="text-muted-foreground">Knowledge Base was not used.</p>;
  const chunks = (tool.chunks as Record<string, unknown>[]) ?? [];
  if (tool.error) return <p className="text-destructive">{String(tool.error)}</p>;
  if (!chunks.length) return <p className="text-muted-foreground">No relevant chunks above the threshold.</p>;
  return (
    <ul className="space-y-2">
      {chunks.map((c, i) => (
        <li key={String(c.chunk_id)} className="rounded border border-border p-2">
          <div className="flex items-center justify-between">
            <span className="font-medium">
              [{i + 1}] {String(c.filename)} {c.page_start ? `· p.${String(c.page_start)}` : ""}
            </span>
            <span className="text-muted-foreground">
              score {Number(c.score).toFixed(3)}
              {c.rerank_score != null ? ` · rerank ${Number(c.rerank_score).toFixed(2)}` : ""}
            </span>
          </div>
          {c.section_title ? <p className="text-muted-foreground">{String(c.section_title)}</p> : null}
          <p className="mt-1 whitespace-pre-wrap">{String(c.text).slice(0, 600)}</p>
        </li>
      ))}
    </ul>
  );
}

function SqlTab({ tool }: { tool?: Record<string, unknown> }) {
  if (!tool) return <p className="text-muted-foreground">Dataset was not used.</p>;
  const attempts = (tool.attempts as Record<string, unknown>[]) ?? [];
  const result = tool.result as
    | { columns: string[]; rows: unknown[][]; row_count: number; truncated: boolean; duration_ms: number }
    | undefined;
  return (
    <div className="space-y-3">
      <p>
        <b>Status:</b> {String(tool.status ?? "running")}{" "}
        {tool.intent ? `· intent ${String(tool.intent)}` : ""}
      </p>
      {tool.sql ? <SqlCode sql={String(tool.sql)} /> : null}
      {tool.explanation ? <p className="text-muted-foreground">{String(tool.explanation)}</p> : null}
      {result ? (
        <div>
          <p className="mb-1">
            <b>{result.row_count} rows</b> in {result.duration_ms} ms {result.truncated ? "(truncated)" : ""}
            <button
              className="ml-2 text-primary underline"
              onClick={() => {
                const csv = [
                  result.columns.join(","),
                  ...result.rows.map((r) =>
                    r.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(","),
                  ),
                ].join("\n");
                const blob = new Blob([csv], { type: "text/csv" });
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = "result.csv";
                a.click();
              }}
            >
              Download CSV
            </button>
          </p>
          <div className="max-h-64 overflow-auto rounded border border-border">
            <table className="w-full text-[11px]">
              <thead className="bg-muted">
                <tr>
                  {result.columns.map((c) => (
                    <th key={c} className="px-2 py-1 text-left font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, 50).map((r, i) => (
                  <tr key={i} className="border-t border-border">
                    {r.map((v, j) => (
                      <td key={j} className="px-2 py-1">
                        {String(v ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {attempts.length ? (
        <div>
          <p className="font-medium">Attempts</p>
          <ol className="list-decimal space-y-1 pl-4">
            {attempts.map((a, i) => (
              <li key={i}>
                {a.error ? (
                  <span className="text-destructive">{String(a.error)}</span>
                ) : (
                  <span className="text-success">ok · {String(a.rows)} rows</span>
                )}
                {a.sql ? <SqlCode sql={String(a.sql)} className="mt-1" /> : null}
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </div>
  );
}

function WebTab({ tool }: { tool?: Record<string, unknown> }) {
  if (!tool) return <p className="text-muted-foreground">Web search was not used.</p>;
  if (tool.error) return <p className="text-destructive">{String(tool.error)}</p>;
  const results = (tool.results as Record<string, unknown>[]) ?? [];
  return (
    <ul className="space-y-2">
      {results.map((r) => (
        <li key={String(r.url)} className="rounded border border-border p-2">
          <a
            href={String(r.url)}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-primary underline"
          >
            {String(r.title || r.url)}
          </a>
          <p className="mt-1 text-muted-foreground">{String(r.content).slice(0, 300)}</p>
        </li>
      ))}
    </ul>
  );
}

export function PlaygroundToolbar({
  pg,
  extra,
}: {
  pg: ReturnType<typeof usePlayground>;
  extra?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-border px-3 py-2 text-xs">
      {pg.confirmAvailable ? (
        <label className="flex items-center gap-2">
          <Switch checked={pg.confirmSql} onCheckedChange={pg.setConfirmSql} /> Confirm SQL before running
        </label>
      ) : null}
      {extra}
      <div className="flex-1" />
      <Button variant="ghost" size="sm" onClick={pg.reset} disabled={pg.busy}>
        <RotateCcw /> Reset conversation
      </Button>
    </div>
  );
}
