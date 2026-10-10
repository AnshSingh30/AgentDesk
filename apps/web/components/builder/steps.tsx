"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ExternalLink, Info, Plus, XCircle } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { CodeBlock } from "@/components/shared";
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
  StatusBadge,
} from "@/components/ui/display";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
  Checkbox,
  Dialog,
  DialogContent,
  Field,
  Input,
  Select,
  Switch,
  Textarea,
} from "@/components/ui/primitives";
import { useDatasets, useKnowledgeBases, useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import type { AgentConfig, AgentOut, CredentialOut, KBOut, ModelCatalog, TemplateOut } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface StepProps {
  agent: AgentOut;
  config: AgentConfig;
  update: (fn: (c: AgentConfig) => AgentConfig, name?: string) => void;
}

const VARIABLES = ["{{org_name}}", "{{agent_name}}", "{{current_date}}", "{{channel}}"];

export function AdvancedSection({
  children,
  title = "Advanced",
}: {
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <Accordion type="single" collapsible className="mt-6">
      <AccordionItem value="advanced">
        <AccordionTrigger>{title}</AccordionTrigger>
        <AccordionContent>
          <Alert variant="warning" className="mb-4">
            ⚠️ Only change these if you know what you&apos;re doing. Defaults are tuned for most cases.
            Settings marked (re-index) reprocess all documents.
          </Alert>
          {children}
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}

/* ---------------------------------------------------------------- template */
export function TemplateStep({ agent, update }: StepProps) {
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<TemplateOut[]>("/templates") });
  const current = templates.data?.find((t) => t.key === (agent.template_key ?? "blank"));
  const [open, setOpen] = React.useState(false);
  const apply = (t: TemplateOut) => {
    update((c) => ({
      ...c,
      instructions: {
        ...c.instructions,
        system_prompt: t.system_prompt,
        starter_questions: t.starter_questions,
      },
      tools: {
        ...c.tools,
        rag: { ...c.tools.rag, enabled: !!t.enabled_tools.rag },
        sql: { ...c.tools.sql, enabled: !!t.enabled_tools.sql },
        web_search: { ...c.tools.web_search, enabled: !!t.enabled_tools.web_search },
      },
    }));
    setOpen(false);
    toast.success(`Applied template "${t.name}"`);
  };
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>{current?.name ?? "Blank agent"}</CardTitle>
          <CardDescription>
            {current?.description ?? "No template. Configure every step manually."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {current ? (
            <KeyValue
              items={[
                {
                  k: "Tools",
                  v:
                    Object.entries(current.enabled_tools)
                      .filter(([, v]) => v)
                      .map(([k]) => k)
                      .join(", ") || "none",
                },
                { k: "Recommended documents", v: current.recommended_kb_docs.join(", ") || "—" },
                { k: "Recommended dataset tables", v: current.recommended_dataset_schema.join(", ") || "—" },
              ]}
            />
          ) : null}
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => current && apply(current)} disabled={!current}>
              Reset prompt & tools to template
            </Button>
            <Button variant="ghost" onClick={() => setOpen(true)}>
              Switch template
            </Button>
          </div>
        </CardContent>
      </Card>
      <Dialog open={open} onOpenChange={setOpen}>
        {open ? (
          <DialogContent
            title="Switch template"
            description="Replaces the prompt, starter questions and tool toggles. Attached knowledge bases and datasets are kept."
            wide
          >
            <div className="grid gap-3 sm:grid-cols-2">
              {templates.data?.map((t) => (
                <button
                  key={t.key}
                  onClick={() => apply(t)}
                  className="rounded-lg border border-border p-4 text-left hover:border-primary"
                >
                  <p className="font-medium">{t.name}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{t.description}</p>
                </button>
              ))}
            </div>
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  );
}

/* ---------------------------------------------------------------- instructions */
export function InstructionsStep({ agent, config, update }: StepProps) {
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<TemplateOut[]>("/templates") });
  const preamble = useQuery({
    queryKey: ["preamble"],
    queryFn: () =>
      api<{ preamble: string; citation_rules: string; channel_rules: Record<string, string> }>(
        "/templates/platform-preamble",
      ),
  });
  const tpl = templates.data?.find((t) => t.key === (agent.template_key ?? "blank"));
  const ref = React.useRef<HTMLTextAreaElement>(null);
  const [showOriginal, setShowOriginal] = React.useState(false);
  const prompt = config.instructions.system_prompt;
  const lines = prompt.split("\n").length;
  const insertVar = (v: string) => {
    const el = ref.current;
    const start = el?.selectionStart ?? prompt.length;
    const end = el?.selectionEnd ?? prompt.length;
    const next = prompt.slice(0, start) + v + prompt.slice(end);
    update((c) => ({ ...c, instructions: { ...c.instructions, system_prompt: next } }));
    requestAnimationFrame(() => el?.setSelectionRange(start + v.length, start + v.length));
  };
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>Instructions (system prompt)</CardTitle>
            <CardDescription>
              Tell the agent who it is, what it may answer and how. Max 12,000 characters.
            </CardDescription>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="outline" size="sm" onClick={() => setShowOriginal(true)} disabled={!tpl}>
              View template original
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!tpl}
              onClick={() =>
                tpl &&
                update((c) => ({
                  ...c,
                  instructions: { ...c.instructions, system_prompt: tpl.system_prompt },
                }))
              }
            >
              Reset to template
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted-foreground">Insert variable:</span>
            {VARIABLES.map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => insertVar(v)}
                className="rounded-full border border-border bg-muted px-2 py-0.5 font-mono hover:border-primary"
              >
                {v}
              </button>
            ))}
          </div>
          <div className="flex overflow-hidden rounded-md border border-input bg-card focus-within:ring-2 focus-within:ring-ring">
            <pre
              aria-hidden
              className="select-none border-r border-border bg-muted px-2 py-2 text-right font-mono text-xs leading-5 text-muted-foreground"
            >
              {Array.from({ length: lines }, (_, i) => i + 1).join("\n")}
            </pre>
            <textarea
              ref={ref}
              value={prompt}
              maxLength={12000}
              onChange={(e) =>
                update((c) => ({ ...c, instructions: { ...c.instructions, system_prompt: e.target.value } }))
              }
              className="min-h-[360px] flex-1 resize-y bg-transparent px-3 py-2 font-mono text-xs leading-5 outline-none"
              spellCheck={false}
              aria-label="System prompt"
            />
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            {prompt.length.toLocaleString()} / 12,000 characters · ~
            {Math.ceil(prompt.length / 4).toLocaleString()} tokens
          </p>
        </CardContent>
      </Card>
      <Accordion type="single" collapsible>
        <AccordionItem value="platform">
          <AccordionTrigger>Platform instructions (read-only)</AccordionTrigger>
          <AccordionContent>
            <p className="mb-2 text-xs text-muted-foreground">
              Prepended to every prompt: tool-usage rules, citation format, safety and channel formatting.
            </p>
            <CodeBlock>
              {[
                preamble.data?.preamble,
                preamble.data?.citation_rules,
                preamble.data?.channel_rules?.whatsapp,
              ]
                .filter(Boolean)
                .join("\n\n")}
            </CodeBlock>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
      <Card>
        <CardHeader>
          <CardTitle>Starter questions</CardTitle>
          <CardDescription>Shown as suggestions in the Playground (up to 6).</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {config.instructions.starter_questions.map((q, i) => (
            <div key={i} className="flex gap-2">
              <Input
                value={q}
                onChange={(e) =>
                  update((c) => ({
                    ...c,
                    instructions: {
                      ...c.instructions,
                      starter_questions: c.instructions.starter_questions.map((x, j) =>
                        j === i ? e.target.value : x,
                      ),
                    },
                  }))
                }
                aria-label={`Starter question ${i + 1}`}
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label="Remove"
                onClick={() =>
                  update((c) => ({
                    ...c,
                    instructions: {
                      ...c.instructions,
                      starter_questions: c.instructions.starter_questions.filter((_, j) => j !== i),
                    },
                  }))
                }
              >
                <XCircle />
              </Button>
            </div>
          ))}
          {config.instructions.starter_questions.length < 6 ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                update((c) => ({
                  ...c,
                  instructions: {
                    ...c.instructions,
                    starter_questions: [...c.instructions.starter_questions, ""],
                  },
                }))
              }
            >
              <Plus /> Add question
            </Button>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>WhatsApp greeting</CardTitle>
          <CardDescription>
            Optional welcome message sent on first contact; the AI disclosure is recommended by WhatsApp
            policy.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            value={config.instructions.welcome_message ?? ""}
            onChange={(e) =>
              update((c) => ({
                ...c,
                instructions: { ...c.instructions, welcome_message: e.target.value || null },
              }))
            }
            placeholder={`Hi! I'm ${agent.name}. Ask me anything about...`}
            rows={2}
          />
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={config.instructions.ai_disclosure}
              onCheckedChange={(v) =>
                update((c) => ({ ...c, instructions: { ...c.instructions, ai_disclosure: v } }))
              }
            />
            Add &quot;You&apos;re chatting with an AI assistant&quot; to the greeting
          </label>
        </CardContent>
      </Card>
      <Dialog open={showOriginal} onOpenChange={setShowOriginal}>
        {showOriginal && tpl ? (
          <DialogContent title={`Template: ${tpl.name}`} wide>
            <CodeBlock className="max-h-[60vh]">{tpl.system_prompt}</CodeBlock>
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  );
}

