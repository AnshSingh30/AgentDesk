import Link from "next/link";

import { Logo } from "@/components/app-shell";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex h-14 items-center px-6">
        <Link href="/">
          <Logo />
        </Link>
      </header>
      <main className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-md">{children}</div>
      </main>
      <footer className="px-6 py-4 text-center text-xs text-muted-foreground">
        <Link href="/legal/terms" className="hover:underline">
          Terms
        </Link>{" "}
        ·{" "}
        <Link href="/legal/privacy" className="hover:underline">
          Privacy
        </Link>
      </footer>
    </div>
  );
}
