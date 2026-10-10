"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Trash2 } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog, CopyButton } from "@/components/shared";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui/display";
import { Input } from "@/components/ui/primitives";
import { API_URL, api, errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/utils";

interface ApiKeyOut {
  id: string;
  name: string;
  last4: string;
  created_at: string;
  last_used_at: string | null;
  key?: string;
}

/** Service API keys for one agent (Admin+). The raw key is shown once, right after creation. */
export function ApiAccessCard({
  orgId,
  agentId,
  published,
}: {
  orgId: string;
  agentId: string;
  published: boolean;
}) {
  const qc = useQueryClient();
  const path = `/orgs/${orgId}/agents/${agentId}/api-keys`;
  const keys = useQuery({
    queryKey: ["api-keys", orgId, agentId],
    queryFn: () => api<{ items: ApiKeyOut[] }>(path),
  });
  const [name, setName] = React.useState("");
  const [fresh, setFresh] = React.useState<string | null>(null);
  const [revoking, setRevoking] = React.useState<ApiKeyOut | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["api-keys", orgId, agentId] });
  const create = useMutation({
    mutationFn: () => api<ApiKeyOut>(path, { method: "POST", body: { name: name.trim() } }),
    onSuccess: (k) => {
      setFresh(k.key ?? null);
      setName("");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`${path}/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("API key revoked");
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const curl = `curl -X POST ${API_URL}/v1/service/chat \\
  -H "Authorization: Bearer ${fresh ?? "adk_..."}" \\
  -H "Content-Type: application/json" \\
  -d '{"message": "Hello", "user": "customer-42", "response_mode": "blocking"}'`;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="h-4 w-4" /> Service API
        </CardTitle>
        <CardDescription>
          Call this agent&apos;s latest published version from your own backend. Pass the returned{" "}
          <code>conversation_id</code> to keep context; <code>response_mode: &quot;streaming&quot;</code>{" "}
          returns server-sent events. Keep keys server-side - never ship them in a browser or mobile app.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!published ? (
          <Alert variant="warning">Publish a version before calling the Service API.</Alert>
        ) : null}
        {fresh ? (
          <Alert variant="success">
            <p className="mb-2 font-medium">Copy your new key now - it won&apos;t be shown again.</p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1 text-xs">{fresh}</code>
              <CopyButton value={fresh} />
            </div>
          </Alert>
        ) : null}
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <Input
            aria-label="Key name"
            placeholder="Key name, e.g. website-backend"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" disabled={!name.trim() || create.isPending}>
            Create key
          </Button>
        </form>
        {keys.data?.items.length ? (
          <Table>
            <THead>
              <Tr>
                <Th>Name</Th>
                <Th>Key</Th>
                <Th>Created</Th>
                <Th>Last used</Th>
                <Th />
              </Tr>
            </THead>
            <TBody>
              {keys.data.items.map((k) => (
                <Tr key={k.id}>
                  <Td>{k.name}</Td>
                  <Td className="font-mono text-xs">adk_…{k.last4}</Td>
                  <Td>{formatDate(k.created_at)}</Td>
                  <Td>{k.last_used_at ? formatDate(k.last_used_at) : "Never"}</Td>
                  <Td className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Revoke ${k.name}`}
                      onClick={() => setRevoking(k)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        ) : null}
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">Example</p>
            <CopyButton value={curl} />
          </div>
          <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">{curl}</pre>
        </div>
      </CardContent>
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(o) => !o && setRevoking(null)}
        title="Revoke API key?"
        description={`Requests using "${revoking?.name}" will fail immediately. This cannot be undone.`}
        onConfirm={() => {
          if (revoking) revoke.mutate(revoking.id);
          setRevoking(null);
        }}
      />
    </Card>
  );
}
