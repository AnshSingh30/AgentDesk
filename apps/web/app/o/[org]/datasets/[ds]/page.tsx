"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, Eye, EyeOff, Play, Plus, RefreshCw, Sparkles, Trash2, Wand2 } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog, PageHeader, SqlCode } from "@/components/shared";
import { Button } from "@/components/ui/button";
import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  KeyValue,
  Progress,
  Skeleton,
  StatusBadge,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui/display";
import {
  Checkbox,
  Field,
  Input,
  Select,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
} from "@/components/ui/primitives";
import { Dropzone, putPresigned, type PresignedUpload } from "@/components/uploader";
import { useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import { useRole } from "@/lib/auth";
import type { DatasetColumnOut, DatasetSchemaOut } from "@/lib/types";
import { cn, formatDate, formatNumber } from "@/lib/utils";

const SEMANTIC = [
  "id",
  "foreign_key",
  "name",
  "email",
  "phone",
  "date",
  "datetime",
  "currency",
  "percentage",
  "category",
  "free_text",
  "boolean",
  "numeric_measure",
  "sensitive_personal",
];

export default function DatasetPage() {
  const { ds: dsId } = useParams<{ ds: string }>();
  const orgId = useOrgId();
  const qc = useQueryClient();
  const router = useRouter();
  const { can } = useRole(orgId);
  const schema = useQuery({
    queryKey: ["schema", orgId, dsId],
    queryFn: () => api<DatasetSchemaOut>(`/orgs/${orgId}/datasets/${dsId}/schema`),
    refetchInterval: (q) =>
      ["connecting", "introspecting", "drafting", "importing"].includes(q.state.data?.dataset.status ?? "")
        ? 3000
        : false,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["schema", orgId, dsId] });
    qc.invalidateQueries({ queryKey: ["datasets", orgId] });
  };
  const action = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      api(`/orgs/${orgId}/datasets/${dsId}/${path}`, { method: "POST", body: body ?? {} }),
    onSuccess: (_, v) => {
      refresh();
      toast.success(
        {
          introspect: "Schema re-sync started",
          "draft-descriptions": "AI drafting started",
          "tables/bulk-review": "All tables marked reviewed",
        }[v.path] ?? "Done",
      );
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const [del, setDel] = React.useState(false);
  const remove = useMutation({
    mutationFn: () => api(`/orgs/${orgId}/datasets/${dsId}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["datasets", orgId] });
      router.push(`/o/${orgId}/datasets`);
    },
  });
  if (schema.isPending) return <Skeleton className="h-64" />;
  if (schema.isError) return <Alert variant="destructive">{errorMessage(schema.error)}</Alert>;
  const s = schema.data!;
  const d = s.dataset;
  const exposed = s.tables.filter((t) => t.exposed);
  const reviewed = exposed.filter((t) => t.reviewed).length;
  const busy = ["introspecting", "drafting", "importing"].includes(d.status);
  const needsUpload = d.type === "spreadsheet" && !d.storage_key;

  return (
    <div>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            {d.name} <StatusBadge status={d.status} />
          </span>
        }
        description={`${d.type === "postgres" ? `PostgreSQL · ${String(d.connection_config.host ?? "")}` : "Spreadsheet (DuckDB)"} · schema v${d.current_schema_version}`}
        actions={
          can("editor") ? (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => action.mutate({ path: "introspect" })}
                disabled={busy || needsUpload}
                loading={action.isPending && action.variables?.path === "introspect"}
              >
                <RefreshCw /> Re-sync schema
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => action.mutate({ path: "draft-descriptions", body: {} })}
                disabled={busy || !s.tables.length}
              >
                <Sparkles /> Draft descriptions (AI)
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => action.mutate({ path: "tables/bulk-review", body: {} })}
                disabled={busy || !s.tables.length}
              >
                <CheckCheck /> Mark all reviewed
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setDel(true)} aria-label="Delete dataset">
                <Trash2 />
              </Button>
            </>
          ) : null
        }
      />
      {d.error ? (
        <Alert variant="destructive" className="mb-4">
          {d.error}
        </Alert>
      ) : null}
      {busy ? (
        <Alert variant="info" className="mb-4">
          Working: {d.status}… this page refreshes automatically.
        </Alert>
      ) : null}
      {exposed.length ? (
        <div className="mb-4 flex items-center gap-3 text-sm">
          <Progress value={(reviewed / exposed.length) * 100} className="max-w-xs" label="Review progress" />
          <span className="text-muted-foreground">
            {reviewed}/{exposed.length} tables reviewed
          </span>
        </div>
      ) : null}
      {needsUpload ? (
        <SpreadsheetImport dsId={dsId} onDone={refresh} />
      ) : (
        <Tabs defaultValue="schema">
          <TabsList className="flex-wrap">
            <TabsTrigger value="schema">Schema review</TabsTrigger>
            <TabsTrigger value="relationships">Relationships</TabsTrigger>
            <TabsTrigger value="glossary">Glossary</TabsTrigger>
            <TabsTrigger value="examples">Example queries</TabsTrigger>
            <TabsTrigger value="runner">SQL runner</TabsTrigger>
            <TabsTrigger value="connection">Connection</TabsTrigger>
          </TabsList>
          <TabsContent value="schema" className="mt-4">
            <SchemaReview s={s} onChange={refresh} canEdit={can("editor")} />
          </TabsContent>
          <TabsContent value="relationships" className="mt-4">
            <Relationships s={s} onChange={refresh} canEdit={can("editor")} />
          </TabsContent>
          <TabsContent value="glossary" className="mt-4">
            <Glossary s={s} onChange={refresh} canEdit={can("editor")} />
          </TabsContent>
          <TabsContent value="examples" className="mt-4">
            <Examples s={s} onChange={refresh} canEdit={can("editor")} />
          </TabsContent>
          <TabsContent value="runner" className="mt-4">
            <SqlRunner dsId={dsId} />
          </TabsContent>
          <TabsContent value="connection" className="mt-4">
            <Connection s={s} onChange={refresh} canEdit={can("editor")} />
          </TabsContent>
        </Tabs>
      )}
      <ConfirmDialog
        open={del}
        onOpenChange={setDel}
        title={`Delete "${d.name}"?`}
        description="Agents using this dataset will fail validation."
        confirmText={d.name}
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
      />
    </div>
  );
}

/* ---------------------------------------------------------------- schema review (two-pane) */
function SchemaReview({
  s,
  onChange,
  canEdit,
}: {
  s: DatasetSchemaOut;
  onChange: () => void;
  canEdit: boolean;
}) {
  const orgId = useOrgId();
  const [q, setQ] = React.useState("");
  const [sel, setSel] = React.useState<string | null>(s.tables[0]?.id ?? null);
  const table = s.tables.find((t) => t.id === sel) ?? s.tables[0];
  const patchTable = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api(`/orgs/${orgId}/datasets/${s.dataset.id}/tables/${id}`, { method: "PATCH", body }),
    onSuccess: onChange,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const patchColumn = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api(`/orgs/${orgId}/datasets/${s.dataset.id}/columns/${id}`, { method: "PATCH", body }),
    onSuccess: onChange,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const regen = useMutation({
    mutationFn: (id: string) =>
      api(`/orgs/${orgId}/datasets/${s.dataset.id}/draft-descriptions`, {
        method: "POST",
        body: { table_ids: [id], overwrite: true },
      }),
    onSuccess: () => toast.success("Regenerating description…"),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const [desc, setDesc] = React.useState(table?.description ?? "");
  React.useEffect(() => setDesc(table?.description ?? ""), [table?.id, table?.description]);
  if (!s.tables.length)
    return <p className="text-sm text-muted-foreground">No tables yet. Run a schema sync.</p>;
  const list = s.tables.filter((t) => t.table_name.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
      <Card className="p-3">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search tables"
          className="mb-2"
          aria-label="Search tables"
        />
        <ul className="max-h-[70vh] space-y-0.5 overflow-y-auto">
          {list.map((t) => (
            <li key={t.id}>
              <button
                onClick={() => setSel(t.id)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                  t.id === table?.id && "bg-muted font-medium",
                )}
              >
                <span className={cn("flex-1 truncate", !t.exposed && "text-muted-foreground line-through")}>
                  {t.table_name}
                </span>
                {t.reviewed ? (
                  <Badge variant="success">reviewed</Badge>
                ) : t.description_source === "ai_draft" ? (
                  <Badge variant="warning">AI draft</Badge>
                ) : (
                  <Badge variant="secondary">todo</Badge>
                )}
                {t.hidden_columns ? <Badge variant="outline">{t.hidden_columns} hidden</Badge> : null}
              </button>
            </li>
          ))}
        </ul>
      </Card>
      {table ? (
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-3">
            <div>
              <CardTitle className="font-mono">
                {table.schema_name}.{table.table_name}
              </CardTitle>
              <CardDescription>
                {table.kind} · ~{formatNumber(table.row_estimate)} rows · {table.columns.length} columns
              </CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              <label className="flex items-center gap-2 text-xs">
                <Switch
                  checked={table.exposed}
                  disabled={!canEdit}
                  onCheckedChange={(v) => patchTable.mutate({ id: table.id, body: { exposed: v } })}
                />{" "}
                Exposed to agent
              </label>
              <Button size="sm" variant="outline" disabled={!canEdit} onClick={() => regen.mutate(table.id)}>
                <Wand2 /> Regenerate
              </Button>
              <Button
                size="sm"
                variant={table.reviewed ? "outline" : "default"}
                disabled={!canEdit}
                onClick={() => patchTable.mutate({ id: table.id, body: { reviewed: !table.reviewed } })}
              >
                {table.reviewed ? "Unmark reviewed" : "Mark reviewed"}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field
              label="Table description"
              hint={table.description_source ? `source: ${table.description_source}` : undefined}
            >
              <Textarea
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                rows={2}
                disabled={!canEdit}
                onBlur={() =>
                  desc !== (table.description ?? "") &&
                  patchTable.mutate({ id: table.id, body: { description: desc } })
                }
              />
            </Field>
            <Table>
              <THead>
                <tr>
                  <Th>Column</Th>
                  <Th>Type</Th>
                  <Th>Description</Th>
                  <Th>Semantic</Th>
                  <Th>Hidden</Th>
                </tr>
              </THead>
              <TBody>
                {table.columns.map((c) => (
                  <ColumnRow
                    key={c.id}
                    c={c}
                    canEdit={canEdit}
                    onPatch={(body) => patchColumn.mutate({ id: c.id, body })}
                  />
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function ColumnRow({
  c,
  canEdit,
  onPatch,
}: {
  c: DatasetColumnOut;
  canEdit: boolean;
  onPatch: (body: Record<string, unknown>) => void;
}) {
  const [desc, setDesc] = React.useState(c.description ?? "");
  React.useEffect(() => setDesc(c.description ?? ""), [c.description]);
  const samples = (c.sample_values ?? []) as unknown[];
  const sampleNote = samples.length === 1 && String(samples[0]).startsWith("sampling");
  return (
    <Tr className={c.hidden ? "opacity-60" : ""}>
      <Td>
        <p className="font-mono text-xs font-medium">
          {c.column_name} {c.is_pk ? <Badge variant="outline">PK</Badge> : null}
        </p>
        {samples.length && !sampleNote ? (
          <div className="mt-1 flex flex-wrap gap-1">
            {samples.slice(0, 5).map((v, i) => (
              <span key={i} className="rounded bg-muted px-1.5 py-0.5 text-[10px]">
                {String(v).slice(0, 24)}
              </span>
            ))}
          </div>
        ) : sampleNote ? (
          <p className="mt-1 text-[10px] text-muted-foreground">{String(samples[0])}</p>
        ) : null}
      </Td>
      <Td className="font-mono text-xs text-muted-foreground">
        {c.data_type}
        {c.nullable ? "" : " NOT NULL"}
      </Td>
      <Td className="min-w-[220px]">
        <Input
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          disabled={!canEdit}
          onBlur={() => desc !== (c.description ?? "") && onPatch({ description: desc })}
          className="h-8 text-xs"
          aria-label={`Description for ${c.column_name}`}
        />
        {c.description_source ? (
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            {c.description_source}
            {c.reviewed ? " · reviewed" : ""}
          </p>
        ) : null}
      </Td>
      <Td className="min-w-[140px]">
        <Select
          value={c.semantic_type ?? undefined}
          onValueChange={(v) => onPatch({ semantic_type: v })}
          options={SEMANTIC.map((s) => ({ value: s, label: s }))}
          placeholder="—"
          disabled={!canEdit}
          className="h-8 text-xs"
        />
        {c.semantic_type === "sensitive_personal" && !c.hidden ? (
          <p className="mt-0.5 text-[10px] text-warning">suggested hidden</p>
        ) : null}
      </Td>
      <Td>
        <button
          disabled={!canEdit}
          onClick={() => onPatch({ hidden: !c.hidden })}
          className="rounded p-1 hover:bg-muted disabled:opacity-50"
          aria-label={c.hidden ? "Unhide column" : "Hide column"}
          title={c.hidden ? "Hidden: excluded from prompts and blocked in SQL" : "Visible"}
        >
          {c.hidden ? (
            <EyeOff className="h-4 w-4 text-destructive" />
          ) : (
            <Eye className="h-4 w-4 text-muted-foreground" />
          )}
        </button>
      </Td>
    </Tr>
  );
}

/* ---------------------------------------------------------------- relationships (SVG diagram + list) */
function Relationships({
  s,
  onChange,
  canEdit,
}: {
  s: DatasetSchemaOut;
  onChange: () => void;
  canEdit: boolean;
}) {
  const orgId = useOrgId();
  const tables = s.tables.filter((t) => t.exposed);
  const byId = Object.fromEntries(s.tables.map((t) => [t.id, t]));
  const [form, setForm] = React.useState({
    from_table_id: "",
    from_column: "",
    to_table_id: "",
    to_column: "",
  });
  const add = useMutation({
    mutationFn: () =>
      api(`/orgs/${orgId}/datasets/${s.dataset.id}/relationships`, { method: "POST", body: form }),
    onSuccess: () => {
      onChange();
      setForm({ from_table_id: "", from_column: "", to_table_id: "", to_column: "" });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      api(`/orgs/${orgId}/datasets/${s.dataset.id}/relationships/${id}`, { method: "DELETE" }),
    onSuccess: onChange,
  });
  const cols = 4;
  const W = 190;
  const H = 54;
  const GX = 60;
  const GY = 50;
  const pos = Object.fromEntries(
    tables.map((t, i) => [t.id, { x: 20 + (i % cols) * (W + GX), y: 20 + Math.floor(i / cols) * (H + GY) }]),
  );
  const width = 40 + Math.min(tables.length, cols) * (W + GX);
  const height = 40 + Math.ceil(tables.length / cols) * (H + GY);
  const colOptions = (tid: string) =>
    (byId[tid]?.columns ?? []).map((c) => ({ value: c.column_name, label: c.column_name }));
  return (
    <div className="space-y-4">
      <Card className="overflow-x-auto p-3">
        <svg width={width} height={height} className="text-xs" role="img" aria-label="Relationship diagram">
          {s.relationships.map((r) => {
            const a = pos[r.from_table_id];
            const b = pos[r.to_table_id];
            if (!a || !b) return null;
            const ax = a.x + W / 2;
            const ay = a.y + H / 2;
            const bx = b.x + W / 2;
            const by = b.y + H / 2;
            return (
              <g key={r.id}>
                <line
                  x1={ax}
                  y1={ay}
                  x2={bx}
                  y2={by}
                  stroke={
                    r.type === "fk"
                      ? "var(--primary)"
                      : r.type === "user"
                        ? "var(--success)"
                        : "var(--warning)"
                  }
                  strokeWidth={1.5}
                  strokeDasharray={r.type === "inferred" ? "4 3" : undefined}
                />
                <text
                  x={(ax + bx) / 2}
                  y={(ay + by) / 2 - 4}
                  textAnchor="middle"
                  fill="var(--muted-foreground)"
                  fontSize={9}
                >
                  {r.from_column} → {r.to_column}
                </text>
              </g>
            );
          })}
          {tables.map((t) => (
            <g key={t.id} transform={`translate(${pos[t.id].x},${pos[t.id].y})`}>
              <rect width={W} height={H} rx={8} fill="var(--card)" stroke="var(--border)" />
              <text x={10} y={22} fontWeight={600} fill="var(--foreground)">
                {t.table_name}
              </text>
              <text x={10} y={40} fill="var(--muted-foreground)" fontSize={10}>
                {t.columns.length} columns · {t.reviewed ? "reviewed" : "unreviewed"}
              </text>
            </g>
          ))}
        </svg>
        <p className="mt-2 text-xs text-muted-foreground">
          Solid teal = foreign key · dashed = AI-inferred · green = added by you
        </p>
      </Card>
      <Table>
        <THead>
          <tr>
            <Th>From</Th>
            <Th>To</Th>
            <Th>Type</Th>
            <Th />
          </tr>
        </THead>
        <TBody>
          {s.relationships.map((r) => (
            <Tr key={r.id}>
              <Td className="font-mono text-xs">
                {byId[r.from_table_id]?.table_name}.{r.from_column}
              </Td>
              <Td className="font-mono text-xs">
                {byId[r.to_table_id]?.table_name}.{r.to_column}
              </Td>
              <Td>
                <Badge variant={r.type === "fk" ? "default" : r.type === "user" ? "success" : "warning"}>
                  {r.type}
                  {r.confidence != null && r.type === "inferred" ? ` ${Math.round(r.confidence * 100)}%` : ""}
                </Badge>
              </Td>
              <Td>
                {canEdit && r.type !== "fk" ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => remove.mutate(r.id)}
                    aria-label="Remove relationship"
                  >
                    <Trash2 />
                  </Button>
                ) : null}
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
      {canEdit ? (
        <Card className="p-4">
          <p className="mb-3 text-sm font-medium">Add relationship</p>
          <div className="grid gap-2 sm:grid-cols-5">
            <Select
              value={form.from_table_id || undefined}
              onValueChange={(v) => setForm({ ...form, from_table_id: v, from_column: "" })}
              options={s.tables.map((t) => ({ value: t.id, label: t.table_name }))}
              placeholder="From table"
            />
            <Select
              value={form.from_column || undefined}
              onValueChange={(v) => setForm({ ...form, from_column: v })}
              options={colOptions(form.from_table_id)}
              placeholder="Column"
              disabled={!form.from_table_id}
            />
            <Select
              value={form.to_table_id || undefined}
              onValueChange={(v) => setForm({ ...form, to_table_id: v, to_column: "" })}
              options={s.tables.map((t) => ({ value: t.id, label: t.table_name }))}
              placeholder="To table"
            />
            <Select
              value={form.to_column || undefined}
              onValueChange={(v) => setForm({ ...form, to_column: v })}
              options={colOptions(form.to_table_id)}
              placeholder="Column"
              disabled={!form.to_table_id}
            />
            <Button
              onClick={() => add.mutate()}
              disabled={!form.from_column || !form.to_column}
              loading={add.isPending}
            >
              <Plus /> Add
            </Button>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- glossary */
function Glossary({ s, onChange, canEdit }: { s: DatasetSchemaOut; onChange: () => void; canEdit: boolean }) {
  const orgId = useOrgId();
  const [term, setTerm] = React.useState("");
  const [def, setDef] = React.useState("");
  const add = useMutation({
    mutationFn: () =>
      api(`/orgs/${orgId}/datasets/${s.dataset.id}/glossary`, {
        method: "POST",
        body: { term, definition: def },
      }),
    onSuccess: () => {
      onChange();
      setTerm("");
      setDef("");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      api(`/orgs/${orgId}/datasets/${s.dataset.id}/glossary/${id}`, { method: "DELETE" }),
    onSuccess: onChange,
  });
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Business definitions the agent uses when writing SQL, e.g. &quot;attrition rate = exits / average
        headcount&quot;.
      </p>
      {s.glossary.length ? (
        <Table>
          <THead>
            <tr>
              <Th>Term</Th>
              <Th>Definition</Th>
              <Th />
            </tr>
          </THead>
          <TBody>
            {s.glossary.map((g) => (
              <Tr key={g.id}>
                <Td className="font-medium">{g.term}</Td>
                <Td>{g.definition}</Td>
                <Td>
                  {canEdit ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => remove.mutate(g.id)}
                      aria-label="Remove term"
                    >
                      <Trash2 />
                    </Button>
                  ) : null}
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      ) : null}
      {canEdit ? (
        <Card className="p-4">
          <div className="grid gap-2 sm:grid-cols-[1fr_2fr_auto]">
            <Input
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              placeholder="Term"
              aria-label="Term"
            />
            <Input
              value={def}
              onChange={(e) => setDef(e.target.value)}
              placeholder="Definition"
              aria-label="Definition"
            />
            <Button
              onClick={() => add.mutate()}
              disabled={!term.trim() || !def.trim()}
              loading={add.isPending}
            >
              <Plus /> Add
            </Button>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- example queries */
function Examples({ s, onChange, canEdit }: { s: DatasetSchemaOut; onChange: () => void; canEdit: boolean }) {
  const orgId = useOrgId();
  const [q, setQ] = React.useState("");
  const [sql, setSql] = React.useState("");
  const add = useMutation({
    mutationFn: () =>
      api<{ validated_at: string | null; last_error: string | null }>(
        `/orgs/${orgId}/datasets/${s.dataset.id}/example-queries`,
        { method: "POST", body: { question: q, sql } },
      ),
    onSuccess: (r) => {
      onChange();
      setQ("");
      setSql("");
      if (r.validated_at) toast.success("Example validated by running it");
      else toast.warning(`Saved, but validation failed: ${r.last_error}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const validate = useMutation({
    mutationFn: (id: string) =>
      api(`/orgs/${orgId}/datasets/${s.dataset.id}/example-queries/${id}/validate`, { method: "POST" }),
    onSuccess: onChange,
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      api(`/orgs/${orgId}/datasets/${s.dataset.id}/example-queries/${id}`, { method: "DELETE" }),
    onSuccess: onChange,
  });
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Question → SQL pairs used as few-shot examples. Each is validated by running it (read-only).
      </p>
      <div className="space-y-2">
        {s.example_queries.map((e) => (
          <Card key={e.id} className="p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{e.question}</p>
                <SqlCode sql={e.sql} className="mt-2" />
                {e.last_error ? <p className="mt-1 text-xs text-destructive">{e.last_error}</p> : null}
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Badge variant={e.validated_at ? "success" : "destructive"}>
                  {e.validated_at ? "validated" : "invalid"}
                </Badge>
                {canEdit ? (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => validate.mutate(e.id)}
                      aria-label="Validate"
                    >
                      <Play />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => remove.mutate(e.id)} aria-label="Remove">
                      <Trash2 />
                    </Button>
                  </>
                ) : null}
              </div>
            </div>
          </Card>
        ))}
      </div>
      {canEdit ? (
        <Card className="space-y-2 p-4">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Natural-language question"
            aria-label="Question"
          />
          <Textarea
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            placeholder="SELECT …"
            rows={3}
            className="font-mono text-xs"
            aria-label="SQL"
          />
          <Button onClick={() => add.mutate()} disabled={!q.trim() || !sql.trim()} loading={add.isPending}>
            <Plus /> Add example
          </Button>
        </Card>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- SQL runner */
function SqlRunner({ dsId }: { dsId: string }) {
  const orgId = useOrgId();
  const [sql, setSql] = React.useState("SELECT * FROM ");
  const run = useMutation({
    mutationFn: () =>
      api<{
        validated_sql: string;
        columns: string[];
        rows: unknown[][];
        row_count: number;
        truncated: boolean;
        duration_ms: number;
        warnings: string[];
      }>(`/orgs/${orgId}/datasets/${dsId}/query`, { method: "POST", body: { sql, max_rows: 500 } }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Runs through the same validator as the agent: read-only, exposed tables only, hidden columns blocked,
        LIMIT enforced.
      </p>
      <Textarea
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        rows={5}
        className="font-mono text-xs"
        aria-label="SQL"
      />
      <Button onClick={() => run.mutate()} loading={run.isPending}>
        <Play /> Run
      </Button>
      {run.data ? (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            {run.data.row_count} rows in {run.data.duration_ms} ms {run.data.truncated ? "(truncated)" : ""}{" "}
            {run.data.warnings.join(" ")}
          </p>
          <SqlCode sql={run.data.validated_sql} />
          <Table>
            <THead>
              <tr>
                {run.data.columns.map((c) => (
                  <Th key={c}>{c}</Th>
                ))}
              </tr>
            </THead>
            <TBody>
              {run.data.rows.map((r, i) => (
                <Tr key={i}>
                  {r.map((v, j) => (
                    <Td key={j} className="font-mono text-xs">
                      {String(v ?? "")}
                    </Td>
                  ))}
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- connection */
function Connection({
  s,
  onChange,
  canEdit,
}: {
  s: DatasetSchemaOut;
  onChange: () => void;
  canEdit: boolean;
}) {
  const orgId = useOrgId();
  const d = s.dataset;
  const [password, setPassword] = React.useState("");
  const [sampling, setSampling] = React.useState(d.allow_sampling);
  const versions = useQuery({
    queryKey: ["schema-versions", orgId, d.id],
    queryFn: () =>
      api<{ version: number; diff: Record<string, unknown[]>; created_at: string }[]>(
        `/orgs/${orgId}/datasets/${d.id}/schema-versions`,
      ),
  });
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api(`/orgs/${orgId}/datasets/${d.id}`, { method: "PATCH", body }),
    onSuccess: () => {
      onChange();
      toast.success("Saved");
      setPassword("");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const cfg = d.connection_config;
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Connection</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {d.type === "postgres" ? (
            <KeyValue
              items={[
                { k: "Host", v: String(cfg.host) },
                { k: "Port", v: String(cfg.port) },
                { k: "Database", v: String(cfg.database) },
                { k: "Username", v: String(cfg.username) },
                { k: "Password", v: d.password_configured ? "configured (hidden)" : "not set" },
                { k: "SSL mode", v: String(cfg.sslmode) },
                { k: "Schemas", v: ((cfg.schemas as string[]) ?? []).join(", ") || "all" },
              ]}
            />
          ) : (
            <KeyValue
              items={[
                { k: "Storage", v: d.storage_key ?? "—" },
                {
                  k: "Import report",
                  v: JSON.stringify((cfg.import_report as Record<string, unknown>)?.tables ?? []),
                },
              ]}
            />
          )}
          {canEdit && d.type === "postgres" ? (
            <div className="flex flex-wrap items-end gap-2">
              <Field label="New password">
                <Input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="off"
                />
              </Field>
              <Button
                variant="outline"
                disabled={!password}
                onClick={() => patch.mutate({ connection: { ...cfg, password } })}
              >
                Update password
              </Button>
            </div>
          ) : null}
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={sampling}
              disabled={!canEdit}
              onCheckedChange={(v) => {
                setSampling(v);
                patch.mutate({ allow_sampling: v });
              }}
            />{" "}
            Allow sampling values (up to 5 distinct values per column, sensitive names skipped)
          </label>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Schema versions</CardTitle>
          <CardDescription>Each re-sync records a diff; existing descriptions are preserved.</CardDescription>
        </CardHeader>
        <CardContent>
          {versions.data?.length ? (
            <ul className="space-y-2 text-sm">
              {versions.data.map((v) => {
                const counts = Object.entries(v.diff)
                  .map(([k, arr]) => `${(arr as unknown[]).length} ${k.replace(/_/g, " ")}`)
                  .filter((x) => !x.startsWith("0 "));
                return (
                  <li
                    key={v.version}
                    className="flex items-center justify-between border-b border-border py-1.5"
                  >
                    <span>
                      v{v.version} · {formatDate(v.created_at)}
                    </span>
                    <span className="text-xs text-muted-foreground">{counts.join(", ") || "no changes"}</span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No versions yet.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- spreadsheet import */
type Preview = {
  filename: string;
  sheets: {
    name: string;
    table_name: string;
    header_row: number;
    row_count: number;
    columns: { source_index: number; source_name: string; name: string; type: string; samples: string[] }[];
    rows: (string | null)[][];
    warnings: string[];
  }[];
};

function SpreadsheetImport({ dsId, onDone }: { dsId: string; onDone: () => void }) {
  const orgId = useOrgId();
  const [key, setKey] = React.useState<string | null>(null);
  const [preview, setPreview] = React.useState<Preview | null>(null);
  const [plan, setPlan] = React.useState<Preview["sheets"] | null>(null);
  const [include, setInclude] = React.useState<Record<string, boolean>>({});
  const [busy, setBusy] = React.useState(false);
  const onFiles = async (files: File[]) => {
    const f = files[0];
    setBusy(true);
    try {
      const p = await api<PresignedUpload & { key: string }>(
        `/orgs/${orgId}/datasets/${dsId}/spreadsheet/uploads`,
        { method: "POST", body: { name: f.name, size: f.size } },
      );
      await putPresigned(f, p);
      const pv = await api<Preview>(`/orgs/${orgId}/datasets/${dsId}/spreadsheet/preview`, {
        method: "POST",
        body: { key: p.key },
      });
      setKey(p.key);
      setPreview(pv);
      setPlan(pv.sheets);
      setInclude(Object.fromEntries(pv.sheets.map((s) => [s.name, true])));
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const importIt = useMutation({
    mutationFn: () =>
      api(`/orgs/${orgId}/datasets/${dsId}/spreadsheet/import`, {
        method: "POST",
        body: {
          key,
          plan: {
            sheets: plan!.map((s) => ({
              name: s.name,
              table_name: s.table_name,
              header_row: s.header_row,
              include: include[s.name],
              columns: s.columns.map((c) => ({ source_index: c.source_index, name: c.name, type: c.type })),
            })),
          },
        },
      }),
    onSuccess: () => {
      toast.success("Import started");
      onDone();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="space-y-4">
      {!preview ? (
        <Dropzone
          accept={{
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx", ".xlsm"],
            "text/csv": [".csv"],
          }}
          maxSizeMb={50}
          multiple={false}
          onFiles={onFiles}
          busy={busy}
          hint="XLSX or CSV · up to 50 MB · 20 sheets · 1M rows per sheet"
        />
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            Review each sheet: header row, column names and types. You can rename columns and change types
            before import.
          </p>
          {plan!.map((sheet, si) => (
            <Card key={sheet.name}>
              <CardHeader className="flex-row items-center justify-between">
                <div className="flex items-center gap-3">
                  <Checkbox
                    checked={include[sheet.name]}
                    onCheckedChange={(v) => setInclude({ ...include, [sheet.name]: v === true })}
                    aria-label={`Include ${sheet.name}`}
                  />
                  <div>
                    <CardTitle>{sheet.name}</CardTitle>
                    <CardDescription>
                      {sheet.row_count} rows · header row {sheet.header_row + 1}
                    </CardDescription>
                  </div>
                </div>
                <Field label="Table name">
                  <Input
                    value={sheet.table_name}
                    onChange={(e) =>
                      setPlan(plan!.map((s, i) => (i === si ? { ...s, table_name: e.target.value } : s)))
                    }
                    className="h-8 w-48 font-mono text-xs"
                  />
                </Field>
              </CardHeader>
              <CardContent>
                {sheet.warnings.map((w) => (
                  <Alert key={w} variant="warning" className="mb-2">
                    {w}
                  </Alert>
                ))}
                <Table>
                  <THead>
                    <tr>
                      <Th>Source header</Th>
                      <Th>Column name</Th>
                      <Th>Type</Th>
                      <Th>Samples</Th>
                    </tr>
                  </THead>
                  <TBody>
                    {sheet.columns.map((c, ci) => (
                      <Tr key={c.source_index}>
                        <Td className="text-muted-foreground">
                          {c.source_name || `(column ${c.source_index + 1})`}
                        </Td>
                        <Td>
                          <Input
                            value={c.name}
                            onChange={(e) =>
                              setPlan(
                                plan!.map((s, i) =>
                                  i === si
                                    ? {
                                        ...s,
                                        columns: s.columns.map((x, j) =>
                                          j === ci ? { ...x, name: e.target.value } : x,
                                        ),
                                      }
                                    : s,
                                ),
                              )
                            }
                            className="h-8 font-mono text-xs"
                          />
                        </Td>
                        <Td>
                          <Select
                            value={c.type}
                            onValueChange={(v) =>
                              setPlan(
                                plan!.map((s, i) =>
                                  i === si
                                    ? {
                                        ...s,
                                        columns: s.columns.map((x, j) => (j === ci ? { ...x, type: v } : x)),
                                      }
                                    : s,
                                ),
                              )
                            }
                            options={["int", "float", "bool", "date", "datetime", "text"].map((t) => ({
                              value: t,
                              label: t,
                            }))}
                            className="h-8 w-28 text-xs"
                          />
                        </Td>
                        <Td className="text-xs text-muted-foreground">{c.samples.join(", ")}</Td>
                      </Tr>
                    ))}
                  </TBody>
                </Table>
              </CardContent>
            </Card>
          ))}
          <div className="flex gap-2">
            <Button
              onClick={() => importIt.mutate()}
              loading={importIt.isPending}
              disabled={!Object.values(include).some(Boolean)}
            >
              Import into DuckDB
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setPreview(null);
                setPlan(null);
                setKey(null);
              }}
            >
              Choose another file
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
