"use client";

import { useQuery } from "@tanstack/react-query";
import * as React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { PageHeader, Stat } from "@/components/shared";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Skeleton,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui/display";
import { Select } from "@/components/ui/primitives";
import { useAgents, useOrgId } from "@/hooks/use-org";
import { api } from "@/lib/api";
import type { AnalyticsOverview } from "@/lib/types";
import { formatCost, formatNumber } from "@/lib/utils";

const COLORS = ["#0f766e", "#7c3aed", "#f59e0b", "#94a3b8"];

export default function AnalyticsPage() {
  const orgId = useOrgId();
  const agents = useAgents();
  const [days, setDays] = React.useState("30");
  const [agentId, setAgentId] = React.useState("");
  const from = new Date(Date.now() - Number(days) * 86400_000).toISOString();
  const q = useQuery({
    queryKey: ["analytics", orgId, days, agentId],
    queryFn: () =>
      api<AnalyticsOverview>(
        `/orgs/${orgId}/analytics/overview?from_=${encodeURIComponent(from)}${agentId ? `&agent_id=${agentId}` : ""}`,
      ),
  });
  const a = q.data;
  const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
  const toolData = a
    ? Object.entries(a.tool_split)
        .map(([name, value]) => ({
          name: { rag: "Knowledge", sql: "Dataset", web_search: "Web", none: "Direct" }[name] ?? name,
          value,
        }))
        .filter((d) => d.value > 0)
    : [];

  return (
    <div>
      <PageHeader
        title="Analytics"
        description="Volume, routing, latency, quality and cost."
        actions={
          <div className="flex gap-2">
            <div className="w-40">
              <Select
                value={days}
                onValueChange={setDays}
                options={[
                  { value: "7", label: "Last 7 days" },
                  { value: "30", label: "Last 30 days" },
                  { value: "90", label: "Last 90 days" },
                ]}
              />
            </div>
            <div className="w-48">
              <Select
                value={agentId || "all"}
                onValueChange={(v) => setAgentId(v === "all" ? "" : v)}
                options={[
                  { value: "all", label: "All agents" },
                  ...(agents.data ?? []).map((x) => ({ value: x.id, label: x.name })),
                ]}
              />
            </div>
          </div>
        }
      />
      {!a ? (
        <Skeleton className="h-64" />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Stat
              label="Messages in"
              value={formatNumber(a.totals.messages_in)}
              hint={`${formatNumber(a.totals.conversations)} conversations · ${formatNumber(a.totals.unique_contacts)} contacts`}
            />
            <Stat
              label="Latency p50 / p95"
              value={`${a.latency_ms.p50 ? (a.latency_ms.p50 / 1000).toFixed(1) : "—"}s / ${a.latency_ms.p95 ? (a.latency_ms.p95 / 1000).toFixed(1) : "—"}s`}
              hint={`avg ${a.latency_ms.avg ? (a.latency_ms.avg / 1000).toFixed(1) : "—"}s`}
            />
            <Stat
              label="SQL success"
              value={pct(a.sql.success_rate)}
              hint={`${a.sql.runs} runs · ${a.sql.failed} failed`}
            />
            <Stat
              label="No-answer rate"
              value={pct(a.no_answer_rate)}
              hint={`feedback 👍 ${a.feedback.positive} / 👎 ${a.feedback.negative} (${pct(a.feedback.ratio)} positive)`}
            />
          </div>
          <div className="grid gap-6 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Messages per day</CardTitle>
              </CardHeader>
              <CardContent className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={a.messages_per_day}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} tickFormatter={(d: string) => d.slice(5)} />
                    <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                    <RTooltip
                      contentStyle={{
                        background: "var(--card)",
                        border: "1px solid var(--border)",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                    />
                    <Legend />
                    <Bar dataKey="in" name="User messages" fill={COLORS[0]} radius={[3, 3, 0, 0]} />
                    <Bar dataKey="out" name="Answers" fill={COLORS[1]} radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Tool usage</CardTitle>
              </CardHeader>
              <CardContent className="h-72">
                {toolData.length ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={toolData}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={50}
                        outerRadius={90}
                        paddingAngle={3}
                      >
                        {toolData.map((_, i) => (
                          <Cell key={i} fill={COLORS[i % COLORS.length]} />
                        ))}
                      </Pie>
                      <RTooltip
                        contentStyle={{
                          background: "var(--card)",
                          border: "1px solid var(--border)",
                          borderRadius: 8,
                          fontSize: 12,
                        }}
                      />
                      <Legend />
                    </PieChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="text-sm text-muted-foreground">No answers in this period.</p>
                )}
              </CardContent>
            </Card>
          </div>
          <Card>
            <CardHeader>
              <CardTitle>Tokens & estimated cost by model</CardTitle>
            </CardHeader>
            <CardContent>
              {a.usage_by_model.length ? (
                <Table>
                  <THead>
                    <tr>
                      <Th>Model</Th>
                      <Th>Input tokens</Th>
                      <Th>Output tokens</Th>
                      <Th>Est. cost</Th>
                    </tr>
                  </THead>
                  <TBody>
                    {a.usage_by_model.map((m) => (
                      <Tr key={m.model}>
                        <Td className="font-medium">{m.model}</Td>
                        <Td>{formatNumber(m.input_tokens)}</Td>
                        <Td>{formatNumber(m.output_tokens)}</Td>
                        <Td>{formatCost(m.est_cost_usd)}</Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              ) : (
                <p className="text-sm text-muted-foreground">No LLM usage in this period.</p>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
