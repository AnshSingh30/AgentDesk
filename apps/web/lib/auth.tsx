"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { api, ApiError } from "@/lib/api";
import type { MeOut } from "@/lib/types";

export function useMe(enabled = true) {
  return useQuery<MeOut, ApiError>({
    queryKey: ["me"],
    queryFn: () => api<MeOut>("/auth/me"),
    retry: (count, err) => err.status !== 401 && count < 2,
    staleTime: 60_000,
    enabled,
  });
}

export function useLogout() {
  const qc = useQueryClient();
  const router = useRouter();
  return async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      qc.clear();
      router.push("/login");
    }
  };
}

/** Client-side guard: redirects to /login on 401 and renders children once the user is known. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  useEffect(() => {
    if (me.isError && me.error.status === 401) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [me.isError, me.error, router, pathname]);
  if (me.isPending) return <div className="p-8 text-sm text-muted-foreground">Loading…</div>;
  if (me.isError)
    return (
      <div className="p-8 text-sm text-destructive">Could not load your session ({me.error.message}).</div>
    );
  return <>{children}</>;
}

export function useRole(orgId: string | undefined) {
  const me = useMe();
  const m = me.data?.memberships.find((x) => x.org_id === orgId);
  const role = m?.role ?? (me.data?.user.is_superadmin ? "owner" : undefined);
  const rank: Record<string, number> = { viewer: 0, editor: 1, admin: 2, owner: 3 };
  return {
    role,
    can: (min: "viewer" | "editor" | "admin" | "owner") => role !== undefined && rank[role] >= rank[min],
    isSuperadmin: !!me.data?.user.is_superadmin,
  };
}
