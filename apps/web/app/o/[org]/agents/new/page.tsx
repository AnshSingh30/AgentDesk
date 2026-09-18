"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { TemplateCard } from "@/components/template-card";
import { PageHeader } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, Skeleton } from "@/components/ui/display";
import { Field, Input } from "@/components/ui/primitives";
import { useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import type { AgentOut, TemplateOut } from "@/lib/types";

function NewAgent() {
  const orgId = useOrgId();
  const router = useRouter();
  const search = useSearchParams();
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<TemplateOut[]>("/templates") });
  const [selected, setSelected] = React.useState<string>(search.get("template") ?? "");
  const [name, setName] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    const t = templates.data?.find((x) => x.key === selected);
    if (t && !name) setName(t.key === "blank" ? "My agent" : t.name);
  }, [selected, templates.data, name]);

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="New agent"
        description="Choose a template. It pre-fills the instructions, tools, routing rules, guardrails and starter questions."
      />
      {templates.isPending ? (
        <Skeleton className="h-40" />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {templates.data?.map((t) => (
            <TemplateCard key={t.key} t={t} selected={selected === t.key} onPick={() => setSelected(t.key)} />
          ))}
        </div>
      )}
      <Card className="mt-6">
        <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-end">
          <Field label="Agent name" htmlFor="agent-name" className="flex-1">
            <Input
              id="agent-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. HR Buddy"
            />
          </Field>
          <Button
            disabled={!selected || !name.trim()}
            loading={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const agent = await api<AgentOut>(`/orgs/${orgId}/agents`, {
                  method: "POST",
                  body: { template_key: selected, name: name.trim() },
                });
                router.push(`/o/${orgId}/agents/${agent.id}/build/instructions`);
              } catch (e) {
                toast.error(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Create agent
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

export default function NewAgentPage() {
  return (
    <React.Suspense>
      <NewAgent />
    </React.Suspense>
  );
}
