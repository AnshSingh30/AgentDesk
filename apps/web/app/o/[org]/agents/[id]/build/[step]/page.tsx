"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronRight, MessageSquare, Rocket, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import {
  DatasetStep,
  GuardrailsStep,
  InstructionsStep,
  KnowledgeStep,
  ModelStep,
  ReviewStep,
  RoutingStep,
  TemplateStep,
  WebStep,
} from "@/components/builder/steps";
import { ChatPane, PlaygroundToolbar, usePlayground } from "@/components/playground/chat";
import { Button } from "@/components/ui/button";
import { Alert, Skeleton } from "@/components/ui/display";
import { Dialog, DialogClose, DialogContent, Input, Textarea } from "@/components/ui/primitives";
import { useAgentDraft } from "@/hooks/use-agent";
import { useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import type { AgentConfig, VersionOut } from "@/lib/types";
import { cn, timeAgo } from "@/lib/utils";

const STEPS = [
  { key: "template", label: "Template" },
  { key: "instructions", label: "Instructions" },
  { key: "knowledge", label: "Knowledge Base" },
  { key: "dataset", label: "Dataset" },
  { key: "web", label: "Web Search" },
  { key: "routing", label: "Routing" },
  { key: "model", label: "Model" },
  { key: "guardrails", label: "Guardrails & Memory" },
  { key: "review", label: "Review" },
];

function stepStatus(key: string, c: AgentConfig): "done" | "warn" | "todo" {
  switch (key) {
    case "instructions":
      return c.instructions.system_prompt.trim() ? "done" : "todo";
    case "knowledge":
      return !c.tools.rag.enabled ? "done" : c.tools.rag.kb_ids.length ? "done" : "warn";
    case "dataset":
      return !c.tools.sql.enabled ? "done" : c.tools.sql.dataset_id ? "done" : "warn";
    case "routing": {
      const n = [c.tools.rag.enabled, c.tools.sql.enabled, c.tools.web_search.enabled].filter(Boolean).length;
      if (n < 2) return "done";
      const missing =
        (c.tools.rag.enabled && !c.routing.rag_instructions.trim()) ||
        (c.tools.sql.enabled && !c.routing.sql_instructions.trim()) ||
        (c.tools.web_search.enabled && !c.routing.web_instructions.trim());
      return missing ? "warn" : "done";
    }
    case "model":
      return c.model.mode === "platform" || c.model.credential_id || c.model.api_base ? "done" : "warn";
    default:
      return "done";
  }
}

export default function BuilderPage() {
  const { id, step } = useParams<{ id: string; step: string }>();
  const orgId = useOrgId();
  const router = useRouter();
  const qc = useQueryClient();
  const draft = useAgentDraft(id);
  const [mini, setMini] = React.useState(false);
  const [publishOpen, setPublishOpen] = React.useState(false);
  const [changelog, setChangelog] = React.useState("");
  const base = `/o/${orgId}/agents/${id}`;
  const publish = useMutation({
    mutationFn: () =>
      api<VersionOut>(`/orgs/${orgId}/agents/${id}/publish`, {
        method: "POST",
        body: { changelog: changelog || null },
      }),
    onSuccess: (v) => {
      toast.success(`Published v${v.version_number}`);
      setPublishOpen(false);
      qc.invalidateQueries({ queryKey: ["agent", orgId, id] });
      qc.invalidateQueries({ queryKey: ["versions", orgId, id] });
      router.push(`${base}/deploy`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (draft.query.isPending || !draft.config || !draft.agent) return <Skeleton className="h-96" />;
  if (draft.query.isError) return <Alert variant="destructive">{errorMessage(draft.query.error)}</Alert>;
  const current = STEPS.find((s) => s.key === step) ?? STEPS[0];
  const idx = STEPS.indexOf(current);
  const props = { agent: draft.agent, config: draft.config, update: draft.update };
  const StepView = {
    template: TemplateStep,
    instructions: InstructionsStep,
    knowledge: KnowledgeStep,
    dataset: DatasetStep,
    web: WebStep,
    routing: RoutingStep,
    model: ModelStep,
    guardrails: GuardrailsStep,
    review: ReviewStep,
  }[current.key]!;

  return (
    <div className="-m-4 flex min-h-[calc(100vh-3.5rem)] lg:-m-8">
      <aside className="hidden w-60 shrink-0 border-r border-border bg-card p-4 lg:block">
        <p className="mb-3 truncate text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Build steps
        </p>
        <ol className="space-y-0.5">
          {STEPS.map((s, i) => {
            const st = stepStatus(s.key, draft.config!);
            return (
              <li key={s.key}>
                <Link
                  href={`${base}/build/${s.key}`}
                  className={cn(
                    "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted",
                    s.key === current.key && "bg-muted font-medium",
                  )}
                  aria-current={s.key === current.key ? "step" : undefined}
                >
                  <span
                    className={cn(
                      "flex h-5 w-5 items-center justify-center rounded-full border text-[10px]",
                      st === "done"
                        ? "border-success text-success"
                        : st === "warn"
                          ? "border-warning text-warning"
                          : "border-border text-muted-foreground",
                    )}
                  >
                    {st === "done" ? (
                      <Check className="h-3 w-3" />
                    ) : st === "warn" ? (
                      <AlertTriangle className="h-3 w-3" />
                    ) : (
                      i + 1
                    )}
                  </span>
                  {s.label}
                </Link>
              </li>
            );
          })}
        </ol>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-3 border-b border-border bg-card px-4 py-2">
          <Input
            value={draft.name}
            onChange={(e) => draft.update((c) => c, e.target.value)}
            className="h-8 w-56 font-medium"
            aria-label="Agent name"
          />
          <SaveIndicator state={draft.state} savedAt={draft.savedAt} />
          <div className="ml-auto flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setMini((m) => !m)} aria-pressed={mini}>
              <MessageSquare /> Test
            </Button>
            <Button variant="outline" size="sm" asChild>
              <Link href={`${base}/annotations`}>Annotations</Link>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <Link href={`${base}/playground`}>Open Playground</Link>
            </Button>
            <Button size="sm" onClick={() => setPublishOpen(true)}>
              <Rocket /> Publish
            </Button>
          </div>
        </div>
        {draft.state === "conflict" ? (
          <div className="px-4 pt-4">
            <Alert variant="warning" title="This draft was changed by someone else">
              <div className="mt-2 flex gap-2">
                <Button size="sm" variant="outline" onClick={draft.reload}>
                  Reload their changes
                </Button>
                <Button size="sm" variant="destructive" onClick={draft.overwrite}>
                  Overwrite with mine
                </Button>
              </div>
            </Alert>
          </div>
        ) : null}
        <div className="flex min-h-0 flex-1">
          <div className="min-w-0 flex-1 overflow-y-auto p-4 lg:p-6">
            <div className="mx-auto max-w-3xl">
              <div className="mb-4 flex items-center gap-2 text-sm text-muted-foreground lg:hidden">
                <select
                  className="rounded-md border border-border bg-card px-2 py-1"
                  value={current.key}
                  onChange={(e) => router.push(`${base}/build/${e.target.value}`)}
                  aria-label="Step"
                >
                  {STEPS.map((s) => (
                    <option key={s.key} value={s.key}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </div>
              <h2 className="mb-4 text-xl font-semibold">{current.label}</h2>
              <StepView {...props} />
              <div className="mt-8 flex justify-between">
                <Button variant="outline" disabled={idx === 0} asChild={idx > 0}>
                  {idx > 0 ? (
                    <Link href={`${base}/build/${STEPS[idx - 1].key}`}>Back</Link>
                  ) : (
                    <span>Back</span>
                  )}
                </Button>
                {idx < STEPS.length - 1 ? (
                  <Button asChild>
                    <Link href={`${base}/build/${STEPS[idx + 1].key}`}>
                      Next: {STEPS[idx + 1].label} <ChevronRight />
                    </Link>
                  </Button>
                ) : (
                  <Button onClick={() => setPublishOpen(true)}>
                    <Rocket /> Publish
                  </Button>
                )}
              </div>
            </div>
          </div>
          {mini ? (
            <aside className="flex w-[380px] shrink-0 flex-col border-l border-border bg-card">
              <div className="flex items-center justify-between border-b border-border px-3 py-2 text-sm font-medium">
                Test (draft)
                <button
                  onClick={() => setMini(false)}
                  aria-label="Close test panel"
                  className="rounded p-1 hover:bg-muted"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <MiniPlayground agentId={id} />
            </aside>
          ) : null}
        </div>
      </div>
      <Dialog open={publishOpen} onOpenChange={setPublishOpen}>
        {publishOpen ? (
          <DialogContent
            title="Publish a new version"
            description="Creates an immutable snapshot of the draft. Validation runs first."
          >
            <Textarea
              placeholder="Changelog (optional), e.g. Added leave policy and tightened no-answer message"
              value={changelog}
              onChange={(e) => setChangelog(e.target.value)}
              rows={3}
            />
            <div className="mt-4 flex justify-end gap-2">
              <DialogClose asChild>
                <Button variant="outline">Cancel</Button>
              </DialogClose>
              <Button onClick={() => publish.mutate()} loading={publish.isPending}>
                Publish
              </Button>
            </div>
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  );
}

function MiniPlayground({ agentId }: { agentId: string }) {
  const pg = usePlayground(agentId, null);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PlaygroundToolbar pg={pg} />
      <ChatPane pg={pg} compact />
    </div>
  );
}

function SaveIndicator({ state, savedAt }: { state: string; savedAt: Date | null }) {
  const text =
    state === "saving"
      ? "Saving…"
      : state === "dirty"
        ? "Unsaved changes"
        : state === "conflict"
          ? "Conflict"
          : state === "error"
            ? "Save failed"
            : savedAt
              ? `Saved · ${timeAgo(savedAt)}`
              : "All changes saved";
  return (
    <span
      className={cn(
        "text-xs",
        state === "error" || state === "conflict" ? "text-destructive" : "text-muted-foreground",
      )}
      aria-live="polite"
    >
      {text}
    </span>
  );
}
