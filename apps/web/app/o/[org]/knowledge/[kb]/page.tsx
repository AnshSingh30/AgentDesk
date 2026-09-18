"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, FileText, RefreshCw, Search, Trash2 } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog, PageHeader } from "@/components/shared";
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
  StatusBadge,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui/display";
import {
  Dialog,
  DialogContent,
  Field,
  Input,
  Select,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
  Tooltip,
} from "@/components/ui/primitives";
import { Dropzone, putPresigned, UploadProgressList, type PresignedUpload } from "@/components/uploader";
import { useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import { useRole } from "@/lib/auth";
import type { DocumentOut, KBOut } from "@/lib/types";
import { bytes, formatDate } from "@/lib/utils";

const ACCEPT = {
  "application/pdf": [".pdf"],
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
  "text/plain": [".txt"],
  "text/markdown": [".md"],
};
const STEPS = ["queued", "parsing", "chunking", "embedding", "ready"];

export default function KnowledgeBasePage() {
  const { kb: kbId } = useParams<{ kb: string }>();
  const orgId = useOrgId();
  const qc = useQueryClient();
  const router = useRouter();
  const { can } = useRole(orgId);
  const kb = useQuery({
    queryKey: ["kb", orgId, kbId],
    queryFn: () => api<KBOut>(`/orgs/${orgId}/knowledge-bases/${kbId}`),
  });
  const docs = useQuery({
    queryKey: ["docs", orgId, kbId],
    queryFn: () => api<DocumentOut[]>(`/orgs/${orgId}/knowledge-bases/${kbId}/documents`),
    refetchInterval: (q) =>
      q.state.data?.some((d) => !["ready", "failed"].includes(d.status)) ? 2500 : false,
  });
  const [uploads, setUploads] = React.useState<{ name: string; size: number; pct: number; error?: string }[]>(
    [],
  );
  const [busy, setBusy] = React.useState(false);
  const [chunksFor, setChunksFor] = React.useState<DocumentOut | null>(null);
  const [del, setDel] = React.useState<DocumentOut | null>(null);
  const [delKb, setDelKb] = React.useState(false);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["docs", orgId, kbId] });
    qc.invalidateQueries({ queryKey: ["kb", orgId, kbId] });
    qc.invalidateQueries({ queryKey: ["kbs", orgId] });
  };

  const onFiles = async (files: File[]) => {
    setBusy(true);
    setUploads(files.map((f) => ({ name: f.name, size: f.size, pct: 0 })));
    try {
      const presigned = await api<{ uploads: PresignedUpload[] }>(
        `/orgs/${orgId}/knowledge-bases/${kbId}/uploads`,
        {
          method: "POST",
          body: { files: files.map((f) => ({ name: f.name, size: f.size, mime: f.type })) },
        },
      );
      const done: PresignedUpload[] = [];
      for (const p of presigned.uploads) {
        const file = files.find((f) => f.name === p.name) ?? files[presigned.uploads.indexOf(p)];
        try {
          await putPresigned(file, p, (pct) =>
            setUploads((u) => u.map((x) => (x.name === file.name ? { ...x, pct } : x))),
          );
          done.push(p);
        } catch (e) {
          setUploads((u) => u.map((x) => (x.name === file.name ? { ...x, error: errorMessage(e) } : x)));
        }
      }
      if (done.length) {
        await api(`/orgs/${orgId}/knowledge-bases/${kbId}/documents`, {
          method: "POST",
          body: { uploads: done.map((d) => ({ document_id: d.document_id, name: d.name, mime: d.mime })) },
        });
        toast.success(`${done.length} file(s) queued for ingestion`);
      }
      refresh();
    } finally {
      setBusy(false);
      setTimeout(() => setUploads([]), 1500);
    }
  };

  const retry = useMutation({
    mutationFn: (id: string) => api(`/orgs/${orgId}/documents/${id}/retry`, { method: "POST" }),
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/orgs/${orgId}/documents/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setDel(null);
      refresh();
      toast.success("Document deleted");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const removeKb = useMutation({
    mutationFn: () => api(`/orgs/${orgId}/knowledge-bases/${kbId}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kbs", orgId] });
      router.push(`/o/${orgId}/knowledge`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (kb.isPending) return <Skeleton className="h-64" />;
  if (kb.isError) return <Alert variant="destructive">{errorMessage(kb.error)}</Alert>;
  const k = kb.data!;

  return (
    <div>
      <PageHeader
        title={k.name}
        description={`${k.doc_count} documents · ${k.chunk_count} chunks · embeddings: ${k.embedding_model}`}
        actions={
          can("editor") ? (
            <Button variant="ghost" onClick={() => setDelKb(true)}>
              <Trash2 /> Delete
            </Button>
          ) : null
        }
      />
      <Tabs defaultValue="documents">
        <TabsList>
          <TabsTrigger value="documents">Documents</TabsTrigger>
          <TabsTrigger value="search">Test retrieval</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>
        <TabsContent value="documents" className="mt-4 space-y-4">
          {can("editor") ? (
            <div>
              <Dropzone
                accept={ACCEPT}
                maxSizeMb={25}
                onFiles={onFiles}
                busy={busy}
                hint="PDF, DOCX, TXT, MD · up to 25 MB per file · 200 files per knowledge base"
              />
              <UploadProgressList items={uploads} />
            </div>
          ) : null}
          {docs.data?.length ? (
            <Table>
              <THead>
                <tr>
                  <Th>File</Th>
                  <Th>Status</Th>
                  <Th>Pages</Th>
                  <Th>Chunks</Th>
                  <Th>Uploaded</Th>
                  <Th />
                </tr>
              </THead>
              <TBody>
                {docs.data.map((d) => (
                  <Tr key={d.id}>
                    <Td>
                      <div className="flex items-center gap-2">
                        <FileText className="h-4 w-4 text-muted-foreground" />
                        <div>
                          <p className="font-medium">
                            {d.filename}{" "}
                            {d.version > 1 ? <Badge variant="secondary">v{d.version}</Badge> : null}
                          </p>
                          <p className="text-xs text-muted-foreground">{bytes(d.size_bytes)}</p>
                        </div>
                      </div>
                    </Td>
                    <Td>
                      <div className="flex items-center gap-2">
                        <StatusBadge status={d.status} />
                        {d.status === "failed" && d.error ? (
                          <Tooltip content={d.error}>
                            <AlertCircle className="h-4 w-4 text-destructive" />
                          </Tooltip>
                        ) : null}
                      </div>
                      {!["ready", "failed"].includes(d.status) ? (
                        <div className="mt-1 flex gap-1" aria-label="Ingestion progress">
                          {STEPS.map((s) => (
                            <span
                              key={s}
                              className={`h-1 w-6 rounded ${STEPS.indexOf(s) <= STEPS.indexOf(d.status) ? "bg-primary" : "bg-muted"}`}
                            />
                          ))}
                        </div>
                      ) : null}
                    </Td>
                    <Td className="text-muted-foreground">{d.page_count ?? "—"}</Td>
                    <Td className="text-muted-foreground">{d.chunk_count || "—"}</Td>
                    <Td className="text-muted-foreground">{formatDate(d.created_at)}</Td>
                    <Td>
                      <div className="flex justify-end gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setChunksFor(d)}
                          disabled={d.status !== "ready"}
                        >
                          Chunks
                        </Button>
                        {can("editor") ? (
                          <>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => retry.mutate(d.id)}
                              disabled={!["failed", "ready"].includes(d.status)}
                              aria-label="Re-ingest"
                            >
                              <RefreshCw />
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setDel(d)}
                              aria-label="Delete document"
                            >
                              <Trash2 />
                            </Button>
                          </>
                        ) : null}
                      </div>
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">No documents yet.</p>
          )}
        </TabsContent>
        <TabsContent value="search" className="mt-4">
          <TestRetrieval kbId={kbId} />
        </TabsContent>
        <TabsContent value="settings" className="mt-4">
          <KbSettings kb={k} onSaved={refresh} canEdit={can("editor")} />
        </TabsContent>
      </Tabs>
      <ChunkViewer doc={chunksFor} onClose={() => setChunksFor(null)} />
      <ConfirmDialog
        open={!!del}
        onOpenChange={(o) => !o && setDel(null)}
        title={`Delete "${del?.filename}"?`}
        description="The file and its vectors are removed."
        onConfirm={() => {
          if (del) remove.mutate(del.id);
        }}
        loading={remove.isPending}
      />
      <ConfirmDialog
        open={delKb}
        onOpenChange={setDelKb}
        title={`Delete "${k.name}"?`}
        description="All documents and vectors are removed. Agents using it will fail validation."
        confirmText={k.name}
        onConfirm={() => removeKb.mutate()}
        loading={removeKb.isPending}
      />
    </div>
  );
}

function TestRetrieval({ kbId }: { kbId: string }) {
  const orgId = useOrgId();
  const [q, setQ] = React.useState("");
  const [topK, setTopK] = React.useState(5);
  const [threshold, setThreshold] = React.useState(0.3);
  const [mode, setMode] = React.useState("hybrid");
  const search = useMutation({
    mutationFn: () =>
      api<{ results: Record<string, unknown>[] }>(`/orgs/${orgId}/knowledge-bases/${kbId}/search`, {
        method: "POST",
        body: { query: q, top_k: topK, threshold, mode },
      }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Test retrieval</CardTitle>
        <CardDescription>
          Runs hybrid search only (no LLM) and shows the top chunks with scores, sources and pages.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (q.trim()) search.mutate();
          }}
        >
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. how many casual leaves do I get"
            className="min-w-[240px] flex-1"
            aria-label="Query"
          />
          <Input
            type="number"
            min={1}
            max={20}
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
            className="w-20"
            aria-label="Top K"
          />
          <Input
            type="number"
            step={0.05}
            min={0}
            max={1}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            className="w-24"
            aria-label="Threshold"
          />
          <div className="w-32">
            <Select
              value={mode}
              onValueChange={setMode}
              options={[
                { value: "hybrid", label: "Hybrid" },
                { value: "dense", label: "Dense" },
                { value: "sparse", label: "Sparse" },
              ]}
            />
          </div>
          <Button type="submit" loading={search.isPending}>
            <Search /> Search
          </Button>
        </form>
        {search.data ? (
          search.data.results.length ? (
            <ul className="space-y-2">
              {search.data.results.map((r, i) => (
                <li key={String(r.chunk_id)} className="rounded-lg border border-border p-3 text-sm">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium">
                      [{i + 1}] {String(r.filename)} {r.page_start ? `· p.${String(r.page_start)}` : ""}{" "}
                      {r.section_title ? `· ${String(r.section_title)}` : ""}
                    </span>
                    <span className="text-muted-foreground">score {Number(r.score).toFixed(3)}</span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-xs">{String(r.text)}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No chunks above the threshold.</p>
          )
        ) : null}
      </CardContent>
    </Card>
  );
}

function ChunkViewer({ doc, onClose }: { doc: DocumentOut | null; onClose: () => void }) {
  const orgId = useOrgId();
  const [q, setQ] = React.useState("");
  const chunks = useQuery({
    queryKey: ["chunks", orgId, doc?.id, q],
    queryFn: () =>
      api<{ count: number; chunks: Record<string, unknown>[] }>(
        `/orgs/${orgId}/documents/${doc!.id}/chunks${q ? `?q=${encodeURIComponent(q)}` : ""}`,
      ),
    enabled: !!doc,
  });
  return (
    <Dialog open={!!doc} onOpenChange={(o) => !o && onClose()}>
      {doc ? (
        <DialogContent
          title={`Chunks · ${doc.filename}`}
          description={`${chunks.data?.count ?? "…"} chunks`}
          wide
        >
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search within chunks"
            className="mb-3"
          />
          <div className="max-h-[60vh] space-y-2 overflow-y-auto">
            {chunks.data?.chunks.map((c) => (
              <div key={String(c.id)} className="rounded-lg border border-border p-3 text-xs">
                <p className="mb-1 text-muted-foreground">
                  #{String(c.chunk_index)} · page {String(c.page_start ?? "—")} ·{" "}
                  {String(c.section_title ?? "")} · {(c.element_types as string[])?.join(", ")}
                </p>
                <p className="whitespace-pre-wrap">{String(c.text)}</p>
              </div>
            ))}
          </div>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function KbSettings({ kb, onSaved, canEdit }: { kb: KBOut; onSaved: () => void; canEdit: boolean }) {
  const orgId = useOrgId();
  const [name, setName] = React.useState(kb.name);
  const [desc, setDesc] = React.useState(kb.description ?? "");
  const cfg = kb.chunk_config as Record<string, unknown>;
  const [chunk, setChunk] = React.useState({
    chunk_size: Number(cfg.chunk_size ?? 1500),
    chunk_overlap: Number(cfg.chunk_overlap ?? 200),
    parsing_strategy: String(cfg.parsing_strategy ?? "auto"),
    contextual_headers: cfg.contextual_headers !== false,
  });
  const save = useMutation({
    mutationFn: () =>
      api(`/orgs/${orgId}/knowledge-bases/${kb.id}`, {
        method: "PATCH",
        body: { name, description: desc, chunk_config: chunk },
      }),
    onSuccess: () => {
      onSaved();
      toast.success("Saved");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reindex = useMutation({
    mutationFn: () => api(`/orgs/${orgId}/knowledge-bases/${kb.id}/reindex`, { method: "POST" }),
    onSuccess: () => toast.success("Re-index queued for all documents"),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>General</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} disabled={!canEdit} />
          </Field>
          <Field label="Description">
            <Textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={2} disabled={!canEdit} />
          </Field>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Advanced (re-index)</CardTitle>
          <CardDescription>
            ⚠️ Changing these reprocesses all documents. Defaults are tuned for most documents.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Chunk size (chars)" hint="300-4000">
            <Input
              type="number"
              min={300}
              max={4000}
              value={chunk.chunk_size}
              onChange={(e) => setChunk({ ...chunk, chunk_size: Number(e.target.value) })}
              disabled={!canEdit}
            />
          </Field>
          <Field label="Chunk overlap" hint="0-1000">
            <Input
              type="number"
              min={0}
              max={1000}
              value={chunk.chunk_overlap}
              onChange={(e) => setChunk({ ...chunk, chunk_overlap: Number(e.target.value) })}
              disabled={!canEdit}
            />
          </Field>
          <Field label="Parsing strategy">
            <Select
              value={chunk.parsing_strategy}
              onValueChange={(v) => setChunk({ ...chunk, parsing_strategy: v })}
              options={[
                { value: "auto", label: "auto" },
                { value: "hi_res", label: "hi_res (layout model, slower)" },
                { value: "ocr_only", label: "ocr_only (scans)" },
              ]}
              disabled={!canEdit}
            />
          </Field>
          <label className="flex items-center gap-2 pt-6 text-sm">
            <Switch
              checked={chunk.contextual_headers}
              onCheckedChange={(v) => setChunk({ ...chunk, contextual_headers: v })}
              disabled={!canEdit}
            />{" "}
            Contextual chunk headers
          </label>
        </CardContent>
      </Card>
      {canEdit ? (
        <div className="flex gap-2">
          <Button onClick={() => save.mutate()} loading={save.isPending}>
            Save
          </Button>
          <Button variant="outline" onClick={() => reindex.mutate()} loading={reindex.isPending}>
            <RefreshCw /> Re-index all documents
          </Button>
        </div>
      ) : null}
    </div>
  );
}
