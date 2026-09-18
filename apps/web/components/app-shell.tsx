"use client";

import {
  BarChart3,
  BookOpen,
  Bot,
  Building2,
  ChevronsUpDown,
  Database,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  Settings,
  Shield,
  Sun,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/display";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/primitives";
import { useOrgId } from "@/hooks/use-org";
import { api } from "@/lib/api";
import { useLogout, useMe } from "@/lib/auth";
import { cn, initials } from "@/lib/utils";

const NAV = [
  { href: "", label: "Dashboard", icon: LayoutDashboard },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/knowledge", label: "Knowledge", icon: BookOpen },
  { href: "/datasets", label: "Datasets", icon: Database },
  { href: "/conversations", label: "Conversations", icon: MessageSquare },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/settings/general", label: "Settings", icon: Settings, match: "/settings" },
];

export function Logo({ className, light }: { className?: string; light?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2 font-semibold tracking-tight", className)}>
      <span
        className={cn(
          "flex h-7 w-7 items-center justify-center rounded-lg text-sm",
          light ? "bg-white/10 text-white" : "bg-primary text-primary-foreground",
        )}
      >
        AD
      </span>
      AgentDesk
    </span>
  );
}

export function ThemeToggle({ className }: { className?: string }) {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  if (!mounted) return <span className={cn("h-8 w-8", className)} />;
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className={className}
      aria-label="Toggle theme"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
    >
      {resolvedTheme === "dark" ? <Sun /> : <Moon />}
    </Button>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const orgId = useOrgId();
  const pathname = usePathname();
  const router = useRouter();
  const me = useMe();
  const logout = useLogout();
  const [open, setOpen] = React.useState(false);
  const base = `/o/${orgId}`;
  const memberships = me.data?.memberships ?? [];
  const current = memberships.find((m) => m.org_id === orgId);
  const user = me.data?.user;

  const nav = (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      {NAV.map((item) => {
        const href = `${base}${item.href}`;
        const active =
          item.href === "" ? pathname === base : pathname.startsWith(`${base}${item.match ?? item.href}`);
        return (
          <Link
            key={item.label}
            href={href}
            onClick={() => setOpen(false)}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm text-sidebar-muted transition-colors hover:bg-sidebar-active hover:text-sidebar-foreground",
              active && "bg-sidebar-active text-sidebar-foreground",
            )}
            aria-current={active ? "page" : undefined}
          >
            <item.icon className="h-4 w-4" />
            {item.label}
          </Link>
        );
      })}
      {user?.is_superadmin ? (
        <Link
          href="/admin"
          className="mt-2 flex items-center gap-3 rounded-md px-3 py-2 text-sm text-sidebar-muted hover:bg-sidebar-active hover:text-sidebar-foreground"
        >
          <Shield className="h-4 w-4" /> Operator console
        </Link>
      ) : null}
    </nav>
  );

  const orgSwitcher = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm text-sidebar-foreground hover:bg-sidebar-active"
          aria-label="Switch organisation"
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-white/10 text-xs font-semibold">
            {initials(current?.org_name ?? "?")}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium">{current?.org_name ?? "Organisation"}</span>
            <span className="block truncate text-xs text-sidebar-muted capitalize">
              {current?.role ?? ""}
            </span>
          </span>
          <ChevronsUpDown className="h-4 w-4 text-sidebar-muted" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuLabel>Organisations</DropdownMenuLabel>
        {memberships.map((m) => (
          <DropdownMenuItem key={m.org_id} onSelect={() => router.push(`/o/${m.org_id}`)}>
            <Building2 />
            <span className="flex-1 truncate">{m.org_name}</span>
            <span className="text-xs capitalize text-muted-foreground">{m.role}</span>
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => router.push("/onboarding")}>Create organisation</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  const sidebar = (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex items-center justify-between px-4 py-4">
        <Logo light />
        <button
          className="rounded-md p-1 text-sidebar-muted hover:text-sidebar-foreground lg:hidden"
          onClick={() => setOpen(false)}
          aria-label="Close menu"
        >
          <X className="h-5 w-5" />
        </button>
      </div>
      <div className="px-2">{orgSwitcher}</div>
      <div className="mt-4 flex-1 overflow-y-auto px-2">{nav}</div>
      <div className="border-t border-white/10 p-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-sidebar-active"
              aria-label="Account menu"
            >
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
                {initials(user?.name ?? "?")}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate">{user?.name}</span>
                <span className="block truncate text-xs text-sidebar-muted">{user?.email}</span>
              </span>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56">
            <DropdownMenuItem onSelect={() => router.push(`${base}/settings/general`)}>
              <Settings /> Settings
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => logout()} destructive>
              <LogOut /> Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-64 shrink-0 lg:block">{sidebar}</aside>
      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72">{sidebar}</aside>
        </div>
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center gap-3 border-b border-border bg-card px-4 lg:px-6">
          <button className="rounded-md p-1 lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu className="h-5 w-5" />
          </button>
          <div className="flex-1" />
          {user && !user.email_verified ? <ResendVerification /> : null}
          <ThemeToggle />
        </header>
        {user && !user.email_verified ? (
          <div className="px-4 pt-4 lg:px-6">
            <Alert variant="warning" title="Verify your email">
              Check your inbox for the verification link. Creating agents is enabled after verification.
            </Alert>
          </div>
        ) : null}
        <main className="flex-1 px-4 py-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}

function ResendVerification() {
  const [sent, setSent] = React.useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={sent}
      onClick={async () => {
        try {
          await api("/auth/resend-verification", { method: "POST" });
          setSent(true);
          toast.success("Verification email sent");
        } catch (e) {
          toast.error((e as Error).message);
        }
      }}
    >
      {sent ? "Email sent" : "Resend verification"}
    </Button>
  );
}
