"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { CopyButton } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/display";
import {
  Dialog,
  DialogContent,
  Field,
  Input,
  Select,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/primitives";
import { useOrgId } from "@/hooks/use-org";
import { api, errorMessage } from "@/lib/api";
import type { DatasetOut } from "@/lib/types";

const RO_SQL = `CREATE ROLE agentdesk_ro LOGIN PASSWORD '<strong-password>';
GRANT CONNECT ON DATABASE your_db TO agentdesk_ro;
GRANT USAGE ON SCHEMA public TO agentdesk_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agentdesk_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agentdesk_ro;
ALTER ROLE agentdesk_ro SET default_transaction_read_only = on;
ALTER ROLE agentdesk_ro SET statement_timeout = '15s';`;

export function ConnectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const orgId = useOrgId();
  const qc = useQueryClient();
  const router = useRouter();
  const [pg, setPg] = React.useState({
    name: "",
    host: "",
    port: 5432,
    database: "",
    username: "",
    password: "",
    sslmode: "require",
    schemas: "",
  });
  const [sheetName, setSheetName] = React.useState("");
  const [testResult, setTestResult] = React.useState<Record<string, unknown> | null>(null);
  const conn = () => ({
    host: pg.host,
    port: pg.port,
    database: pg.database,
    username: pg.username,
    password: pg.password,
    sslmode: pg.sslmode,
    schemas: pg.schemas
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  });
  const test = useMutation({
    mutationFn: () =>
      api<Record<string, unknown>>(`/orgs/${orgId}/datasets/postgres/test`, { method: "POST", body: conn() }),
    onSuccess: setTestResult,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<DatasetOut>(`/orgs/${orgId}/datasets`, { method: "POST", body }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["datasets", orgId] });
      onOpenChange(false);
      router.push(`/o/${orgId}/datasets/${d.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <DialogContent title="Connect a dataset" wide>
          <Tabs defaultValue="postgres">
            <TabsList>
              <TabsTrigger value="postgres">PostgreSQL</TabsTrigger>
              <TabsTrigger value="spreadsheet">Excel / CSV</TabsTrigger>
            </TabsList>
            <TabsContent value="postgres" className="mt-4 space-y-4">
              <Alert variant="info" title="Use a dedicated read-only user">
                We only ever run read-only queries, but a read-only role is strongly recommended. Run this on
                your database:
                <pre className="mt-2 overflow-x-auto rounded bg-muted p-2 font-mono text-[11px]">
                  {RO_SQL}
                </pre>
                <div className="mt-2">
                  <CopyButton value={RO_SQL} label="Copy SQL" />
                </div>
              </Alert>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Dataset name" className="sm:col-span-2">
                  <Input
                    value={pg.name}
                    onChange={(e) => setPg({ ...pg, name: e.target.value })}
                    placeholder="HRIS"
                  />
                </Field>
                <Field label="Host">
                  <Input
                    value={pg.host}
                    onChange={(e) => setPg({ ...pg, host: e.target.value })}
                    placeholder="db.example.com"
                  />
                </Field>
                <Field label="Port">
                  <Input
                    type="number"
                    value={pg.port}
                    onChange={(e) => setPg({ ...pg, port: Number(e.target.value) })}
                  />
                </Field>
                <Field label="Database">
                  <Input value={pg.database} onChange={(e) => setPg({ ...pg, database: e.target.value })} />
                </Field>
                <Field label="Username">
                  <Input value={pg.username} onChange={(e) => setPg({ ...pg, username: e.target.value })} />
                </Field>
                <Field label="Password">
                  <Input
                    type="password"
                    value={pg.password}
                    onChange={(e) => setPg({ ...pg, password: e.target.value })}
                    autoComplete="off"
                  />
                </Field>
                <Field label="SSL mode">
                  <Select
                    value={pg.sslmode}
                    onValueChange={(v) => setPg({ ...pg, sslmode: v })}
                    options={["require", "verify-ca", "verify-full", "prefer", "disable"].map((v) => ({
                      value: v,
                      label: v,
                    }))}
                  />
                </Field>
                <Field label="Schemas (optional, comma-separated)" className="sm:col-span-2">
                  <Input
                    value={pg.schemas}
                    onChange={(e) => setPg({ ...pg, schemas: e.target.value })}
                    placeholder="public"
                  />
                </Field>
              </div>
              {testResult ? (
                testResult.ok ? (
                  <Alert variant={(testResult.warnings as string[])?.length ? "warning" : "success"}>
                    Connected (PostgreSQL {String(testResult.server_version)}). Schemas:{" "}
                    {(testResult.schemas as string[])?.join(", ")}.
                    {(testResult.warnings as string[])?.map((w) => (
                      <p key={w} className="mt-1">
                        {w}
                      </p>
                    ))}
                  </Alert>
                ) : (
                  <Alert variant="destructive">{String(testResult.error)}</Alert>
                )
              ) : null}
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => test.mutate()}
                  loading={test.isPending}
                  disabled={!pg.host || !pg.database || !pg.username}
                >
                  Test connection
                </Button>
                <Button
                  onClick={() => create.mutate({ type: "postgres", name: pg.name, connection: conn() })}
                  loading={create.isPending}
                  disabled={!pg.name || !pg.host || !pg.database || !pg.username}
                >
                  Connect & introspect
                </Button>
              </div>
            </TabsContent>
            <TabsContent value="spreadsheet" className="mt-4 space-y-4">
              <p className="text-sm text-muted-foreground">
                Each sheet becomes a table in an isolated DuckDB file. You will review headers, column names
                and types before import.
              </p>
              <Field label="Dataset name">
                <Input
                  value={sheetName}
                  onChange={(e) => setSheetName(e.target.value)}
                  placeholder="Sales 2026"
                />
              </Field>
              <div className="flex justify-end">
                <Button
                  onClick={() => create.mutate({ type: "spreadsheet", name: sheetName })}
                  loading={create.isPending}
                  disabled={!sheetName}
                >
                  Create & upload file
                </Button>
              </div>
            </TabsContent>
          </Tabs>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
