"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2 } from "lucide-react";
import { useParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog, PageHeader } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/display";
import { Textarea } from "@/components/ui/primitives";
import { useAgent } from "@/hooks/use-agent";
import { useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import { useRole } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";

interface AnnotationOut {
  id: string;
  question: string;
  answer: string;
  hit_count: number;
  last_hit_at: string | null;
}

export default function AnnotationsPage() {
  const { id } = useParams<{ id: string }>();
  const orgId = useOrgId();
  const agent = useAgent(id);
  const { can } = useRole(orgId);
  const qc = useQueryClient();
  const path = `/orgs/${orgId}/agents/${id}/annotations`;
  const list = useQuery({
    queryKey: ["annotations", orgId, id],
    queryFn: () => api<{ items: AnnotationOut[] }>(path),
  });
  const [form, setForm] = React.useState({ question: "", answer: "" });
  const [editing, setEditing] = React.useState<string | null>(null);
  const [toDelete, setToDelete] = React.useState<AnnotationOut | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["annotations", orgId, id] });
  const save = useMutation({
    mutationFn: () =>
      editing
        ? api(`${path}/${editing}`, { method: "PATCH", body: form })
        : api(path, { method: "POST", body: form }),
    onSuccess: () => {
      toast.success(editing ? "Annotation updated" : "Annotation added");
      setForm({ question: "", answer: "" });
      setEditing(null);
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (annId: string) => api(`${path}/${annId}`, { method: "DELETE" }),
    onSuccess: refresh,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const cfg = agent.data?.draft_config as
    { annotations?: { enabled?: boolean; threshold?: number } } | undefined;
  const threshold = cfg?.annotations?.threshold ?? 0.9;
  const enabled = cfg?.annotations?.enabled ?? true;

  return (
    <div className="space-y-6">
      <PageHeader
        title={`${agent.data?.name ?? "Agent"} · Annotations`}
        description={`Curated answers. When a question is at least ${Math.round(threshold * 100)}% similar to one below, the agent replies with your answer verbatim - no model call, no retrieval.${enabled ? "" : " Annotation replies are currently turned off for this agent."}`}
      />
      {can("editor") ? (
        <Card>
          <CardHeader>
            <CardTitle>{editing ? "Edit annotation" : "Add annotation"}</CardTitle>
            <CardDescription>
              Tip: use the bookmark icon on a Playground reply to start from a real answer.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              aria-label="Question"
              placeholder="Question, e.g. What are the office hours?"
              rows={2}
              value={form.question}
              onChange={(e) => setForm({ ...form, question: e.target.value })}
            />
            <Textarea
              aria-label="Answer"
              placeholder="The exact answer to send"
              rows={4}
              value={form.answer}
              onChange={(e) => setForm({ ...form, answer: e.target.value })}
            />
            <div className="flex justify-end gap-2">
              {editing ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    setEditing(null);
                    setForm({ question: "", answer: "" });
                  }}
                >
                  Cancel
                </Button>
              ) : null}
              <Button
                disabled={!form.question.trim() || !form.answer.trim() || save.isPending}
                onClick={() => save.mutate()}
              >
                {editing ? "Save changes" : "Add annotation"}
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}
      {list.data && list.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No annotations yet.</p>
      ) : null}
      <div className="space-y-3">
        {(list.data?.items ?? []).map((a) => (
          <Card key={a.id}>
            <CardContent className="space-y-2 pt-4">
              <div className="flex items-start justify-between gap-3">
                <p className="font-medium">{a.question}</p>
                {can("editor") ? (
                  <div className="flex shrink-0 gap-1">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Edit annotation"
                      onClick={() => {
                        setEditing(a.id);
                        setForm({ question: a.question, answer: a.answer });
                        window.scrollTo({ top: 0, behavior: "smooth" });
                      }}
                    >
                      <Pencil />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Delete annotation"
                      onClick={() => setToDelete(a)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                ) : null}
              </div>
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">{a.answer}</p>
              <p className="text-xs text-muted-foreground">
                Used {a.hit_count} time{a.hit_count === 1 ? "" : "s"}
                {a.last_hit_at ? ` · last ${timeAgo(a.last_hit_at)}` : ""}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
      <ConfirmDialog
        open={toDelete !== null}
        onOpenChange={(o) => !o && setToDelete(null)}
        title="Delete annotation?"
        description={toDelete?.question}
        onConfirm={() => {
          if (toDelete) remove.mutate(toDelete.id);
          setToDelete(null);
        }}
      />
    </div>
  );
}