/* ---------------------------------------------------------------- knowledge */
export function KnowledgeStep({ config, update }: StepProps) {
  const orgId = useOrgId();
  const kbs = useKnowledgeBases();
  const qc = useQueryClient();
  const [newName, setNewName] = React.useState("");
  const rag = config.tools.rag;
  const create = useMutation({
    mutationFn: (name: string) =>
      api<KBOut>(`/orgs/${orgId}/knowledge-bases`, { method: "POST", body: { name } }),
    onSuccess: (kb) => {
      qc.invalidateQueries({ queryKey: ["kbs", orgId] });
      update((c) => ({
        ...c,
        tools: { ...c.tools, rag: { ...c.tools.rag, enabled: true, kb_ids: [...c.tools.rag.kb_ids, kb.id] } },
      }));
      setNewName("");
      toast.success("Knowledge base created");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const toggle = (id: string, on: boolean) =>
    update((c) => ({
      ...c,
      tools: {
        ...c.tools,
        rag: {
          ...c.tools.rag,
          kb_ids: on ? [...new Set([...c.tools.rag.kb_ids, id])] : c.tools.rag.kb_ids.filter((x) => x !== id),
        },
      },
    }));
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle>Knowledge Base (RAG)</CardTitle>
            <CardDescription>Answer from uploaded documents with citations.</CardDescription>
          </div>
          <Switch
            checked={rag.enabled}
            onCheckedChange={(v) =>
              update((c) => ({ ...c, tools: { ...c.tools, rag: { ...c.tools.rag, enabled: v } } }))
            }
            aria-label="Enable Knowledge Base"
          />
        </CardHeader>
        {rag.enabled ? (
          <CardContent className="space-y-4">
            <div className="space-y-2">
              {(kbs.data ?? []).map((kb) => (
                <label
                  key={kb.id}
                  className="flex items-center gap-3 rounded-lg border border-border p-3 text-sm"
                >
                  <Checkbox
                    checked={rag.kb_ids.includes(kb.id)}
                    onCheckedChange={(v) => toggle(kb.id, v === true)}
                  />
                  <span className="flex-1">
                    <span className="font-medium">{kb.name}</span>
                    <span className="ml-2 text-xs text-muted-foreground">
                      {kb.doc_count} documents · {kb.chunk_count} chunks
                    </span>
                  </span>
                  <Link
                    href={`/o/${orgId}/knowledge/${kb.id}`}
                    className="text-xs text-primary hover:underline"
                  >
                    Manage documents <ExternalLink className="inline h-3 w-3" />
                  </Link>
                </label>
              ))}
              {kbs.data && kbs.data.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No knowledge bases yet. Create one below and upload documents.
                </p>
              ) : null}
            </div>
            <div className="flex gap-2">
              <Input
                placeholder="New knowledge base name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
              <Button
                variant="outline"
                onClick={() => create.mutate(newName.trim())}
                disabled={!newName.trim()}
                loading={create.isPending}
              >
                <Plus /> Create & attach
              </Button>
            </div>
            <AdvancedSection title="Advanced RAG settings">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Top K" hint="Chunks passed to the model (1-20)">
                  <Input
                    type="number"
                    min={1}
                    max={20}
                    value={rag.top_k}
                    onChange={(e) =>
                      update((c) => ({
                        ...c,
                        tools: { ...c.tools, rag: { ...c.tools.rag, top_k: Number(e.target.value) } },
                      }))
                    }
                  />
                </Field>
                <Field
                  label="Relevance threshold"
                  hint="Cosine similarity below which chunks are ignored (0-1)"
                >
                  <Input
                    type="number"
                    step={0.05}
                    min={0}
                    max={1}
                    value={rag.threshold}
                    onChange={(e) =>
                      update((c) => ({
                        ...c,
                        tools: { ...c.tools, rag: { ...c.tools.rag, threshold: Number(e.target.value) } },
                      }))
                    }
                  />
                </Field>
                <Field label="Search mode">
                  <Select
                    value={rag.search_mode}
                    onValueChange={(v) =>
                      update((c) => ({
                        ...c,
                        tools: {
                          ...c.tools,
                          rag: { ...c.tools.rag, search_mode: v as "hybrid" | "dense" | "sparse" },
                        },
                      }))
                    }
                    options={[
                      { value: "hybrid", label: "Hybrid (dense + BM25)" },
                      { value: "dense", label: "Dense only" },
                      { value: "sparse", label: "Sparse (BM25) only" },
                    ]}
                  />
                </Field>
                <Field label="Reranker">
                  <Select
                    value={rag.rerank.enabled ? (rag.rerank.provider ?? "local") : "off"}
                    onValueChange={(v) =>
                      update((c) => ({
                        ...c,
                        tools: {
                          ...c.tools,
                          rag: {
                            ...c.tools.rag,
                            rerank: {
                              ...c.tools.rag.rerank,
                              enabled: v !== "off",
                              provider: v === "off" ? null : (v as "cohere" | "local"),
                            },
                          },
                        },
                      }))
                    }
                    options={[
                      { value: "off", label: "Off" },
                      { value: "local", label: "Local cross-encoder" },
                      { value: "cohere", label: "Cohere Rerank (BYO key)" },
                    ]}
                  />
                </Field>
                <Field label="Rerank candidates" hint="10-50">
                  <Input
                    type="number"
                    min={10}
                    max={50}
                    value={rag.rerank.candidates}
                    disabled={!rag.rerank.enabled}
                    onChange={(e) =>
                      update((c) => ({
                        ...c,
                        tools: {
                          ...c.tools,
                          rag: {
                            ...c.tools.rag,
                            rerank: { ...c.tools.rag.rerank, candidates: Number(e.target.value) },
                          },
                        },
                      }))
                    }
                  />
                </Field>
                <div className="space-y-2 pt-1">
                  <label className="flex items-center gap-2 text-sm">
                    <Switch
                      checked={rag.query_rewrite}
                      onCheckedChange={(v) =>
                        update((c) => ({
                          ...c,
                          tools: { ...c.tools, rag: { ...c.tools.rag, query_rewrite: v } },
                        }))
                      }
                    />{" "}
                    Query rewriting (follow-ups)
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <Switch
                      checked={rag.citations}
                      onCheckedChange={(v) =>
                        update((c) => ({
                          ...c,
                          tools: { ...c.tools, rag: { ...c.tools.rag, citations: v } },
                        }))
                      }
                    />{" "}
                    Show citations
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <Switch
                      checked={rag.mmr}
                      onCheckedChange={(v) =>
                        update((c) => ({ ...c, tools: { ...c.tools, rag: { ...c.tools.rag, mmr: v } } }))
                      }
                    />{" "}
                    MMR diversification
                  </label>
                </div>
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                Chunk size, overlap, parsing strategy and contextual headers are per knowledge base (re-index)
                - edit them on the knowledge base page.
              </p>
            </AdvancedSection>
          </CardContent>
        ) : null}
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- dataset */
export function DatasetStep({ config, update }: StepProps) {
  const orgId = useOrgId();
  const datasets = useDatasets();
  const sql = config.tools.sql;
  const chosen = datasets.data?.find((d) => d.id === sql.dataset_id);
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <div>
          <CardTitle>Dataset (text-to-SQL)</CardTitle>
          <CardDescription>
            Answer numbers, lists and trends from a read-only PostgreSQL database or spreadsheet.
          </CardDescription>
        </div>
        <Switch
          checked={sql.enabled}
          onCheckedChange={(v) =>
            update((c) => ({ ...c, tools: { ...c.tools, sql: { ...c.tools.sql, enabled: v } } }))
          }
          aria-label="Enable Dataset"
        />
      </CardHeader>
      {sql.enabled ? (
        <CardContent className="space-y-4">
          <Field label="Connected dataset">
            <Select
              value={sql.dataset_id ?? undefined}
              onValueChange={(v) =>
                update((c) => ({ ...c, tools: { ...c.tools, sql: { ...c.tools.sql, dataset_id: v } } }))
              }
              options={(datasets.data ?? []).map((d) => ({
                value: d.id,
                label: `${d.name} (${d.type})`,
                hint: d.status,
              }))}
              placeholder={datasets.data?.length ? "Select a dataset" : "No datasets yet"}
            />
          </Field>
          {chosen ? (
            <div className="rounded-lg border border-border p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-medium">{chosen.name}</span>
                <StatusBadge status={chosen.status} />
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {chosen.table_stats.exposed ?? 0} of {chosen.table_stats.tables ?? 0} tables exposed ·{" "}
                {chosen.table_stats.reviewed ?? 0} reviewed
              </p>
              {(chosen.table_stats.reviewed ?? 0) < (chosen.table_stats.exposed ?? 0) ? (
                <p className="mt-2 flex items-center gap-1 text-xs text-warning">
                  <AlertTriangle className="h-3.5 w-3.5" /> Some tables have no reviewed descriptions -
                  accuracy improves after schema review.
                </p>
              ) : null}
              <Link
                href={`/o/${orgId}/datasets/${chosen.id}`}
                className="mt-2 inline-block text-xs text-primary hover:underline"
              >
                Open schema review <ExternalLink className="inline h-3 w-3" />
              </Link>
            </div>
          ) : null}
          <Button variant="outline" asChild>
            <Link href={`/o/${orgId}/datasets?new=1`}>
              <Plus /> Connect a new dataset
            </Link>
          </Button>
          <AdvancedSection title="Advanced SQL settings">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Max rows returned to the model" hint="1-5000 (default 200)">
                <Input
                  type="number"
                  min={1}
                  max={5000}
                  value={sql.max_rows}
                  onChange={(e) =>
                    update((c) => ({
                      ...c,
                      tools: { ...c.tools, sql: { ...c.tools.sql, max_rows: Number(e.target.value) } },
                    }))
                  }
                />
              </Field>
              <Field label="Max attempts" hint="Retries after validator or DB errors (1-5)">
                <Input
                  type="number"
                  min={1}
                  max={5}
                  value={sql.max_attempts}
                  onChange={(e) =>
                    update((c) => ({
                      ...c,
                      tools: { ...c.tools, sql: { ...c.tools.sql, max_attempts: Number(e.target.value) } },
                    }))
                  }
                />
              </Field>
            </div>
            <label className="mt-3 flex items-center gap-2 text-sm">
              <Switch
                checked={sql.confirm_in_playground}
                onCheckedChange={(v) =>
                  update((c) => ({
                    ...c,
                    tools: { ...c.tools, sql: { ...c.tools.sql, confirm_in_playground: v } },
                  }))
                }
              />{" "}
              Default &quot;Confirm SQL before running&quot; on in the Playground
            </label>
          </AdvancedSection>
        </CardContent>
      ) : null}
    </Card>
  );
}

/* ---------------------------------------------------------------- web */
export function WebStep({ config, update }: StepProps) {
  const w = config.tools.web_search;
  const list = (s: string) =>
    s
      .split(/[\n,]/)
      .map((x) => x.trim())
      .filter(Boolean);
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <div>
          <CardTitle>Web search</CardTitle>
          <CardDescription>Powered by Tavily. Answers cite source URLs.</CardDescription>
        </div>
        <Switch
          checked={w.enabled}
          onCheckedChange={(v) =>
            update((c) => ({
              ...c,
              tools: { ...c.tools, web_search: { ...c.tools.web_search, enabled: v } },
            }))
          }
          aria-label="Enable web search"
        />
      </CardHeader>
      {w.enabled ? (
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Max results" hint="1-10">
            <Input
              type="number"
              min={1}
              max={10}
              value={w.max_results}
              onChange={(e) =>
                update((c) => ({
                  ...c,
                  tools: {
                    ...c.tools,
                    web_search: { ...c.tools.web_search, max_results: Number(e.target.value) },
                  },
                }))
              }
            />
          </Field>
          <Field label="Search depth">
            <Select
              value={w.depth}
              onValueChange={(v) =>
                update((c) => ({
                  ...c,
                  tools: {
                    ...c.tools,
                    web_search: { ...c.tools.web_search, depth: v as "basic" | "advanced" },
                  },
                }))
              }
              options={[
                { value: "basic", label: "Basic (fast)" },
                { value: "advanced", label: "Advanced (deeper)" },
              ]}
            />
          </Field>
          <Field label="Domain allowlist" hint="One per line; leave empty for the whole web">
            <Textarea
              rows={3}
              defaultValue={w.include_domains.join("\n")}
              onBlur={(e) =>
                update((c) => ({
                  ...c,
                  tools: {
                    ...c.tools,
                    web_search: { ...c.tools.web_search, include_domains: list(e.target.value) },
                  },
                }))
              }
            />
          </Field>
          <Field label="Domain blocklist" hint="One per line">
            <Textarea
              rows={3}
              defaultValue={w.exclude_domains.join("\n")}
              onBlur={(e) =>
                update((c) => ({
                  ...c,
                  tools: {
                    ...c.tools,
                    web_search: { ...c.tools.web_search, exclude_domains: list(e.target.value) },
                  },
                }))
              }
            />
          </Field>
        </CardContent>
      ) : null}
    </Card>
  );
}

/* ---------------------------------------------------------------- routing */
export function RoutingStep({ config, update }: StepProps) {
  const tools = (["rag", "sql", "web_search"] as const).filter((t) => config.tools[t].enabled);
  if (tools.length < 2)
    return (
      <Alert variant="info" title="Routing applies when two or more tools are enabled">
        With {tools.length === 1 ? "one tool" : "no tools"} the agent uses a fixed flow; enable another tool
        to edit routing instructions.
      </Alert>
    );
  const fields = [
    {
      key: "rag_instructions" as const,
      label: "Use the Knowledge Base when…",
      show: config.tools.rag.enabled,
    },
    { key: "sql_instructions" as const, label: "Use the Dataset when…", show: config.tools.sql.enabled },
    {
      key: "web_instructions" as const,
      label: "Use Web search when…",
      show: config.tools.web_search.enabled,
    },
  ].filter((f) => f.show);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Routing</CardTitle>
        <CardDescription>
          The router reads these instructions to pick one, several (in parallel) or none of the tools for each
          message.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {fields.map((f) => (
          <Field key={f.key} label={f.label}>
            <Textarea
              rows={3}
              value={config.routing[f.key]}
              onChange={(e) => update((c) => ({ ...c, routing: { ...c.routing, [f.key]: e.target.value } }))}
            />
          </Field>
        ))}
        <label className="flex items-center gap-2 text-sm">
          <Switch
            checked={config.guardrails.web_fallback}
            disabled={!config.tools.web_search.enabled}
            onCheckedChange={(v) =>
              update((c) => ({ ...c, guardrails: { ...c.guardrails, web_fallback: v } }))
            }
          />
          Fall back to web search when the Knowledge Base has no relevant chunks
        </label>
      </CardContent>
    </Card>
  );
}

