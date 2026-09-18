"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Bot, CheckCircle2, Circle } from "lucide-react";
import Link from "next/link";

import { PageHeader, Stat } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, Skeleton, StatusBadge } from "@/components/ui/display";
import { useAgents, useDatasets, useKnowledgeBases, useOrg, useOrgId } from "@/hooks/use-org";
import { api } from "@/lib/api";
import type { AnalyticsOverview, ConversationOut, DeploymentOut } from "@/lib/types";
import { formatCost, formatNumber, timeAgo } from "@/lib/utils";

export default function DashboardPage() {
  const orgId = useOrgId();
  const org = useOrg();
  const agents = useAgents();
  const kbs = useKnowledgeBases();
  const datasets = useDatasets();
  const deployments = useQuery({
    queryKey: ["deployments", orgId],
    queryFn: () => api<DeploymentOut[]>(`/orgs/${orgId}/deployments`),
  });
  const analytics = useQuery({
    queryKey: ["analytics", orgId, "30d"],
    queryFn: () => api<AnalyticsOverview>(`/orgs/${orgId}/analytics/overview`),
  });
  const conversations = useQuery({
    queryKey: ["conversations", orgId, "recent"],
    queryFn: () => api<{ items: ConversationOut[] }>(`/orgs/${orgId}/conversations?limit=6`),
  });
  const base = `/o/${orgId}`;
  const checklist = [
    { done: (agents.data?.length ?? 0) > 0, label: "Create an agent", href: `${base}/agents/new` },
    {
      done:
        (kbs.data?.some((k) => k.doc_count > 0) ?? false) ||
        (datasets.data?.some((d) => d.status === "ready") ?? false),
      label: "Add documents or connect a dataset",
      href: `${base}/knowledge`,
    },
    {
      done: agents.data?.some((a) => a.status !== "draft") ?? false,
      label: "Publish a version",
      href: `${base}/agents`,
    },
    {
      done: deployments.data?.some((d) => d.status === "live") ?? false,
      label: "Deploy to WhatsApp",
      href: `${base}/agents`,
    },
  ];
  const a = analytics.data;

  return (
    <div>
      <PageHeader
        title={org.data ? `Welcome to ${org.data.name}` : "Dashboard"}
        description="Agents, usage and recent conversations for the last 30 days."
        actions={
          <Button asChild>
            <Link href={`${base}/agents/new`}>
              <Bot /> New agent
            </Link>
          </Button>
        }
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Messages (30d)"
          value={a ? formatNumber(a.totals.messages_in) : <Skeleton className="h-7 w-16" />}
          hint={a ? `${formatNumber(a.totals.conversations)} conversations` : undefined}
        />
        <Stat
          label="Unique contacts"
          value={a ? formatNumber(a.totals.unique_contacts) : <Skeleton className="h-7 w-16" />}
          hint="WhatsApp"
        />
        <Stat
          label="p95 latency"
          value={a?.latency_ms.p95 ? `${(a.latency_ms.p95 / 1000).toFixed(1)}s` : "—"}
          hint={a?.latency_ms.p50 ? `p50 ${(a.latency_ms.p50 / 1000).toFixed(1)}s` : undefined}
        />
        <Stat
          label="Est. LLM cost"
          value={a ? formatCost(a.usage_by_model.reduce((s, m) => s + m.est_cost_usd, 0)) : "—"}
          hint="platform + BYO usage"
        />
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Agents</CardTitle>
            <Button variant="ghost" size="sm" asChild>
              <Link href={`${base}/agents`}>
                View all <ArrowRight />
              </Link>
            </Button>
          </CardHeader>
          <CardContent>
            {agents.isPending ? (
              <Skeleton className="h-24" />
            ) : agents.data?.length ? (
              <ul className="divide-y divide-border">
                {agents.data.slice(0, 5).map((ag) => (
                  <li key={ag.id} className="flex items-center justify-between gap-3 py-2.5">
                    <div className="min-w-0">
                      <Link
                        href={`${base}/agents/${ag.id}/build/template`}
                        className="font-medium hover:underline"
                      >
                        {ag.name}
                      </Link>
                      <p className="truncate text-xs text-muted-foreground">
                        {ag.tools.length
                          ? ag.tools
                              .map((t) => ({ rag: "KB", sql: "Dataset", web_search: "Web" })[t])
                              .join(" · ")
                          : "no tools"}{" "}
                        · edited {timeAgo(ag.draft_updated_at)}
                      </p>
                    </div>
                    <StatusBadge status={ag.status} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No agents yet.</p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Setup checklist</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {checklist.map((c) => (
                <li key={c.label} className="flex items-center gap-2 text-sm">
                  {c.done ? (
                    <CheckCircle2 className="h-4 w-4 text-success" />
                  ) : (
                    <Circle className="h-4 w-4 text-muted-foreground" />
                  )}
                  <Link
                    href={c.href}
                    className={c.done ? "text-muted-foreground line-through" : "hover:underline"}
                  >
                    {c.label}
                  </Link>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
      <Card className="mt-6">
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Recent conversations</CardTitle>
          <Button variant="ghost" size="sm" asChild>
            <Link href={`${base}/conversations`}>
              View all <ArrowRight />
            </Link>
          </Button>
        </CardHeader>
        <CardContent>
          {conversations.data?.items.length ? (
            <ul className="divide-y divide-border">
              {conversations.data.items.map((c) => (
                <li key={c.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                  <Link href={`${base}/conversations/${c.id}`} className="min-w-0 truncate hover:underline">
                    {c.agent_name ?? "Agent"} ·{" "}
                    {c.channel === "whatsapp" ? (c.contact?.wa_id ?? "WhatsApp") : "Playground"}
                  </Link>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {c.message_count} msgs · {timeAgo(c.last_message_at)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No conversations yet. Try the Playground.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
