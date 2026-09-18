"use client";

import type { TemplateOut } from "@/lib/types";

export function TemplateCard({
  t,
  onPick,
  selected,
}: {
  t: TemplateOut;
  onPick: () => void;
  selected?: boolean;
}) {
  const tools = Object.entries(t.enabled_tools)
    .filter(([, v]) => v)
    .map(([k]) => ({ rag: "Knowledge Base", sql: "Dataset", web_search: "Web" })[k] ?? k);
  return (
    <button
      type="button"
      onClick={onPick}
      className={`rounded-xl border p-5 text-left transition-colors hover:border-primary ${selected ? "border-primary bg-primary-soft" : "border-border bg-card"}`}
      aria-pressed={selected}
    >
      <p className="font-medium">{t.name}</p>
      <p className="mt-2 text-sm text-muted-foreground">{t.description}</p>
      <p className="mt-3 text-xs text-muted-foreground">Tools: {tools.length ? tools.join(", ") : "none"}</p>
    </button>
  );
}
