"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";

import { Markdown, PageHeader, SqlCode } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Alert, Badge, Card, KeyValue, Skeleton } from "@/components/ui/display";
import { Dialog, DialogContent } from "@/components/ui/primitives";
import { useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import type { ConversationOut, MessageOut } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

export default function ConversationPage() {
  const { id } = useParams<{ id: string }>();
  const orgId = useOrgId();
  const conv = useQuery({
    queryKey: ["conversation", orgId, id],
    queryFn: () => api<ConversationOut>(`/orgs/${orgId}/conversations/${id}`),
  });
  const [trace, setTrace] = React.useState<MessageOut | null>(null);
  if (conv.isPending) return <Skeleton className="h-64" />;
  if (conv.isError) return <Alert variant="destructive">{errorMessage(conv.error)}</Alert>;
  const c = conv.data!;
  return (
    <div className="mx-auto max-w-4xl">
      <Button variant="ghost" size="sm" asChild className="mb-2">
        <Link href={`/o/${orgId}/conversations`}>
          <ArrowLeft /> Conversations
        </Link>
      </Button>
      <PageHeader
        title={`${c.agent_name ?? "Agent"} · ${c.channel === "whatsapp" ? (c.contact?.wa_id ?? "WhatsApp") : "Playground"}`}
        description={`${c.message_count} messages · started ${formatDate(c.started_at)}${c.closed_at ? ` · closed (${String(c.flags.closed_reason ?? "")})` : ""}`}
      />
      <div className="space-y-3">
        {c.messages?.map((m) => (
          <div key={m.id} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
            <div
              className={cn(
                "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm",
                m.role === "user" ? "bg-primary text-primary-foreground" : "border border-border bg-card",
              )}
            >
              {m.role === "user" ? (
                <p className="whitespace-pre-wrap">{m.content}</p>
              ) : (
                <Markdown>{m.content}</Markdown>
              )}
              <div
                className={cn(
                  "mt-2 flex flex-wrap items-center gap-2 text-[11px]",
                  m.role === "user" ? "text-primary-foreground/80" : "text-muted-foreground",
                )}
              >
                <span>{formatDate(m.created_at)}</span>
                {m.route ? (
                  <Badge variant="secondary">
                    {m.route.tools.length ? m.route.tools.join(" + ") : "direct"}
                  </Badge>
                ) : null}
                {m.tool_summary ? <span>{m.tool_summary}</span> : null}
                {m.latency_ms ? <span>{(m.latency_ms / 1000).toFixed(1)}s</span> : null}
                {m.error ? <Badge variant="destructive">{m.error}</Badge> : null}
                {m.feedback ? (
                  <Badge variant={m.feedback.rating > 0 ? "success" : "destructive"}>
                    {m.feedback.rating > 0 ? "👍" : "👎"} {m.feedback.comment ?? ""}
                  </Badge>
                ) : null}
                {m.role === "assistant" ? (
                  <button className="underline" onClick={() => setTrace(m)}>
                    View trace
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        ))}
      </div>
      <TraceDialog message={trace} onClose={() => setTrace(null)} />
    </div>
  );
}

function TraceDialog({ message, onClose }: { message: MessageOut | null; onClose: () => void }) {
  const orgId = useOrgId();
  const trace = useQuery({
    queryKey: ["trace", orgId, message?.id],
    queryFn: () => api<Record<string, unknown>>(`/orgs/${orgId}/messages/${message!.id}/trace`),
    enabled: !!message,
  });
  const t = trace.data?.trace as Record<string, unknown> | null | undefined;
  return (
    <Dialog open={!!message} onOpenChange={(o) => !o && onClose()}>
      {message ? (
        <DialogContent title="Message trace" description={`run ${message.run_id ?? ""}`} wide>
          {trace.isPending ? (
            <Skeleton className="h-40" />
          ) : !t ? (
            <p className="text-sm text-muted-foreground">
              Trace expired (retention 30 days) or not recorded.
            </p>
          ) : (
            <div className="max-h-[70vh] space-y-4 overflow-y-auto text-xs">
              <KeyValue
                items={[
                  { k: "Route", v: JSON.stringify(trace.data?.route) },
                  { k: "Guardrails", v: JSON.stringify(trace.data?.guardrail_events) },
                ]}
              />
              <Card className="p-3">
                <p className="mb-1 font-medium">Node timings</p>
                {Object.entries((t.node_timings as Record<string, number>) ?? {}).map(([k, v]) => (
                  <div key={k} className="flex justify-between border-b border-border py-0.5">
                    <span>{k}</span>
                    <span>{v == null ? "—" : `${Number(v).toFixed(2)}s`}</span>
                  </div>
                ))}
              </Card>
              {(t.rag_chunks as Record<string, unknown>[])?.length ? (
                <Card className="p-3">
                  <p className="mb-1 font-medium">Retrieved chunks</p>
                  {(t.rag_chunks as Record<string, unknown>[]).map((c, i) => (
                    <p key={i} className="border-b border-border py-1">
                      [{i + 1}] {String(c.filename)} p.{String(c.page_start ?? "—")} · score{" "}
                      {Number(c.score).toFixed(3)} · {String(c.text).slice(0, 160)}
                    </p>
                  ))}
                </Card>
              ) : null}
              {(t.sql_attempts as Record<string, unknown>[])?.length ? (
                <Card className="p-3">
                  <p className="mb-1 font-medium">SQL attempts</p>
                  {(t.sql_attempts as Record<string, unknown>[]).map((a, i) => (
                    <div key={i} className="py-1">
                      {a.sql ? <SqlCode sql={String(a.sql)} /> : null}
                      <p className={a.error ? "text-destructive" : "text-success"}>
                        {a.error
                          ? String(a.error)
                          : `ok · ${String(a.rows)} rows · ${String(a.duration_ms)} ms`}
                      </p>
                    </div>
                  ))}
                </Card>
              ) : null}
              {(t.web_results as Record<string, unknown>[])?.length ? (
                <Card className="p-3">
                  <p className="mb-1 font-medium">Web results</p>
                  {(t.web_results as Record<string, unknown>[]).map((r, i) => (
                    <p key={i}>{String(r.url)}</p>
                  ))}
                </Card>
              ) : null}
              <Card className="p-3">
                <p className="mb-1 font-medium">Usage</p>
                <pre className="overflow-x-auto">{JSON.stringify(t.usage, null, 1)}</pre>
              </Card>
              {t.langsmith_run_url ? (
                <a
                  href={String(t.langsmith_run_url)}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary underline"
                >
                  Open LangSmith trace
                </a>
              ) : null}
            </div>
          )}
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
