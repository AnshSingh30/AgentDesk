"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog, PageHeader, Stat } from "@/components/shared";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardDescription,
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
import { Field, Input, Select, Switch } from "@/components/ui/primitives";
import { useOrg, useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import { useMe, useRole } from "@/lib/auth";
import type { CredentialOut } from "@/lib/types";
import { cn, formatDate, formatNumber } from "@/lib/utils";

const SECTIONS = [
  { key: "general", label: "General" },
  { key: "members", label: "Members" },
  { key: "api-keys", label: "API keys" },
  { key: "usage", label: "Usage" },
  { key: "data", label: "Data & retention" },
  { key: "audit-log", label: "Audit log" },
];

export default function SettingsPage() {
  const { section } = useParams<{ section: string }>();
  const orgId = useOrgId();
  const current = SECTIONS.find((s) => s.key === section) ?? SECTIONS[0];
  const View = {
    general: General,
    members: Members,
    "api-keys": ApiKeys,
    usage: Usage,
    data: DataRetention,
    "audit-log": AuditLog,
  }[current.key]!;
  return (
    <div>
      <PageHeader title="Settings" />
      <div className="grid gap-6 lg:grid-cols-[200px_1fr]">
        <nav className="flex gap-1 overflow-x-auto lg:flex-col" aria-label="Settings sections">
          {SECTIONS.map((s) => (
            <Link
              key={s.key}
              href={`/o/${orgId}/settings/${s.key}`}
              className={cn(
                "whitespace-nowrap rounded-md px-3 py-1.5 text-sm hover:bg-muted",
                s.key === current.key && "bg-muted font-medium",
              )}
            >
              {s.label}
            </Link>
          ))}
        </nav>
        <div className="min-w-0">
          <View />
        </div>
      </div>
    </div>
  );
}

function General() {
  const orgId = useOrgId();
  const org = useOrg();
  const qc = useQueryClient();
  const me = useMe();
  const { can } = useRole(orgId);
  const [name, setName] = React.useState("");
  const [industry, setIndustry] = React.useState("");
  const [size, setSize] = React.useState("");
  const [profileName, setProfileName] = React.useState("");
  const [pw, setPw] = React.useState({ current_password: "", new_password: "" });
  React.useEffect(() => {
    if (org.data) {
      setName(org.data.name);
      setIndustry(org.data.industry ?? "");
      setSize(org.data.size ?? "");
    }
  }, [org.data]);
  React.useEffect(() => {
    if (me.data) setProfileName(me.data.user.name);
  }, [me.data]);
  const save = useMutation({
    mutationFn: () =>
      api(`/orgs/${orgId}`, {
        method: "PATCH",
        body: { name, industry: industry || null, size: size || null },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["org", orgId] });
      qc.invalidateQueries({ queryKey: ["me"] });
      toast.success("Saved");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const saveMe = useMutation({
    mutationFn: () =>
      api("/auth/me", { method: "PATCH", body: { name: profileName, ...(pw.new_password ? pw : {}) } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me"] });
      setPw({ current_password: "", new_password: "" });
      toast.success("Profile updated");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const logoutAll = useMutation({
    mutationFn: () => api("/auth/logout-all", { method: "POST" }),
    onSuccess: () => (window.location.href = "/login"),
  });
  if (org.isPending) return <Skeleton className="h-40" />;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Organisation profile</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} disabled={!can("admin")} />
          </Field>
          <Field label="Industry">
            <Input value={industry} onChange={(e) => setIndustry(e.target.value)} disabled={!can("admin")} />
          </Field>
          <Field label="Size">
            <Input value={size} onChange={(e) => setSize(e.target.value)} disabled={!can("admin")} />
          </Field>
          {can("admin") ? (
            <div className="sm:col-span-2">
              <Button onClick={() => save.mutate()} loading={save.isPending}>
                Save
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Your profile</CardTitle>
          <CardDescription>{me.data?.user.email}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">
            <Input value={profileName} onChange={(e) => setProfileName(e.target.value)} />
          </Field>
          <div />
          <Field label="Current password">
            <Input
              type="password"
              value={pw.current_password}
              onChange={(e) => setPw({ ...pw, current_password: e.target.value })}
              autoComplete="current-password"
            />
          </Field>
          <Field label="New password" hint="At least 10 characters">
            <Input
              type="password"
              value={pw.new_password}
              onChange={(e) => setPw({ ...pw, new_password: e.target.value })}
              autoComplete="new-password"
            />
          </Field>
          <div className="flex gap-2 sm:col-span-2">
            <Button onClick={() => saveMe.mutate()} loading={saveMe.isPending}>
              Update profile
            </Button>
            <Button variant="outline" onClick={() => logoutAll.mutate()}>
              Log out of all devices
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function Members() {
  const orgId = useOrgId();
  const qc = useQueryClient();
  const me = useMe();
  const { can, role } = useRole(orgId);
  const members = useQuery({
    queryKey: ["members", orgId],
    queryFn: () =>
      api<{ id: string; user_id: string; email: string; name: string; role: string; created_at: string }[]>(
        `/orgs/${orgId}/members`,
      ),
  });
  const invites = useQuery({
    queryKey: ["invites", orgId],
    queryFn: () =>
      api<{ id: string; email: string; role: string; expires_at: string }[]>(`/orgs/${orgId}/invitations`),
    enabled: can("admin"),
  });
  const [email, setEmail] = React.useState("");
  const [inviteRole, setInviteRole] = React.useState("editor");
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["members", orgId] });
    qc.invalidateQueries({ queryKey: ["invites", orgId] });
  };
  const invite = useMutation({
    mutationFn: () =>
      api(`/orgs/${orgId}/invitations`, { method: "POST", body: { email, role: inviteRole } }),
    onSuccess: () => {
      refresh();
      setEmail("");
      toast.success("Invitation sent");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const setRole = useMutation({
    mutationFn: ({ id, r }: { id: string; r: string }) =>
      api(`/orgs/${orgId}/members/${id}`, { method: "PATCH", body: { role: r } }),
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/orgs/${orgId}/members/${id}`, { method: "DELETE" }),
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const cancelInvite = useMutation({
    mutationFn: (id: string) => api(`/orgs/${orgId}/invitations/${id}`, { method: "DELETE" }),
    onSuccess: refresh,
  });
  const roles = role === "owner" ? ["owner", "admin", "editor", "viewer"] : ["admin", "editor", "viewer"];
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Members</CardTitle>
          <CardDescription>
            Owner: everything incl. delete org. Admin: everything except delete/transfer. Editor: build, test,
            publish. Viewer: read-only.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <THead>
              <tr>
                <Th>Member</Th>
                <Th>Role</Th>
                <Th>Joined</Th>
                <Th />
              </tr>
            </THead>
            <TBody>
              {members.data?.map((m) => (
                <Tr key={m.id}>
                  <Td>
                    <p className="font-medium">{m.name}</p>
                    <p className="text-xs text-muted-foreground">{m.email}</p>
                  </Td>
                  <Td>
                    {can("admin") && !(m.role === "owner" && role !== "owner") ? (
                      <div className="w-32">
                        <Select
                          value={m.role}
                          onValueChange={(r) => setRole.mutate({ id: m.id, r })}
                          options={roles.map((r) => ({ value: r, label: r }))}
                        />
                      </div>
                    ) : (
                      <Badge variant="secondary">{m.role}</Badge>
                    )}
                  </Td>
                  <Td className="text-muted-foreground">{formatDate(m.created_at)}</Td>
                  <Td>
                    {can("admin") || m.user_id === me.data?.user.id ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => remove.mutate(m.id)}
                        aria-label="Remove member"
                      >
                        <Trash2 />
                      </Button>
                    ) : null}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      {can("admin") ? (
        <Card>
          <CardHeader>
            <CardTitle>Invite</CardTitle>
            <CardDescription>Invitations expire in 7 days.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="colleague@company.com"
                className="min-w-[240px] flex-1"
                aria-label="Email"
              />
              <div className="w-32">
                <Select
                  value={inviteRole}
                  onValueChange={setInviteRole}
                  options={roles.map((r) => ({ value: r, label: r }))}
                />
              </div>
              <Button onClick={() => invite.mutate()} disabled={!email} loading={invite.isPending}>
                <Plus /> Invite
              </Button>
            </div>
            {invites.data?.length ? (
              <ul className="divide-y divide-border text-sm">
                {invites.data.map((i) => (
                  <li key={i.id} className="flex items-center justify-between py-2">
                    <span>
                      {i.email} · <Badge variant="secondary">{i.role}</Badge> · expires{" "}
                      {formatDate(i.expires_at)}
                    </span>
                    <Button size="sm" variant="ghost" onClick={() => cancelInvite.mutate(i.id)}>
                      Cancel
                    </Button>
                  </li>
                ))}
              </ul>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function ApiKeys() {
  const orgId = useOrgId();
  const qc = useQueryClient();
  const { can } = useRole(orgId);
  const creds = useQuery({
    queryKey: ["credentials", orgId],
    queryFn: () => api<CredentialOut[]>(`/orgs/${orgId}/provider-credentials`),
  });
  const [form, setForm] = React.useState({ provider: "openai", label: "", api_key: "", api_base: "" });
  const add = useMutation({
    mutationFn: () =>
      api(`/orgs/${orgId}/provider-credentials`, {
        method: "POST",
        body: { ...form, api_base: form.api_base || null },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["credentials", orgId] });
      setForm({ provider: "openai", label: "", api_key: "", api_base: "" });
      toast.success("Key saved (encrypted)");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: (id: string) => api(`/orgs/${orgId}/provider-credentials/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials", orgId] }),
  });
  const test = useMutation({
    mutationFn: (id: string) =>
      api<{ ok: boolean; code?: string; message?: string; model?: string }>(
        `/orgs/${orgId}/provider-credentials/${id}/test`,
        { method: "POST" },
      ),
    onSuccess: (r) => (r.ok ? toast.success(`Works · ${r.model}`) : toast.error(`${r.code}: ${r.message}`)),
  });
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Provider API keys</CardTitle>
          <CardDescription>
            Encrypted at rest with envelope encryption; only the last 4 characters are ever shown. Referenced
            by agents in the Model step.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {creds.data?.length ? (
            <Table>
              <THead>
                <tr>
                  <Th>Label</Th>
                  <Th>Provider</Th>
                  <Th>Key</Th>
                  <Th>Last used</Th>
                  <Th />
                </tr>
              </THead>
              <TBody>
                {creds.data.map((c) => (
                  <Tr key={c.id}>
                    <Td className="font-medium">{c.label}</Td>
                    <Td>{c.provider}</Td>
                    <Td className="font-mono text-xs">
                      ····{c.last4}
                      {c.api_base ? ` · ${c.api_base}` : ""}
                    </Td>
                    <Td className="text-muted-foreground">
                      {c.last_used_at ? formatDate(c.last_used_at) : "never"}
                    </Td>
                    <Td>
                      {can("admin") ? (
                        <div className="flex justify-end gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => test.mutate(c.id)}
                            disabled={["tavily", "cohere"].includes(c.provider)}
                          >
                            Test
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => del.mutate(c.id)}
                            aria-label="Delete key"
                          >
                            <Trash2 />
                          </Button>
                        </div>
                      ) : null}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">No keys yet.</p>
          )}
        </CardContent>
      </Card>
      {can("admin") ? (
        <Card>
          <CardHeader>
            <CardTitle>Add key</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field label="Provider">
              <Select
                value={form.provider}
                onValueChange={(v) => setForm({ ...form, provider: v })}
                options={[
                  "openai",
                  "anthropic",
                  "gemini",
                  "groq",
                  "openrouter",
                  "custom",
                  "tavily",
                  "cohere",
                ].map((p) => ({ value: p, label: p }))}
              />
            </Field>
            <Field label="Label">
              <Input
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                placeholder="Production OpenAI"
              />
            </Field>
            <Field label="API key">
              <Input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                autoComplete="off"
              />
            </Field>
            <Field label="Base URL (custom endpoints only)">
              <Input
                value={form.api_base}
                onChange={(e) => setForm({ ...form, api_base: e.target.value })}
                placeholder="https://…/v1"
              />
            </Field>
            <div className="sm:col-span-2">
              <Button
                onClick={() => add.mutate()}
                disabled={!form.label || !form.api_key}
                loading={add.isPending}
              >
                <Plus /> Save key
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function Usage() {
  const orgId = useOrgId();
  const usage = useQuery({
    queryKey: ["usage", orgId],
    queryFn: () =>
      api<{
        from: string;
        to: string;
        rows: {
          key: string;
          events: number;
          input_tokens: number;
          output_tokens: number;
          units: number;
          est_cost_usd: number;
        }[];
        limits: { messages_per_month: number; storage_mb: number };
      }>(`/orgs/${orgId}/usage`),
  });
  const byModel = useQuery({
    queryKey: ["usage", orgId, "model"],
    queryFn: () =>
      api<{ rows: { key: string; input_tokens: number; output_tokens: number; est_cost_usd: number }[] }>(
        `/orgs/${orgId}/usage?group_by=model`,
      ),
  });
  if (!usage.data) return <Skeleton className="h-40" />;
  const row = (k: string) => usage.data!.rows.find((r) => r.key === k);
  const msgs = row("message_in")?.events ?? 0;
  const cost = usage.data.rows.reduce((s, r) => s + r.est_cost_usd, 0);
  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        This month ({formatDate(usage.data.from, { timeStyle: undefined })} → today). Metering only; pricing
        is not enabled yet.
      </p>
      <div className="grid gap-4 md:grid-cols-3">
        <Stat
          label="Messages"
          value={`${formatNumber(msgs)} / ${formatNumber(usage.data.limits.messages_per_month)}`}
          hint={`${Math.round((msgs / usage.data.limits.messages_per_month) * 100)}% of monthly limit`}
        />
        <Stat
          label="LLM calls"
          value={formatNumber(row("llm")?.events ?? 0)}
          hint={`${formatNumber((row("llm")?.input_tokens ?? 0) + (row("llm")?.output_tokens ?? 0))} tokens`}
        />
        <Stat
          label="Estimated cost"
          value={`$${cost.toFixed(2)}`}
          hint={`embeddings ${row("embedding")?.events ?? 0} · web searches ${row("web_search")?.events ?? 0}`}
        />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>By model</CardTitle>
        </CardHeader>
        <CardContent>
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
              {(byModel.data?.rows ?? [])
                .filter((r) => r.key)
                .map((r) => (
                  <Tr key={r.key}>
                    <Td>{r.key}</Td>
                    <Td>{formatNumber(r.input_tokens)}</Td>
                    <Td>{formatNumber(r.output_tokens)}</Td>
                    <Td>${r.est_cost_usd.toFixed(4)}</Td>
                  </Tr>
                ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function DataRetention() {
  const orgId = useOrgId();
  const org = useOrg();
  const qc = useQueryClient();
  const router = useRouter();
  const { can, role } = useRole(orgId);
  const [retention, setRetention] = React.useState(180);
  const [tracing, setTracing] = React.useState(true);
  const [del, setDel] = React.useState(false);
  React.useEffect(() => {
    if (org.data) {
      setRetention(Number(org.data.settings.retention_days ?? 180));
      setTracing(org.data.settings.tracing_enabled !== false);
    }
  }, [org.data]);
  const save = useMutation({
    mutationFn: () =>
      api(`/orgs/${orgId}`, {
        method: "PATCH",
        body: { settings: { retention_days: retention, tracing_enabled: tracing } },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["org", orgId] });
      toast.success("Saved");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const exportData = useMutation({
    mutationFn: () => api(`/orgs/${orgId}/export`, { method: "POST" }),
    onSuccess: () => toast.success("Export started; you'll get a notification with the download link"),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api(`/orgs/${orgId}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.clear();
      router.push("/onboarding");
    },
  });
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Retention & privacy</CardTitle>
          <CardDescription>
            Run traces are kept 30 days. Messages are deleted after the retention period below.
            Sub-processors: LLM providers, Tavily, Meta, LangSmith, email provider.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Message retention (days)" hint="7-3650">
            <Input
              type="number"
              min={7}
              max={3650}
              value={retention}
              onChange={(e) => setRetention(Number(e.target.value))}
              disabled={!can("admin")}
              className="max-w-xs"
            />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={tracing} onCheckedChange={setTracing} disabled={!can("admin")} /> LLM tracing
            (LangSmith) enabled for this organisation
          </label>
          {can("admin") ? (
            <Button onClick={() => save.mutate()} loading={save.isPending}>
              Save
            </Button>
          ) : null}
        </CardContent>
      </Card>
      {role === "owner" ? (
        <Card>
          <CardHeader>
            <CardTitle>Export & delete</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Button variant="outline" onClick={() => exportData.mutate()} loading={exportData.isPending}>
              Export all data (JSON zip)
            </Button>
            <Alert variant="destructive" title="Delete organisation">
              Soft-deleted immediately (deployments stop); permanently removed after 14 days.
              <div className="mt-2">
                <Button variant="destructive" onClick={() => setDel(true)}>
                  Delete organisation
                </Button>
              </div>
            </Alert>
          </CardContent>
        </Card>
      ) : null}
      <ConfirmDialog
        open={del}
        onOpenChange={setDel}
        title="Delete this organisation?"
        description="All agents, documents, datasets and conversations will be deleted after 14 days."
        confirmText={org.data?.name}
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
      />
    </div>
  );
}

function AuditLog() {
  const orgId = useOrgId();
  const [action, setAction] = React.useState("");
  const q = useInfiniteQuery({
    queryKey: ["audit", orgId, action],
    queryFn: ({ pageParam }) =>
      api<{
        items: {
          id: string;
          actor_user_id: string | null;
          action: string;
          target_type: string | null;
          target_id: string | null;
          metadata: Record<string, unknown>;
          ip: string | null;
          created_at: string;
        }[];
        next_cursor: string | null;
      }>(
        `/orgs/${orgId}/audit-logs?limit=50${action ? `&action=${action}` : ""}${pageParam ? `&cursor=${pageParam}` : ""}`,
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Audit log</CardTitle>
        <Input
          value={action}
          onChange={(e) => setAction(e.target.value)}
          placeholder="Filter by action, e.g. agent.published"
          className="max-w-xs"
          aria-label="Filter"
        />
      </CardHeader>
      <CardContent>
        {q.isError ? <Alert variant="destructive">{errorMessage(q.error)}</Alert> : null}
        <Table>
          <THead>
            <tr>
              <Th>When</Th>
              <Th>Action</Th>
              <Th>Target</Th>
              <Th>Details</Th>
              <Th>IP</Th>
            </tr>
          </THead>
          <TBody>
            {rows.map((r) => (
              <Tr key={r.id}>
                <Td className="whitespace-nowrap text-muted-foreground">{formatDate(r.created_at)}</Td>
                <Td className="font-mono text-xs">{r.action}</Td>
                <Td className="font-mono text-xs text-muted-foreground">
                  {r.target_type ? `${r.target_type} ${r.target_id?.slice(0, 8)}` : "—"}
                </Td>
                <Td className="max-w-xs truncate text-xs text-muted-foreground">
                  {Object.keys(r.metadata).length ? JSON.stringify(r.metadata) : ""}
                </Td>
                <Td className="text-xs text-muted-foreground">{r.ip ?? ""}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
        {q.hasNextPage ? (
          <Button
            variant="outline"
            className="mt-3"
            onClick={() => q.fetchNextPage()}
            loading={q.isFetchingNextPage}
          >
            Load more
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