/* ---------------------------------------------------------------- model */
export function ModelStep({ config, update }: StepProps) {
  const orgId = useOrgId();
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: () => api<ModelCatalog>("/models/catalog") });
  const creds = useQuery({
    queryKey: ["credentials", orgId],
    queryFn: () => api<CredentialOut[]>(`/orgs/${orgId}/provider-credentials`),
  });
  const [result, setResult] = React.useState<Record<string, unknown> | null>(null);
  const test = useMutation({
    mutationFn: () =>
      api<Record<string, unknown>>(`/orgs/${orgId}/models/test`, { method: "POST", body: config.model }),
    onSuccess: setResult,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const m = config.model;
  const set = (patch: Partial<AgentConfig["model"]>) =>
    update((c) => ({ ...c, model: { ...c.model, ...patch } }));
  const cred = creds.data?.find((c) => c.id === m.credential_id);
  const providerModels =
    catalog.data?.providers.find((p) => p.key === (m.provider ?? cred?.provider))?.models ?? [];
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Model</CardTitle>
          <CardDescription>
            Platform default (metered), your own API key, or a custom OpenAI-compatible endpoint.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2 sm:grid-cols-3">
            {(["platform", "byo", "custom"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() =>
                  set({
                    mode,
                    credential_id: mode === "platform" ? null : m.credential_id,
                    provider: mode === "byo" ? m.provider : null,
                  })
                }
                className={cn(
                  "rounded-lg border p-3 text-left text-sm",
                  m.mode === mode ? "border-primary bg-primary-soft" : "border-border",
                )}
                aria-pressed={m.mode === mode}
              >
                <p className="font-medium">
                  {
                    { platform: "Platform default", byo: "Bring your own key", custom: "Custom endpoint" }[
                      mode
                    ]
                  }
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {
                    {
                      platform: "Uses our key, usage is metered.",
                      byo: "OpenAI, Anthropic, Gemini, Groq, OpenRouter.",
                      custom: "vLLM, Ollama, LM Studio, Azure OpenAI.",
                    }[mode]
                  }
                </p>
              </button>
            ))}
          </div>
          {m.mode !== "platform" ? (
            <Field
              label="API key"
              hint="Keys are stored encrypted at organisation level and referenced here."
            >
              <div className="flex gap-2">
                <Select
                  value={m.credential_id ?? undefined}
                  onValueChange={(v) => {
                    const c = creds.data?.find((x) => x.id === v);
                    set({
                      credential_id: v,
                      provider: m.mode === "byo" ? (c?.provider ?? null) : null,
                      api_base: m.mode === "custom" ? (c?.api_base ?? m.api_base) : null,
                    });
                  }}
                  options={(creds.data ?? [])
                    .filter((c) =>
                      m.mode === "byo" ? !["custom", "tavily", "cohere"].includes(c.provider) : true,
                    )
                    .map((c) => ({ value: c.id, label: `${c.label} (${c.provider} ····${c.last4})` }))}
                  placeholder={m.mode === "custom" ? "Optional for local servers" : "Select a key"}
                />
                <Button variant="outline" asChild>
                  <Link href={`/o/${orgId}/settings/api-keys`}>Manage keys</Link>
                </Button>
              </div>
            </Field>
          ) : null}
          {m.mode === "byo" ? (
            <Field label="Model">
              <Select
                value={m.model ?? undefined}
                onValueChange={(v) => set({ model: v })}
                options={providerModels.map((x) => ({
                  value: x.id,
                  label: x.display,
                  hint: x.supports_tools ? "" : "no tool calling",
                }))}
                placeholder={cred ? "Select a model" : "Select a key first"}
              />
            </Field>
          ) : null}
          {m.mode === "custom" ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Base URL" hint="OpenAI-compatible, e.g. https://llm.example.com/v1">
                <Input
                  value={m.api_base ?? ""}
                  onChange={(e) => set({ api_base: e.target.value })}
                  placeholder="https://…/v1"
                />
              </Field>
              <Field label="Model name">
                <Input
                  value={m.model ?? ""}
                  onChange={(e) => set({ model: e.target.value })}
                  placeholder="e.g. llama-3.3-70b"
                />
              </Field>
              <Field label="Context window (tokens)">
                <Input
                  type="number"
                  value={m.context_window ?? 32000}
                  onChange={(e) => set({ context_window: Number(e.target.value) })}
                />
              </Field>
            </div>
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={`Temperature: ${m.temperature}`} hint="Lower is more deterministic (default 0.2)">
              <input
                type="range"
                min={0}
                max={2}
                step={0.1}
                value={m.temperature}
                onChange={(e) => set({ temperature: Number(e.target.value) })}
                className="w-full"
                aria-label="Temperature"
              />
            </Field>
            <Field label="Max output tokens" hint="64-16000 (default 1024)">
              <Input
                type="number"
                min={64}
                max={16000}
                value={m.max_output_tokens}
                onChange={(e) => set({ max_output_tokens: Number(e.target.value) })}
              />
            </Field>
          </div>
          <div className="flex items-center gap-3">
            <Button variant="outline" onClick={() => test.mutate()} loading={test.isPending}>
              Test connection
            </Button>
            {result ? (
              result.ok ? (
                <span className="flex items-center gap-1 text-sm text-success">
                  <CheckCircle2 className="h-4 w-4" /> {String(result.model)} responded
                  {(result.warnings as string[])?.length
                    ? ` · ${(result.warnings as string[]).join(" ")}`
                    : ""}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-sm text-destructive">
                  <XCircle className="h-4 w-4" /> {String(result.code)}: {String(result.message)}
                </span>
              )
            ) : null}
          </div>
        </CardContent>
      </Card>
      <AdvancedSection title="Advanced model settings">
        <Field
          label="Utility model (optional)"
          hint="Cheaper model for routing, query rewriting and schema descriptions, e.g. openai/gpt-5-mini"
        >
          <Input
            value={m.utility_model ?? ""}
            onChange={(e) => set({ utility_model: e.target.value || null })}
            placeholder="provider/model"
          />
        </Field>
      </AdvancedSection>
    </div>
  );
}

