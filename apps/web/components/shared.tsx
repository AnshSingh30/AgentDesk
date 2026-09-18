"use client";

import { Check, Copy, Inbox } from "lucide-react";
import * as React from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/display";
import { Dialog, DialogClose, DialogContent, Input } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between", className)}>
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("flex flex-col items-center justify-center gap-3 px-6 py-14 text-center", className)}>
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary-soft text-primary">
        <Icon className="h-6 w-6" />
      </div>
      <div>
        <p className="font-medium">{title}</p>
        {description ? <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {action}
    </Card>
  );
}

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard blocked */
        }
      }}
      aria-label={label}
    >
      {copied ? <Check className="text-success" /> : <Copy />}
      {copied ? "Copied" : label}
    </Button>
  );
}

export function CodeBlock({ children, className }: { children: string; className?: string }) {
  return (
    <pre
      className={cn("overflow-x-auto rounded-lg bg-muted p-3 font-mono text-xs leading-relaxed", className)}
    >
      <code>{children}</code>
    </pre>
  );
}

const SQL_KEYWORDS =
  /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|ON|USING|AS|AND|OR|NOT|IN|IS|NULL|CASE|WHEN|THEN|ELSE|END|WITH|UNION|ALL|DISTINCT|COUNT|SUM|AVG|MIN|MAX|ROUND|COALESCE|DATE_TRUNC|INTERVAL|CURRENT_DATE|NOW|EXTRACT|FILTER|OVER|PARTITION BY|DESC|ASC|BETWEEN|LIKE|ILIKE|CAST|NULLIF|EXISTS)\b/gi;

/** Tiny SQL highlighter (keywords, strings, numbers) - replaces Shiki to keep the bundle small. */
export function SqlCode({ sql, className }: { sql: string; className?: string }) {
  const parts: React.ReactNode[] = [];
  const re = /('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|(--[^\n]*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  const pushKeywords = (text: string, key: string) => {
    const segs = text.split(SQL_KEYWORDS);
    segs.forEach((seg, i) => {
      if (i % 2 === 1)
        parts.push(
          <span key={`${key}-${i}`} className="font-semibold text-primary">
            {seg}
          </span>,
        );
      else if (seg) parts.push(<React.Fragment key={`${key}-${i}`}>{seg}</React.Fragment>);
    });
  };
  while ((m = re.exec(sql)) !== null) {
    pushKeywords(sql.slice(last, m.index), `k${m.index}`);
    if (m[1])
      parts.push(
        <span key={`s${m.index}`} className="text-emerald-700 dark:text-emerald-300">
          {m[1]}
        </span>,
      );
    else if (m[2])
      parts.push(
        <span key={`n${m.index}`} className="text-amber-700 dark:text-amber-300">
          {m[2]}
        </span>,
      );
    else if (m[3])
      parts.push(
        <span key={`c${m.index}`} className="text-muted-foreground">
          {m[3]}
        </span>,
      );
    last = m.index + m[0].length;
  }
  pushKeywords(sql.slice(last), "tail");
  return (
    <pre
      className={cn(
        "overflow-x-auto whitespace-pre-wrap rounded-lg bg-muted p-3 font-mono text-xs leading-relaxed",
        className,
      )}
    >
      <code>{parts}</code>
    </pre>
  );
}

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("prose-chat text-sm leading-relaxed", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={{
          a: ({ ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

/** Type-the-name-to-confirm dialog for destructive actions. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText,
  onConfirm,
  loading,
  destructive = true,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description?: string;
  confirmText?: string;
  onConfirm: () => void | Promise<void>;
  loading?: boolean;
  destructive?: boolean;
}) {
  const [typed, setTyped] = React.useState("");
  React.useEffect(() => {
    if (!open) setTyped("");
  }, [open]);
  const ok = !confirmText || typed === confirmText;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent title={title} description={description}>
          {confirmText ? (
            <div className="space-y-2">
              <p className="text-sm">
                Type <span className="font-mono font-medium">{confirmText}</span> to confirm.
              </p>
              <Input
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                autoFocus
                aria-label="Confirmation text"
              />
            </div>
          ) : null}
          <div className="mt-6 flex justify-end gap-2">
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              variant={destructive ? "destructive" : "default"}
              disabled={!ok}
              loading={loading}
              onClick={() => onConfirm()}
            >
              Confirm
            </Button>
          </div>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

export function Stat({
  label,
  value,
  hint,
  className,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("p-4", className)}>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </Card>
  );
}
