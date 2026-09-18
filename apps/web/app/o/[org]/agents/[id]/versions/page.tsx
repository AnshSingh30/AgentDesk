"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import * as React from "react";

import { EmptyState, PageHeader } from "@/components/shared";
import { Button } from "@/components/ui/button";
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
import { useAgent } from "@/hooks/use-agent";
import { useOrgId } from "@/hooks/use-org";
import { api } from "@/lib/api";
import type { VersionOut } from "@/lib/types";
import { formatDate } from "@/lib/utils";

function flatten(obj: unknown, prefix = "", out: Record<string, string> = {}): Record<string, string> {
  if (obj && typeof obj === "object" && !Array.isArray(obj)) {
    for (const [k, v] of Object.entries(obj as Record<string, unknown>))
      flatten(v, prefix ? `${prefix}.${k}` : k, out);
  } else {
    out[prefix] = JSON.stringify(obj);
  }
  return out;
}

export default function VersionsPage() {
  const { id } = useParams<{ id: string }>();
  const orgId = useOrgId();
  const agent = useAgent(id);
  const versions = useQuery({
    queryKey: ["versions", orgId, id],
    queryFn: () => api<VersionOut[]>(`/orgs/${orgId}/agents/${id}/versions`),
  });
  const [a, setA] = React.useState<string>("");
  const [b, setB] = React.useState<string>("");
  const va = useQuery({
    queryKey: ["version", orgId, id, a],
    queryFn: () => api<VersionOut>(`/orgs/${orgId}/agents/${id}/versions/${a}`),
    enabled: !!a,
  });
  const vb = useQuery({
    queryKey: ["version", orgId, id, b],
    queryFn: () => api<VersionOut>(`/orgs/${orgId}/agents/${id}/versions/${b}`),
    enabled: !!b,
  });
  React.useEffect(() => {
    if (versions.data?.length && !a) {
      setA(versions.data[0].id);
      if (versions.data[1]) setB(versions.data[1].id);
    }
  }, [versions.data, a]);
  const diff = React.useMemo(() => {
    if (!va.data?.config || !vb.data?.config) return [];
    const fa = flatten(va.data.config);
    const fb = flatten(vb.data.config);
    const keys = [...new Set([...Object.keys(fa), ...Object.keys(fb)])]
      .filter((k) => !k.startsWith("meta."))
      .sort();
    return keys.filter((k) => fa[k] !== fb[k]).map((k) => ({ key: k, a: fa[k], b: fb[k] }));
  }, [va.data, vb.data]);
  const opts = (versions.data ?? []).map((v) => ({
    value: v.id,
    label: `v${v.version_number} · ${formatDate(v.created_at)}`,
  }));

  return (
    <div>
      <PageHeader
        title={`${agent.data?.name ?? "Agent"} · Versions`}
        description="Immutable snapshots created on publish. Compare any two to see what changed."
      />
      {versions.isPending ? (
        <Skeleton className="h-40" />
      ) : !versions.data?.length ? (
        <EmptyState
          title="No versions yet"
          description="Publish the draft from the builder to create version 1."
        />
      ) : (
        <div className="space-y-6">
          <Table>
            <THead>
              <tr>
                <Th>Version</Th>
                <Th>Changelog</Th>
                <Th>Published</Th>
              </tr>
            </THead>
            <TBody>
              {versions.data.map((v) => (
                <Tr key={v.id}>
                  <Td className="font-medium">v{v.version_number}</Td>
                  <Td className="text-muted-foreground">{v.changelog || "—"}</Td>
                  <Td className="text-muted-foreground">{formatDate(v.created_at)}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
          <Card>
            <CardHeader>
              <CardTitle>Compare</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="mb-4 grid gap-3 sm:grid-cols-2">
                <Select value={a} onValueChange={setA} options={opts} placeholder="Version A" />
                <Select value={b} onValueChange={setB} options={opts} placeholder="Version B" />
              </div>
              {a && b && a === b ? (
                <p className="text-sm text-muted-foreground">Pick two different versions.</p>
              ) : null}
              {va.data && vb.data && a !== b ? (
                diff.length ? (
                  <Table>
                    <THead>
                      <tr>
                        <Th>Setting</Th>
                        <Th>v{va.data.version_number}</Th>
                        <Th>v{vb.data.version_number}</Th>
                      </tr>
                    </THead>
                    <TBody>
                      {diff.map((d) => (
                        <Tr key={d.key}>
                          <Td className="font-mono text-xs">{d.key}</Td>
                          <Td className="max-w-xs break-words font-mono text-xs text-destructive">
                            {d.a ?? "—"}
                          </Td>
                          <Td className="max-w-xs break-words font-mono text-xs text-success">
                            {d.b ?? "—"}
                          </Td>
                        </Tr>
                      ))}
                    </TBody>
                  </Table>
                ) : (
                  <p className="text-sm text-muted-foreground">No configuration differences.</p>
                )
              ) : null}
              <Button variant="ghost" size="sm" className="mt-3" onClick={() => setB(a)}>
                Clear comparison
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
