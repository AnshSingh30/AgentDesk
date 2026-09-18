"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";

import { Logo } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Alert, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/display";
import { api, errorMessage } from "@/lib/api";
import { useMe } from "@/lib/auth";
import type { OrgOut } from "@/lib/types";

export default function InvitePage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const preview = useQuery({
    queryKey: ["invite", token],
    queryFn: () => api<{ org_name: string; email: string; role: string }>(`/invitations/${token}`),
  });
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const loggedIn = !!me.data;
  const next = `/invite/${token}`;

  return (
    <div className="mx-auto max-w-md px-6 py-16">
      <Logo className="mb-8" />
      <Card>
        <CardHeader>
          <CardTitle className="text-xl">You&apos;ve been invited</CardTitle>
          {preview.data ? (
            <CardDescription>
              Join <b>{preview.data.org_name}</b> as <b>{preview.data.role}</b> ({preview.data.email}).
            </CardDescription>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-4">
          {preview.isError ? <Alert variant="destructive">{errorMessage(preview.error)}</Alert> : null}
          {error ? <Alert variant="destructive">{error}</Alert> : null}
          {!loggedIn ? (
            <div className="flex gap-2">
              <Button asChild>
                <Link href={`/signup?next=${encodeURIComponent(next)}`}>Create account</Link>
              </Button>
              <Button variant="outline" asChild>
                <Link href={`/login?next=${encodeURIComponent(next)}`}>Sign in</Link>
              </Button>
            </div>
          ) : (
            <Button
              loading={busy}
              disabled={preview.isError}
              onClick={async () => {
                setBusy(true);
                try {
                  const org = await api<OrgOut>(`/invitations/${token}/accept`, { method: "POST" });
                  await qc.invalidateQueries({ queryKey: ["me"] });
                  router.push(`/o/${org.id}`);
                } catch (e) {
                  setError(errorMessage(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              Accept invitation
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
