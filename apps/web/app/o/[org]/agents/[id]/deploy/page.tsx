"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Circle,
  ExternalLink,
  MessageCircle,
  Pause,
  Play,
  RefreshCw,
  Send,
  Trash2,
} from "lucide-react";
import { useParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ApiAccessCard } from "@/components/api-access";
import { ConfirmDialog, CopyButton, PageHeader } from "@/components/shared";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  KeyValue,
  StatusBadge,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui/display";
import { Field, Input, Select } from "@/components/ui/primitives";
import { useAgent } from "@/hooks/use-agent";
import { useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import { useRole } from "@/lib/auth";
import type { DeploymentOut, VersionOut } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const META_STEPS = [
  [
    "Create a Meta developer app",
    "In developers.facebook.com create an app of type Business and add the WhatsApp product.",
    "https://developers.facebook.com/apps/",
  ],
  [
    "Pick a number",
    "Use the free test number (sends to up to 5 verified recipients) or register your own business number.",
    "https://developers.facebook.com/docs/whatsapp/cloud-api/get-started",
  ],
  [
    "Create a permanent token",
    "Temporary tokens expire in ~24 hours. Create a System User in Meta Business Settings and generate a permanent token with whatsapp_business_messaging and whatsapp_business_management.",
    "https://developers.facebook.com/docs/whatsapp/business-management-api/get-started",
  ],
  [
    "Copy IDs and secret",
    "From the WhatsApp > API Setup page copy the Phone Number ID and WhatsApp Business Account ID; from App Settings > Basic copy the App Secret.",
    null,
  ],
];

export default function DeployPage() {
  const { id } = useParams<{ id: string }>();
  const orgId = useOrgId();
  const qc = useQueryClient();
  const agent = useAgent(id);
  const { can } = useRole(orgId);
  const versions = useQuery({
    queryKey: ["versions", orgId, id],
    queryFn: () => api<VersionOut[]>(`/orgs/${orgId}/agents/${id}/versions`),
  });
  const deployments = useQuery({
    queryKey: ["deployments", orgId, id],
    queryFn: () => api<DeploymentOut[]>(`/orgs/${orgId}/deployments?agent_id=${id}`),
    refetchInterval: (q) => (q.state.data?.some((d) => d.status === "pending_webhook") ? 5000 : false),
  });
  const [created, setCreated] = React.useState<DeploymentOut | null>(null);
  const [form, setForm] = React.useState({
    agent_version_id: "",
    phone_number_id: "",
    waba_id: "",
    access_token: "",
    app_secret: "",
  });
  const create = useMutation({
    mutationFn: () =>
      api<DeploymentOut>(`/orgs/${orgId}/deployments`, {
        method: "POST",
        body: {
          agent_id: id,
          agent_version_id: form.agent_version_id || null,
          phone_number_id: form.phone_number_id,
          waba_id: form.waba_id,
          access_token: form.access_token,
          app_secret: form.app_secret,
        },
      }),
    onSuccess: (d) => {
      setCreated(d);
      setForm({ agent_version_id: "", phone_number_id: "", waba_id: "", access_token: "", app_secret: "" });
      qc.invalidateQueries({ queryKey: ["deployments", orgId, id] });
      toast.success("Deployment created. Configure the webhook in Meta next.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["deployments", orgId, id] });
    qc.invalidateQueries({ queryKey: ["deployments", orgId] });
  };
  const noVersion = !agent.data?.latest_version_id;

  return (
    <div className="space-y-6">
      <PageHeader
        title={`${agent.data?.name ?? "Agent"} · Deploy`}
        description="Connect a WhatsApp Cloud API number to a published version, or call it from your backend with the Service API."
      />
      {noVersion ? (
        <Alert variant="warning">Publish a version from the builder before deploying.</Alert>
      ) : null}
      {(deployments.data ?? []).map((d) => (
        <DeploymentCard
          key={d.id}
          d={d}
          versions={versions.data ?? []}
          onChange={invalidate}
          verifyToken={created?.id === d.id ? created.verify_token : undefined}
          canManage={can("admin")}
        />
      ))}
      {can("admin") ? (
        <Card>
          <CardHeader>
            <CardTitle>New WhatsApp deployment</CardTitle>
            <CardDescription>
              Follow the Meta setup steps, then paste the credentials. Credentials are encrypted at rest and
              never shown again.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <ol className="space-y-3">
              {META_STEPS.map(([title, text, href], i) => (
                <li key={title as string} className="flex gap-3 text-sm">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-soft text-xs font-semibold text-primary">
                    {i + 1}
                  </span>
                  <div>
                    <p className="font-medium">
                      {title as string}
                      {href ? (
                        <a
                          href={href as string}
                          target="_blank"
                          rel="noreferrer"
                          className="ml-2 text-xs text-primary hover:underline"
                        >
                          Meta docs <ExternalLink className="inline h-3 w-3" />
                        </a>
                      ) : null}
                    </p>
                    <p className="text-muted-foreground">{text as string}</p>
                  </div>
                </li>
              ))}
            </ol>
            <Alert variant="warning">
              Temporary tokens from the API Setup page expire in about 24 hours - use a permanent System User
              token for anything beyond a quick test. Test numbers can only message recipients you add to the
              allowed list in Meta.
            </Alert>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Version to deploy">
                <Select
                  value={form.agent_version_id || (versions.data?.[0]?.id ?? "")}
                  onValueChange={(v) => setForm({ ...form, agent_version_id: v })}
                  options={(versions.data ?? []).map((v) => ({
                    value: v.id,
                    label: `v${v.version_number}${v.changelog ? ` · ${v.changelog}` : ""}`,
                  }))}
                  placeholder="Latest"
                />
              </Field>
              <Field label="Phone Number ID">
                <Input
                  value={form.phone_number_id}
                  onChange={(e) => setForm({ ...form, phone_number_id: e.target.value.trim() })}
                  placeholder="1234567890123"
                />
              </Field>
              <Field label="WhatsApp Business Account ID">
                <Input
                  value={form.waba_id}
                  onChange={(e) => setForm({ ...form, waba_id: e.target.value.trim() })}
                  placeholder="1234567890123"
                />
              </Field>
              <Field label="Access token (permanent)">
                <Input
                  type="password"
                  value={form.access_token}
                  onChange={(e) => setForm({ ...form, access_token: e.target.value.trim() })}
                  placeholder="EAAG…"
                  autoComplete="off"
                />
              </Field>
              <Field label="App secret">
                <Input
                  type="password"
                  value={form.app_secret}
                  onChange={(e) => setForm({ ...form, app_secret: e.target.value.trim() })}
                  autoComplete="off"
                />
              </Field>
            </div>
            <Button
              onClick={() => create.mutate()}
              loading={create.isPending}
              disabled={
                noVersion ||
                !form.phone_number_id ||
                !form.waba_id ||
                form.access_token.length < 20 ||
                form.app_secret.length < 10
              }
            >
              <MessageCircle /> Create deployment
            </Button>
          </CardContent>
        </Card>
      ) : null}
      {can("admin") ? <ApiAccessCard orgId={orgId} agentId={id} published={!noVersion} /> : null}
    </div>
  );
}

function DeploymentCard({
  d,
  versions,
  onChange,
  verifyToken,
  canManage,
}: {
  d: DeploymentOut;
  versions: VersionOut[];
  onChange: () => void;
  verifyToken?: string;
  canManage: boolean;
}) {
  const orgId = useOrgId();
  const [token, setToken] = React.useState<string | undefined>(verifyToken);
  const [testTo, setTestTo] = React.useState("");
  const [del, setDel] = React.useState(false);
  const [creds, setCreds] = React.useState({ access_token: "", app_secret: "" });
  React.useEffect(() => setToken(verifyToken), [verifyToken]);
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<DeploymentOut>(`/orgs/${orgId}/deployments/${d.id}`, { method: "PATCH", body }),
    onSuccess: () => {
      onChange();
      toast.success("Deployment updated");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const verify = useMutation({
    mutationFn: () =>
      api<{ ok: boolean; message?: string; code?: string; status: string }>(
        `/orgs/${orgId}/deployments/${d.id}/verify`,
        { method: "POST" },
      ),
    onSuccess: (r) => {
      onChange();
      if (r.ok) toast.success(`Credentials valid · status ${r.status}`);
      else toast.error(`${r.code}: ${r.message}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const test = useMutation({
    mutationFn: () =>
      api<{ ok: boolean; message?: string; code?: string }>(
        `/orgs/${orgId}/deployments/${d.id}/test-message`,
        { method: "POST", body: { to: testTo } },
      ),
    onSuccess: (r) => (r.ok ? toast.success("Test message sent") : toast.error(`${r.code}: ${r.message}`)),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reveal = useMutation({
    mutationFn: () => api<{ verify_token: string }>(`/orgs/${orgId}/deployments/${d.id}/verify-token`),
    onSuccess: (r) => setToken(r.verify_token),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const rotate = useMutation({
    mutationFn: () =>
      api<{ verify_token: string }>(`/orgs/${orgId}/deployments/${d.id}/rotate-verify-token`, {
        method: "POST",
      }),
    onSuccess: (r) => {
      setToken(r.verify_token);
      onChange();
      toast.success("Verify token rotated; re-verify the webhook in Meta");
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/orgs/${orgId}/deployments/${d.id}`, { method: "DELETE" }),
    onSuccess: () => {
      onChange();
      toast.success("Deployment deleted");
    },
  });
  const contacts = useQuery({
    queryKey: ["contacts", orgId, d.id],
    queryFn: () =>
      api<
        {
          id: string;
          wa_id: string;
          profile_name: string | null;
          message_count: number;
          blocked: boolean;
          last_seen_at: string;
        }[]
      >(`/orgs/${orgId}/deployments/${d.id}/contacts`),
  });
  const checks = [
    { label: "Credentials valid", ok: d.credentials_valid },
    { label: "Webhook verified", ok: !!d.webhook_verified_at },
    { label: "Live", ok: d.status === "live" },
  ];
  const latest = versions[0];

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          <CardTitle className="flex items-center gap-2">
            <MessageCircle className="h-4 w-4 text-whatsapp" /> {d.display_phone ?? d.phone_number_id}{" "}
            <StatusBadge status={d.status} />
          </CardTitle>
          <CardDescription>
            Pinned to v{d.version_number} · {d.message_count} messages ·{" "}
            {d.verified_name ?? "unverified name"}
            {d.quality_rating ? ` · quality ${d.quality_rating}` : ""}
          </CardDescription>
        </div>
        {canManage ? (
          <div className="flex flex-wrap gap-2">
            {d.status === "live" ? (
              <Button size="sm" variant="outline" onClick={() => patch.mutate({ status: "paused" })}>
                <Pause /> Pause
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => patch.mutate({ status: "live" })}
                disabled={!d.credentials_valid || !d.webhook_verified_at}
              >
                <Play /> Resume
              </Button>
            )}
            {latest && latest.id !== d.agent_version_id ? (
              <Button size="sm" onClick={() => patch.mutate({ agent_version_id: latest.id })}>
                Update to v{latest.version_number}
              </Button>
            ) : null}
            <Button size="sm" variant="ghost" onClick={() => setDel(true)} aria-label="Delete deployment">
              <Trash2 />
            </Button>
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-5">
        {d.last_error ? (
          <Alert variant="destructive" title="Deployment error">
            {d.last_error} ({formatDate(d.last_error_at)})
          </Alert>
        ) : null}
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <p className="text-sm font-medium">Webhook configuration (Meta → WhatsApp → Configuration)</p>
            <KeyValue
              items={[
                {
                  k: "Callback URL",
                  v: (
                    <span className="flex items-center gap-2 break-all font-mono text-xs">
                      {d.webhook_url} <CopyButton value={d.webhook_url} />
                    </span>
                  ),
                },
                {
                  k: "Verify token",
                  v: token ? (
                    <span className="flex items-center gap-2 font-mono text-xs">
                      {token} <CopyButton value={token} />
                    </span>
                  ) : (
                    <span className="flex items-center gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => reveal.mutate()}
                        disabled={!canManage}
                      >
                        Reveal
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => rotate.mutate()} disabled={!canManage}>
                        <RefreshCw /> Rotate
                      </Button>
                    </span>
                  ),
                },
                { k: "Subscribe to", v: <span className="font-mono text-xs">messages</span> },
              ]}
            />
            <p className="text-xs text-muted-foreground">
              Local development: expose port 8001 with cloudflared or ngrok and set WEBHOOK_BASE_URL to that
              URL.
            </p>
          </div>
          <div className="space-y-3">
            <p className="text-sm font-medium">Status checks</p>
            <ul className="space-y-1 text-sm">
              {checks.map((c) => (
                <li key={c.label} className="flex items-center gap-2">
                  {c.ok ? (
                    <CheckCircle2 className="h-4 w-4 text-success" />
                  ) : (
                    <Circle className="h-4 w-4 text-muted-foreground" />
                  )}{" "}
                  {c.label}
                </li>
              ))}
            </ul>
            <Button
              size="sm"
              variant="outline"
              onClick={() => verify.mutate()}
              loading={verify.isPending}
              disabled={!canManage}
            >
              Verify connection
            </Button>
            <div className="flex gap-2">
              <Input
                placeholder="Recipient, e.g. 9198xxxxxxxx"
                value={testTo}
                onChange={(e) => setTestTo(e.target.value)}
                className="max-w-xs"
                aria-label="Test recipient"
              />
              <Button
                size="sm"
                variant="whatsapp"
                onClick={() => test.mutate()}
                loading={test.isPending}
                disabled={!testTo || !canManage}
              >
                <Send /> Send test
              </Button>
            </div>
          </div>
        </div>
        {canManage ? (
          <details className="rounded-lg border border-border p-3 text-sm">
            <summary className="cursor-pointer font-medium">Pinned version, credentials & limits</summary>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <Field label="Pinned version (rollback)">
                <Select
                  value={d.agent_version_id}
                  onValueChange={(v) => patch.mutate({ agent_version_id: v })}
                  options={versions.map((v) => ({ value: v.id, label: `v${v.version_number}` }))}
                />
              </Field>
              <Field label="Per-contact limit (msgs/min)">
                <Input
                  type="number"
                  defaultValue={d.rate_limits.contact_per_minute ?? 20}
                  onBlur={(e) =>
                    patch.mutate({ rate_limits: { contact_per_minute: Number(e.target.value) } })
                  }
                />
              </Field>
              <Field label="New access token" hint={`Current ends with ····${d.token_last4 ?? "????"}`}>
                <Input
                  type="password"
                  value={creds.access_token}
                  onChange={(e) => setCreds({ ...creds, access_token: e.target.value })}
                  autoComplete="off"
                />
              </Field>
              <Field label="New app secret">
                <Input
                  type="password"
                  value={creds.app_secret}
                  onChange={(e) => setCreds({ ...creds, app_secret: e.target.value })}
                  autoComplete="off"
                />
              </Field>
            </div>
            <Button
              size="sm"
              className="mt-3"
              variant="outline"
              disabled={!creds.access_token && !creds.app_secret}
              onClick={() =>
                patch.mutate({
                  access_token: creds.access_token || undefined,
                  app_secret: creds.app_secret || undefined,
                })
              }
            >
              Update credentials
            </Button>
          </details>
        ) : null}
        {contacts.data?.length ? (
          <div>
            <p className="mb-2 text-sm font-medium">Contacts</p>
            <Table>
              <THead>
                <tr>
                  <Th>Number</Th>
                  <Th>Name</Th>
                  <Th>Messages</Th>
                  <Th>Last seen</Th>
                  <Th />
                </tr>
              </THead>
              <TBody>
                {contacts.data.map((c) => (
                  <Tr key={c.id}>
                    <Td className="font-mono text-xs">{c.wa_id}</Td>
                    <Td>{c.profile_name ?? "—"}</Td>
                    <Td>{c.message_count}</Td>
                    <Td className="text-muted-foreground">{formatDate(c.last_seen_at)}</Td>
                    <Td>
                      {canManage ? (
                        <Button
                          size="sm"
                          variant={c.blocked ? "outline" : "ghost"}
                          onClick={async () => {
                            await api(`/orgs/${orgId}/deployments/${d.id}/contacts/${c.id}`, {
                              method: "PATCH",
                              body: { blocked: !c.blocked },
                            });
                            contacts.refetch();
                          }}
                        >
                          {c.blocked ? "Unblock" : "Block"}
                        </Button>
                      ) : null}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
        ) : null}
      </CardContent>
      <ConfirmDialog
        open={del}
        onOpenChange={setDel}
        title="Delete this deployment?"
        description="The WhatsApp number stops answering immediately."
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
      />
    </Card>
  );
}
