"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Plus } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState, PageHeader } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Skeleton } from "@/components/ui/display";
import { Dialog, DialogClose, DialogContent, Field, Input, Textarea } from "@/components/ui/primitives";
import { useKnowledgeBases, useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import { useRole } from "@/lib/auth";
import type { KBOut } from "@/lib/types";
import { timeAgo } from "@/lib/utils";

export default function KnowledgePage() {
  const orgId = useOrgId();
  const kbs = useKnowledgeBases();
  const qc = useQueryClient();
  const { can } = useRole(orgId);
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [desc, setDesc] = React.useState("");
  const create = useMutation({
    mutationFn: () =>
      api<KBOut>(`/orgs/${orgId}/knowledge-bases`, {
        method: "POST",
        body: { name, description: desc || null },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kbs", orgId] });
      setOpen(false);
      setName("");
      setDesc("");
      toast.success("Knowledge base created");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div>
      <PageHeader
        title="Knowledge bases"
        description="Collections of documents indexed for retrieval. A knowledge base can be attached to several agents."
        actions={
          can("editor") ? (
            <Button onClick={() => setOpen(true)}>
              <Plus /> New knowledge base
            </Button>
          ) : null
        }
      />
      {kbs.isPending ? (
        <Skeleton className="h-40" />
      ) : !kbs.data?.length ? (
        <EmptyState
          icon={BookOpen}
          title="No knowledge bases yet"
          description="Create one and upload PDFs, Word files, text or Markdown."
          action={
            can("editor") ? <Button onClick={() => setOpen(true)}>New knowledge base</Button> : undefined
          }
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {kbs.data.map((kb) => (
            <Link key={kb.id} href={`/o/${orgId}/knowledge/${kb.id}`}>
              <Card className="h-full transition-colors hover:border-primary">
                <CardHeader>
                  <CardTitle>{kb.name}</CardTitle>
                  <CardDescription>{kb.description || "No description"}</CardDescription>
                </CardHeader>
                <CardContent className="text-xs text-muted-foreground">
                  {kb.doc_count} documents · {kb.chunk_count} chunks ·{" "}
                  {kb.embedding_model.replace("fastembed/", "")} · updated {timeAgo(kb.updated_at)}
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        {open ? (
          <DialogContent title="New knowledge base">
            <div className="space-y-4">
              <Field label="Name" htmlFor="kb-name">
                <Input
                  id="kb-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="HR Policies"
                  autoFocus
                />
              </Field>
              <Field label="Description (optional)" hint="Shown to the router to decide when to search here.">
                <Textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={2} />
              </Field>
              <div className="flex justify-end gap-2">
                <DialogClose asChild>
                  <Button variant="outline">Cancel</Button>
                </DialogClose>
                <Button onClick={() => create.mutate()} disabled={!name.trim()} loading={create.isPending}>
                  Create
                </Button>
              </div>
            </div>
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  );
}