/* ---------------------------------------------------------------- guardrails & memory */
export function GuardrailsStep({ config, update }: StepProps) {
  const g = config.guardrails;
  const mem = config.memory;
  const setG = (patch: Partial<AgentConfig["guardrails"]>) =>
    update((c) => ({ ...c, guardrails: { ...c.guardrails, ...patch } }));
  const setA = (patch: Partial<AgentConfig["guardrails"]["advanced"]>) =>
    update((c) => ({
      ...c,
      guardrails: { ...c.guardrails, advanced: { ...c.guardrails.advanced, ...patch } },
    }));
  const setM = (patch: Partial<AgentConfig["memory"]>) =>
    update((c) => ({ ...c, memory: { ...c.memory, ...patch } }));
  const ann = config.annotations ?? { enabled: true, threshold: 0.9 };
  const setAnn = (patch: Partial<NonNullable<AgentConfig["annotations"]>>) =>
    update((c) => ({ ...c, annotations: { ...ann, ...c.annotations, ...patch } }));
  const list = (s: string) =>
    s
      .split(/[\n,]/)
      .map((x) => x.trim())
      .filter(Boolean);
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Guardrails</CardTitle>
          <CardDescription>
            Defaults on: prompt-injection defence, soft topic restriction, no-answer policy, input length
            limit, WhatsApp formatting.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="No-answer message" className="sm:col-span-2">
            <Textarea
              rows={2}
              value={g.no_answer_message}
              onChange={(e) => setG({ no_answer_message: e.target.value })}
            />
          </Field>
          <Field
            label="Allowed topics"
            hint="Plain-language description; anything else gets a polite redirect"
            className="sm:col-span-2"
          >
            <Textarea
              rows={2}
              value={g.allowed_topics}
              onChange={(e) => setG({ allowed_topics: e.target.value })}
            />
          </Field>
          <Field label="Blocked topics" hint="One per line">
            <Textarea
              rows={3}
              defaultValue={g.blocked_topics.join("\n")}
              onBlur={(e) => setG({ blocked_topics: list(e.target.value) })}
            />
          </Field>
          <Field label="Blocked keywords" hint="Exact words/phrases that trigger a refusal">
            <Textarea
              rows={3}
              defaultValue={g.blocked_keywords.join("\n")}
              onBlur={(e) => setG({ blocked_keywords: list(e.target.value) })}
            />
          </Field>
          <Field label="Response language">
            <Select
              value={g.language}
              onValueChange={(v) => setG({ language: v })}
              options={["auto", "English", "Hindi", "Hinglish", "Spanish", "French", "German", "Arabic"].map(
                (l) => ({ value: l, label: l === "auto" ? "Auto (match the user)" : l }),
              )}
            />
          </Field>
          <Field label="Tone">
            <Select
              value={g.tone}
              onValueChange={(v) => setG({ tone: v as "friendly" | "professional" | "concise" })}
              options={[
                { value: "friendly", label: "Friendly" },
                { value: "professional", label: "Professional" },
                { value: "concise", label: "Concise" },
              ]}
            />
          </Field>
          <Field label="Max input length (chars)" hint="50-10000">
            <Input
              type="number"
              min={50}
              max={10000}
              value={g.max_input_chars}
              onChange={(e) => setG({ max_input_chars: Number(e.target.value) })}
            />
          </Field>
          <div className="space-y-2 pt-6">
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={g.citations} onCheckedChange={(v) => setG({ citations: v })} /> Citations in
              answers
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Switch
                checked={g.web_fallback}
                disabled={!config.tools.web_search.enabled}
                onCheckedChange={(v) => setG({ web_fallback: v })}
              />{" "}
              Web-search fallback
            </label>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Memory</CardTitle>
          <CardDescription>
            Conversation history kept per contact (WhatsApp) or per session (Playground). Users can type
            /reset on WhatsApp.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <Field label="Turns remembered" hint="0-50 (default 10)">
            <Input
              type="number"
              min={0}
              max={50}
              value={mem.max_turns}
              onChange={(e) => setM({ max_turns: Number(e.target.value) })}
            />
          </Field>
          <Field label="History token budget" hint="1,000-32,000 (default 8,000)">
            <Input
              type="number"
              min={1000}
              max={32000}
              step={500}
              value={mem.history_token_budget}
              onChange={(e) => setM({ history_token_budget: Number(e.target.value) })}
            />
          </Field>
          <Field label="Session timeout (minutes)" hint="Idle time before a new session (default 24h)">
            <Input
              type="number"
              min={5}
              max={43200}
              value={mem.session_timeout_minutes}
              onChange={(e) => setM({ session_timeout_minutes: Number(e.target.value) })}
            />
          </Field>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Annotation replies</CardTitle>
          <CardDescription>
            Questions close to a curated annotation get its answer verbatim, without a model call. Manage them
            on the agent&apos;s Annotations page or save one from a Playground reply.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={ann.enabled} onCheckedChange={(v) => setAnn({ enabled: v })} /> Use annotation
            replies
          </label>
          <Field label="Similarity threshold" hint="0.50-1.00 (default 0.90); higher = stricter match">
            <Input
              type="number"
              min={0.5}
              max={1}
              step={0.01}
              value={ann.threshold}
              disabled={!ann.enabled}
              onChange={(e) => setAnn({ threshold: Number(e.target.value) })}
            />
          </Field>
        </CardContent>
      </Card>
      <AdvancedSection title="Advanced guardrails">
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={g.advanced.input_classifier}
              onCheckedChange={(v) => setA({ input_classifier: v })}
            />{" "}
            LLM input classifier (injection / off-topic / abusive)
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={g.advanced.grounding_check}
              onCheckedChange={(v) => setA({ grounding_check: v })}
            />{" "}
            Grounding check (verify claims against context; regenerates once)
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={g.advanced.log_pii_redaction}
              onCheckedChange={(v) => setA({ log_pii_redaction: v })}
            />{" "}
            Redact PII in logs and traces
          </label>
          <Field label="Injection sensitivity">
            <Select
              value={g.advanced.injection_sensitivity}
              onValueChange={(v) => setA({ injection_sensitivity: v as "low" | "medium" | "high" })}
              options={[
                { value: "low", label: "Low - classifier only" },
                { value: "medium", label: "Medium - flag and defend (default)" },
                { value: "high", label: "High - block on heuristics" },
              ]}
            />
          </Field>
          <Field label="Custom guardrail instructions" hint="Appended to the prompt">
            <Textarea
              rows={3}
              value={g.advanced.custom_instructions}
              onChange={(e) => setA({ custom_instructions: e.target.value })}
            />
          </Field>
        </div>
      </AdvancedSection>
    </div>
  );
}

