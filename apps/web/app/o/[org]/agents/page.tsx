"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bot, Copy, MoreHorizontal, Trash2 } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog, EmptyState, PageHeader } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Skeleton, StatusBadge, Table, TBody, Td, Th, THead, Tr } from "@/components/ui/display";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Input,
} from "@/components/ui/primitives";
import { useAgents, useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import { useRole } from "@/lib/auth";
import type { AgentSummary } from "@/lib/types";
import { timeAgo } from "@/lib/utils";

const TOOL_LABEL: Record<string, string> = {
  rag: "Knowledge Base",
  sql: "Dataset",
  web_search: "Web search",
};

export default function AgentsPage() {
  const orgId = useOrgId();
  const agents = useAgents();
  const qc = useQueryClient();
  const { can } = useRole(orgId);
  const [q, setQ] = React.useState("");
  const [toDelete, setToDelete] = React.useState<AgentSummary | null>(null);
  const base = `/o/${orgId}`;
  const del = useMutation({
    mutationFn: (id: string) => api(`/orgs/${orgId}/agents/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Agent deleted");
      setToDelete(null);
      qc.invalidateQueries({ queryKey: ["agents", orgId] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const dup = useMutation({
    mutationFn: (id: string) =>
      api<AgentSummary>(`/orgs/${orgId}/agents/${id}/duplicate`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Agent duplicated");
      qc.invalidateQueries({ queryKey: ["agents", orgId] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const rows = (agents.data ?? []).filter((a) => a.name.toLowerCase().includes(q.toLowerCase()));

  return (
    <div>
      <PageHeader
        title="Agents"
        description="Build, test and publish assistants. Each agent has a draft you edit and immutable published versions."
        actions={
          can("editor") ? (
            <Button asChild>
              <Link href={`${base}/agents/new`}>
                <Bot /> New agent
              </Link>
            </Button>
          ) : null
        }
      />
      <div className="mb-4 max-w-xs">
        <Input
          placeholder="Search agents"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search agents"
        />
      </div>
      {agents.isPending ? (
        <Skeleton className="h-40" />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Bot}
          title={q ? "No agents match your search" : "Create your first agent"}
          description="Start from a template with a ready-made prompt, tools and guardrails, or build one from scratch."
          action={
            can("editor") ? (
              <Button asChild>
                <Link href={`${base}/agents/new`}>New agent</Link>
              </Button>
            ) : undefined
          }
        />
      ) : (
        <Table>
          <THead>
            <tr>
              <Th>Name</Th>
              <Th>Status</Th>
              <Th>Tools</Th>
              <Th>Version</Th>
              <Th>Last edited</Th>
              <Th className="w-10" />
            </tr>
          </THead>
          <TBody>
            {rows.map((a) => (
              <Tr key={a.id}>
                <Td>
                  <Link
                    href={`${base}/agents/${a.id}/build/template`}
                    className="font-medium hover:underline"
                  >
                    {a.name}
                  </Link>
                  {a.description ? (
                    <p className="max-w-md truncate text-xs text-muted-foreground">{a.description}</p>
                  ) : null}
                </Td>
                <Td>
                  <StatusBadge status={a.status} />
                </Td>
                <Td className="text-muted-foreground">
                  {a.tools.length ? a.tools.map((t) => TOOL_LABEL[t]).join(", ") : "—"}
                </Td>
                <Td className="text-muted-foreground">
                  {a.latest_version_number ? `v${a.latest_version_number}` : "—"}
                </Td>
                <Td className="text-muted-foreground">{timeAgo(a.draft_updated_at)}</Td>
                <Td>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon-sm" aria-label="Agent actions">
                        <MoreHorizontal />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem asChild>
                        <Link href={`${base}/agents/${a.id}/playground`}>Open Playground</Link>
                      </DropdownMenuItem>
                      <DropdownMenuItem asChild>
                        <Link href={`${base}/agents/${a.id}/deploy`}>Deploy</Link>
                      </DropdownMenuItem>
                      <DropdownMenuItem asChild>
                        <Link href={`${base}/agents/${a.id}/versions`}>Versions</Link>
                      </DropdownMenuItem>
                      <DropdownMenuItem asChild>
                        <Link href={`${base}/agents/${a.id}/annotations`}>Annotations</Link>
                      </DropdownMenuItem>
                      {can("editor") ? (
                        <>
                          <DropdownMenuItem onSelect={() => dup.mutate(a.id)}>
                            <Copy /> Duplicate
                          </DropdownMenuItem>
                          <DropdownMenuItem destructive onSelect={() => setToDelete(a)}>
                            <Trash2 /> Delete
                          </DropdownMenuItem>
                        </>
                      ) : null}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      )}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={`Delete "${toDelete?.name}"?`}
        description="The agent, its draft and versions will be removed. Deployments using it stop working."
        confirmText={toDelete?.name}
        loading={del.isPending}
        onConfirm={() => {
          if (toDelete) del.mutate(toDelete.id);
        }}
      />
    </div>
  );
}
