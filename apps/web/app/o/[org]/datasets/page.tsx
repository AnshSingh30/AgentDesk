"use client";

import { Database, Plus } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import * as React from "react";

import { ConnectDialog } from "@/components/connect-dataset-dialog";
import { EmptyState, PageHeader } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Skeleton, StatusBadge, Table, TBody, Td, Th, THead, Tr } from "@/components/ui/display";
import { useDatasets, useOrgId } from "@/hooks/use-org";
import { useRole } from "@/lib/auth";
import { timeAgo } from "@/lib/utils";

function DatasetsInner() {
  const orgId = useOrgId();
  const datasets = useDatasets();
  const search = useSearchParams();
  const { can } = useRole(orgId);
  const [open, setOpen] = React.useState(search.get("new") === "1");
  return (
    <div>
      <PageHeader
        title="Datasets"
        description="Read-only PostgreSQL connections or uploaded spreadsheets, with a reviewed schema the agent can query."
        actions={
          can("editor") ? (
            <Button onClick={() => setOpen(true)}>
              <Plus /> Connect dataset
            </Button>
          ) : null
        }
      />
      {datasets.isPending ? (
        <Skeleton className="h-40" />
      ) : !datasets.data?.length ? (
        <EmptyState
          icon={Database}
          title="No datasets yet"
          description="Connect a Postgres database (we recommend a read-only user) or upload an Excel/CSV file."
          action={can("editor") ? <Button onClick={() => setOpen(true)}>Connect dataset</Button> : undefined}
        />
      ) : (
        <Table>
          <THead>
            <tr>
              <Th>Name</Th>
              <Th>Type</Th>
              <Th>Status</Th>
              <Th>Tables</Th>
              <Th>Updated</Th>
            </tr>
          </THead>
          <TBody>
            {datasets.data.map((d) => (
              <Tr key={d.id}>
                <Td>
                  <Link href={`/o/${orgId}/datasets/${d.id}`} className="font-medium hover:underline">
                    {d.name}
                  </Link>
                  {d.error ? <p className="max-w-md truncate text-xs text-destructive">{d.error}</p> : null}
                </Td>
                <Td className="capitalize text-muted-foreground">{d.type}</Td>
                <Td>
                  <StatusBadge status={d.status} />
                </Td>
                <Td className="text-muted-foreground">
                  {d.table_stats.reviewed ?? 0}/{d.table_stats.tables ?? 0} reviewed
                </Td>
                <Td className="text-muted-foreground">{timeAgo(d.updated_at)}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      )}
      <ConnectDialog open={open} onOpenChange={setOpen} />
    </div>
  );
}

export default function DatasetsPage() {
  return (
    <React.Suspense>
      <DatasetsInner />
    </React.Suspense>
  );
}
