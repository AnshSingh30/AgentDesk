"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";

import { api } from "@/lib/api";
import type { AgentSummary, DatasetOut, KBOut, OrgOut } from "@/lib/types";

export function useOrgId(): string {
  const params = useParams<{ org: string }>();
  return params.org;
}

export function useOrg() {
  const orgId = useOrgId();
  return useQuery({
    queryKey: ["org", orgId],
    queryFn: () => api<OrgOut>(`/orgs/${orgId}`),
    enabled: !!orgId,
  });
}

export function useAgents() {
  const orgId = useOrgId();
  return useQuery({
    queryKey: ["agents", orgId],
    queryFn: () => api<AgentSummary[]>(`/orgs/${orgId}/agents`),
  });
}

export function useKnowledgeBases() {
  const orgId = useOrgId();
  return useQuery({
    queryKey: ["kbs", orgId],
    queryFn: () => api<KBOut[]>(`/orgs/${orgId}/knowledge-bases`),
  });
}

export function useDatasets() {
  const orgId = useOrgId();
  return useQuery({
    queryKey: ["datasets", orgId],
    queryFn: () => api<DatasetOut[]>(`/orgs/${orgId}/datasets`),
  });
}
