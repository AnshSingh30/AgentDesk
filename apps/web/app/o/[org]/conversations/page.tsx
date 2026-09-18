"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Download, MessageSquare } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState, PageHeader } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Badge, Skeleton, Table, TBody, Td, Th, THead, Tr } from "@/components/ui/display";
import { Input, Select } from "@/components/ui/primitives";
import { useAgents, useOrgId } from "@/hooks/use-org";
import { api, API_URL } from "@/lib/api";
import { useRole } from "@/lib/auth";
import type { ConversationOut, DeploymentOut } from "@/lib/types";
import { formatDate } from "@/lib/utils";

export default function ConversationsPage() {
  const orgId = useOrgId();
  const agents = useAgents();
  const { can } = useRole(orgId);
  const deployments = useQuery({
    queryKey: ["deployments", orgId],
    queryFn: () => api<DeploymentOut[]>(`/orgs/${orgId}/deployments`),
  });
  const [f, setF] = React.useState({
    agent_id: "",
    deployment_id: "",
    channel: "",
    flag: "",
    from: "",
    to: "",
  });
  const params = new URLSearchParams();
  Object.entries(f).forEach(
    ([k, v]) =>
      v && params.set(k === "from" ? "from_" : k, k === "from" || k === "to" ? new Date(v).toISOString() : v),
  );
  const qs = params.toString();
  const list = useInfiniteQuery({
    queryKey: ["conversations", orgId, qs],
    queryFn: ({ pageParam }) =>
      api<{ items: ConversationOut[]; next_cursor: string | null }>(
        `/orgs/${orgId}/conversations?limit=30${qs ? `&${qs}` : ""}${pageParam ? `&cursor=${pageParam}` : ""}`,
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const rows = list.data?.pages.flatMap((p) => p.items) ?? [];
  const all = (v: string) => (v === "all" ? "" : v);

  return (
    <div>
      <PageHeader
        title="Conversations"
        description="Read-only transcripts from WhatsApp and the Playground, with per-message routing and traces."
        actions={
          can("editor") ? (
            <Button variant="outline" asChild>
              <a
                href={`${API_URL}/v1/orgs/${orgId}/conversations/export.csv?${qs}`}
                target="_blank"
                rel="noreferrer"
              >
                <Download /> Export CSV
              </a>
            </Button>
          ) : null
        }
      />
      <div className="mb-4 grid gap-2 md:grid-cols-6">
        <Select
          value={f.agent_id || "all"}
          onValueChange={(v) => setF({ ...f, agent_id: all(v) })}
          options={[
            { value: "all", label: "All agents" },
            ...(agents.data ?? []).map((a) => ({ value: a.id, label: a.name })),
          ]}
        />
        <Select
          value={f.deployment_id || "all"}
          onValueChange={(v) => setF({ ...f, deployment_id: all(v) })}
          options={[
            { value: "all", label: "All deployments" },
            ...(deployments.data ?? []).map((d) => ({
              value: d.id,
              label: d.display_phone ?? d.phone_number_id ?? d.id,
            })),
          ]}
        />
        <Select
          value={f.channel || "all"}
          onValueChange={(v) => setF({ ...f, channel: all(v) })}
          options={[
            { value: "all", label: "All channels" },
            { value: "whatsapp", label: "WhatsApp" },
            { value: "playground", label: "Playground" },
          ]}
        />
        <Select
          value={f.flag || "all"}
          onValueChange={(v) => setF({ ...f, flag: all(v) })}
          options={[
            { value: "all", label: "Any flag" },
            { value: "has_error", label: "Has error" },
            { value: "no_answer", label: "No answer" },
            { value: "sql_failed", label: "SQL failed" },
            { value: "negative_feedback", label: "Negative feedback" },
          ]}
        />
        <Input
          type="date"
          value={f.from}
          onChange={(e) => setF({ ...f, from: e.target.value })}
          aria-label="From date"
        />
        <Input
          type="date"
          value={f.to}
          onChange={(e) => setF({ ...f, to: e.target.value })}
          aria-label="To date"
        />
      </div>
      {list.isPending ? (
        <Skeleton className="h-40" />
      ) : !rows.length ? (
        <EmptyState
          icon={MessageSquare}
          title="No conversations"
          description="Conversations appear here once someone messages an agent in the Playground or on WhatsApp."
        />
      ) : (
        <>
          <Table>
            <THead>
              <tr>
                <Th>Agent</Th>
                <Th>Channel</Th>
                <Th>Contact</Th>
                <Th>Messages</Th>
                <Th>Flags</Th>
                <Th>Last message</Th>
              </tr>
            </THead>
            <TBody>
              {rows.map((c) => (
                <Tr key={c.id}>
                  <Td>
                    <Link href={`/o/${orgId}/conversations/${c.id}`} className="font-medium hover:underline">
                      {c.agent_name ?? "Agent"}
                    </Link>
                  </Td>
                  <Td className="capitalize text-muted-foreground">{c.channel}</Td>
                  <Td className="font-mono text-xs">
                    {c.contact
                      ? `${c.contact.wa_id}${c.contact.profile_name ? ` · ${c.contact.profile_name}` : ""}`
                      : "—"}
                  </Td>
                  <Td>{c.message_count}</Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(c.flags)
                        .filter(([k, v]) => v === true && k !== "closed_reason")
                        .map(([k]) => (
                          <Badge key={k} variant={k === "has_error" ? "destructive" : "warning"}>
                            {k.replace(/_/g, " ")}
                          </Badge>
                        ))}
                    </div>
                  </Td>
                  <Td className="text-muted-foreground">{formatDate(c.last_message_at)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
          {list.hasNextPage ? (
            <div className="mt-4 flex justify-center">
              <Button
                variant="outline"
                onClick={() => list.fetchNextPage()}
                loading={list.isFetchingNextPage}
              >
                Load more
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
