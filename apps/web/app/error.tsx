"use client";

import { Button } from "@/components/ui/button";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <p className="text-4xl font-semibold tracking-tight">Something went wrong</p>
      <p className="max-w-md text-sm text-muted-foreground">{error.message}</p>
      {error.digest ? (
        <p className="font-mono text-xs text-muted-foreground">request id: {error.digest}</p>
      ) : null}
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
