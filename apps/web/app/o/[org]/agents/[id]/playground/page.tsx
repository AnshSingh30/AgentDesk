"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, PanelRightClose, PanelRightOpen } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";

import { ChatPane, DebugPanel, PlaygroundToolbar, usePlayground } from "@/components/playground/chat";
import { Button } from "@/components/ui/button";
import { Select, Switch } from "@/components/ui/primitives";
import { useAgent } from "@/hooks/use-agent";
import { useOrgId } from "@/hooks/use-org";
import { api } from "@/lib/api";
import type { VersionOut } from "@/lib/types";

export default function PlaygroundPage() {
  const { id } = useParams<{ id: string }>();
  const orgId = useOrgId();
  const agent = useAgent(id);
  const versions = useQuery({
    queryKey: ["versions", orgId, id],
    queryFn: () => api<VersionOut[]>(`/orgs/${orgId}/agents/${id}/versions`),
  });
  const [versionId, setVersionId] = React.useState<string | null>(null);
  const [debug, setDebug] = React.useState(true);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [wa, setWa] = React.useState(false);
  const pg = usePlayground(id, versionId);
  const selectedMsg =
    pg.messages.find((m) => m.id === selected) ??
    [...pg.messages].reverse().find((m) => m.role === "assistant") ??
    null;

  return (
    <div className="-m-4 flex h-[calc(100vh-3.5rem)] flex-col lg:-m-8">
      <div className="flex items-center gap-3 border-b border-border bg-card px-4 py-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/o/${orgId}/agents/${id}/build/review`}>
            <ArrowLeft /> Builder
          </Link>
        </Button>
        <h1 className="truncate font-medium">{agent.data?.name ?? "Playground"}</h1>
        <div className="ml-auto flex items-center gap-3">
          <div className="w-44">
            <Select
              value={versionId ?? "draft"}
              onValueChange={(v) => setVersionId(v === "draft" ? null : v)}
              options={[
                { value: "draft", label: "Draft (unpublished)" },
                ...(versions.data ?? []).map((v) => ({ value: v.id, label: `v${v.version_number}` })),
              ]}
            />
          </div>
          <Button variant="ghost" size="sm" onClick={() => setDebug((d) => !d)} aria-pressed={debug}>
            {debug ? <PanelRightClose /> : <PanelRightOpen />} Debug
          </Button>
        </div>
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <PlaygroundToolbar
            pg={pg}
            extra={
              <label className="flex items-center gap-2">
                <Switch checked={wa} onCheckedChange={setWa} /> WhatsApp preview
              </label>
            }
          />
          <ChatPane pg={pg} selected={selectedMsg?.id ?? null} onSelect={setSelected} whatsappPreview={wa} />
        </div>
        {debug ? (
          <aside className="hidden w-[420px] shrink-0 border-l border-border bg-card lg:block">
            <DebugPanel message={selectedMsg} />
          </aside>
        ) : null}
      </div>
    </div>
  );
}
