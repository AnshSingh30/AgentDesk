"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Logo, ThemeToggle } from "@/components/app-shell";
import { PageHeader } from "@/components/shared";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui/display";
import { Input, Switch, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { RequireAuth, useMe } from "@/lib/auth";
import { formatDate, formatNumber } from "@/lib/utils";

function Admin() {
  const me = useMe();
  const qc = useQueryClient();
  const enabled = !!me.data?.user.is_superadmin; // admin API 404s for everyone else
  const orgs = useQuery({
    enabled,
    queryKey: ["admin-orgs"],
    queryFn: () => api<Record<string, unknown>[]>("/admin/orgs"),
  });
  const users = useQuery({
    enabled,
    queryKey: ["admin-users"],
    queryFn: () => api<Record<string, unknown>[]>("/admin/users"),
  });
  const errors = useQuery({
    enabled,
    queryKey: ["admin-errors"],
    queryFn: () => api<Record<string, Record<string, unknown>[]>>("/admin/errors"),
  });
  const flags = useQuery({
    enabled,
    queryKey: ["admin-flags"],
    queryFn: () =>
      api<{ id: string; org_id: string | null; key: string; enabled: boolean }[]>("/admin/feature-flags"),
  });
  const [flag, setFlag] = React.useState({ key: "", org_id: "" });
  const suspend = useMutation({
    mutationFn: ({ id, on }: { id: string; on: boolean }) =>
      api(`/admin/orgs/${id}/${on ? "suspend" : "unsuspend"}`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-orgs"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const setFlagM = useMutation({
    mutationFn: (body: { key: string; org_id: string | null; enabled: boolean }) =>
      api("/admin/feature-flags", { method: "PUT", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-flags"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (me.data && !me.data.user.is_superadmin)
    return <Alert variant="destructive">Operator access required.</Alert>;
  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      <div className="mb-6 flex items-center justify-between">
        <Link href="/">
          <Logo />
        </Link>
        <ThemeToggle />
      </div>
      <PageHeader title="Operator console" description="Tenants, users, usage, errors and feature flags." />
      <Tabs defaultValue="orgs">
        <TabsList>
          <TabsTrigger value="orgs">Organisations</TabsTrigger>
          <TabsTrigger value="users">Users</TabsTrigger>
          <TabsTrigger value="errors">Errors</TabsTrigger>
          <TabsTrigger value="flags">Feature flags</TabsTrigger>
        </TabsList>
        <TabsContent value="orgs" className="mt-4">
          <Table>
            <THead>
              <tr>
                <Th>Organisation</Th>
                <Th>Members</Th>
                <Th>Agents</Th>
                <Th>Deployments</Th>
                <Th>Usage (month)</Th>
                <Th>Status</Th>
                <Th />
              </tr>
            </THead>
            <TBody>
              {orgs.data?.map((o) => (
                <Tr key={String(o.id)}>
                  <Td>
                    <Link href={`/o/${String(o.id)}`} className="font-medium hover:underline">
                      {String(o.name)}
                    </Link>
                    <p className="text-xs text-muted-foreground">{String(o.slug)}</p>
                  </Td>
                  <Td>{String(o.members)}</Td>
                  <Td>{String(o.agents)}</Td>
                  <Td>{String(o.deployments)}</Td>
                  <Td>
                    {formatNumber(Number(o.usage_events_month))} events · $
                    {Number(o.est_cost_month).toFixed(2)}
                  </Td>
                  <Td>
                    {o.suspended_at ? (
                      <Badge variant="destructive">suspended</Badge>
                    ) : o.deleted_at ? (
                      <Badge variant="secondary">deleted</Badge>
                    ) : (
                      <Badge variant="success">active</Badge>
                    )}
                  </Td>
                  <Td>
                    <Button
                      size="sm"
                      variant={o.suspended_at ? "outline" : "destructive"}
                      onClick={() => suspend.mutate({ id: String(o.id), on: !o.suspended_at })}
                    >
                      {o.suspended_at ? "Unsuspend" : "Suspend"}
                    </Button>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TabsContent>
        <TabsContent value="users" className="mt-4">
          <Table>
            <THead>
              <tr>
                <Th>User</Th>
                <Th>Orgs</Th>
                <Th>Verified</Th>
                <Th>Last login</Th>
              </tr>
            </THead>
            <TBody>
              {users.data?.map((u) => (
                <Tr key={String(u.id)}>
                  <Td>
                    <p className="font-medium">
                      {String(u.name)} {u.is_superadmin ? <Badge>superadmin</Badge> : null}
                    </p>
                    <p className="text-xs text-muted-foreground">{String(u.email)}</p>
                  </Td>
                  <Td className="text-xs">{(u.orgs as string[]).join(", ")}</Td>
                  <Td>{u.email_verified ? "yes" : "no"}</Td>
                  <Td className="text-muted-foreground">
                    {u.last_login_at ? formatDate(String(u.last_login_at)) : "—"}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TabsContent>
        <TabsContent value="errors" className="mt-4 space-y-4">
          {(["deployments_in_error", "failed_messages", "failed_documents"] as const).map((k) => (
            <Card key={k}>
              <CardHeader>
                <CardTitle className="capitalize">{k.replace(/_/g, " ")}</CardTitle>
              </CardHeader>
              <CardContent>
                {errors.data?.[k]?.length ? (
                  <ul className="space-y-1 text-xs">
                    {errors.data[k].map((e) => (
                      <li key={String(e.id)} className="border-b border-border py-1">
                        <span className="font-mono">{String(e.org_id).slice(0, 8)}</span> ·{" "}
                        {String(e.error ?? e.last_error ?? e.filename ?? "")}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground">None in the last 7 days.</p>
                )}
              </CardContent>
            </Card>
          ))}
        </TabsContent>
        <TabsContent value="flags" className="mt-4 space-y-4">
          <Card>
            <CardContent className="flex flex-wrap items-end gap-2 p-4">
              <Input
                value={flag.key}
                onChange={(e) => setFlag({ ...flag, key: e.target.value })}
                placeholder="flag key, e.g. web_search"
                className="max-w-xs"
                aria-label="Flag key"
              />
              <Input
                value={flag.org_id}
                onChange={(e) => setFlag({ ...flag, org_id: e.target.value })}
                placeholder="org id (blank = global)"
                className="max-w-xs"
                aria-label="Org id"
              />
              <Button
                onClick={() => setFlagM.mutate({ key: flag.key, org_id: flag.org_id || null, enabled: true })}
                disabled={!flag.key}
              >
                Enable
              </Button>
              <Button
                variant="outline"
                onClick={() =>
                  setFlagM.mutate({ key: flag.key, org_id: flag.org_id || null, enabled: false })
                }
                disabled={!flag.key}
              >
                Disable
              </Button>
            </CardContent>
          </Card>
          <Table>
            <THead>
              <tr>
                <Th>Key</Th>
                <Th>Scope</Th>
                <Th>Enabled</Th>
              </tr>
            </THead>
            <TBody>
              {flags.data?.map((f) => (
                <Tr key={f.id}>
                  <Td className="font-mono text-xs">{f.key}</Td>
                  <Td className="font-mono text-xs">{f.org_id ?? "global"}</Td>
                  <Td>
                    <Switch
                      checked={f.enabled}
                      onCheckedChange={(v) => setFlagM.mutate({ key: f.key, org_id: f.org_id, enabled: v })}
                    />
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function AdminPage() {
  return (
    <RequireAuth>
      <Admin />
    </RequireAuth>
  );
}