/* ---------------------------------------------------------------- review */
export function ReviewStep({ agent, config }: StepProps) {
  const orgId = useOrgId();
  const validation = useQuery({
    queryKey: ["validate", orgId, agent.id, agent.draft_updated_at],
    queryFn: () =>
      api<{
        ok: boolean;
        errors: { field: string; message: string }[];
        warnings: { field: string; message: string }[];
      }>(`/orgs/${orgId}/agents/${agent.id}/validate`, { method: "POST" }),
  });
  const tools = (["rag", "sql", "web_search"] as const).filter((t) => config.tools[t].enabled);
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Checklist</CardTitle>
          <CardDescription>
            Blocking errors must be fixed before publishing; warnings are advisory.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {validation.isPending ? <p className="text-muted-foreground">Checking…</p> : null}
          {validation.data?.errors.map((e) => (
            <p key={e.field + e.message} className="flex items-start gap-2 text-destructive">
              <XCircle className="mt-0.5 h-4 w-4 shrink-0" /> {e.message}
            </p>
          ))}
          {validation.data?.warnings.map((w) => (
            <p key={w.field + w.message} className="flex items-start gap-2 text-warning">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {w.message}
            </p>
          ))}
          {validation.data && validation.data.ok && validation.data.warnings.length === 0 ? (
            <p className="flex items-center gap-2 text-success">
              <CheckCircle2 className="h-4 w-4" /> Everything looks good.
            </p>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Configuration summary</CardTitle>
        </CardHeader>
        <CardContent>
          <KeyValue
            items={[
              { k: "Name", v: agent.name },
              { k: "Template", v: agent.template_key ?? "blank" },
              {
                k: "Tools",
                v: tools.length
                  ? tools.map((t) => (
                      <Badge key={t} className="mr-1">
                        {t}
                      </Badge>
                    ))
                  : "none",
              },
              {
                k: "Knowledge bases",
                v: config.tools.rag.enabled
                  ? `${config.tools.rag.kb_ids.length} attached · top ${config.tools.rag.top_k}, threshold ${config.tools.rag.threshold}`
                  : "off",
              },
              {
                k: "Dataset",
                v: config.tools.sql.enabled ? (config.tools.sql.dataset_id ?? "not connected") : "off",
              },
              {
                k: "Model",
                v: `${config.model.mode}${config.model.model ? ` · ${config.model.model}` : ""} · temp ${config.model.temperature}`,
              },
              {
                k: "Memory",
                v: `${config.memory.max_turns} turns · ${config.memory.history_token_budget} tokens · ${config.memory.session_timeout_minutes} min session`,
              },
              {
                k: "Guardrails",
                v: `${config.guardrails.tone} · ${config.guardrails.language} · ${config.guardrails.blocked_keywords.length} blocked keywords`,
              },
              {
                k: "Last published",
                v: agent.latest_version_number ? `v${agent.latest_version_number}` : "never",
              },
            ]}
          />
        </CardContent>
      </Card>
      <Alert variant="info" className="flex gap-2">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          Publishing creates an immutable version. Deployments keep running the version they are pinned to
          until you update them.
        </span>
      </Alert>
    </div>
  );
}
