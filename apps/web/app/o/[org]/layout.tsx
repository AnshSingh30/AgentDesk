"use client";

import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/lib/auth";

export default function OrgLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <AppShell>{children}</AppShell>
    </RequireAuth>
  );
}
