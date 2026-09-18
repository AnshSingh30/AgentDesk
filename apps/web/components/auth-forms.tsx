"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Alert, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/display";
import { Field, Input } from "@/components/ui/primitives";
import { api, API_URL, errorMessage } from "@/lib/api";
import type { MeOut } from "@/lib/types";

const loginSchema = z.object({
  email: z.string().email("Enter a valid email"),
  password: z.string().min(1, "Enter your password"),
});
const signupSchema = z.object({
  name: z.string().min(1, "Enter your name").max(120),
  email: z.string().email("Enter a valid email"),
  password: z.string().min(10, "Use at least 10 characters"),
});

function afterAuth(me: MeOut, next: string | null) {
  if (next && next.startsWith("/")) return next;
  if (me.memberships.length > 0) return `/o/${me.memberships[0].org_id}`;
  return "/onboarding";
}

export function LoginForm() {
  const router = useRouter();
  const search = useSearchParams();
  const qc = useQueryClient();
  const form = useForm<z.infer<typeof loginSchema>>({ resolver: zodResolver(loginSchema) });
  const [error, setError] = React.useState<string | null>(null);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Welcome back</CardTitle>
        <CardDescription>Sign in to your AgentDesk account.</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit(async (values) => {
            setError(null);
            try {
              const me = await api<MeOut>("/auth/login", { method: "POST", body: values });
              qc.setQueryData(["me"], me);
              router.push(afterAuth(me, search.get("next")));
            } catch (e) {
              setError(errorMessage(e));
            }
          })}
        >
          {error ? <Alert variant="destructive">{error}</Alert> : null}
          <Field label="Email" htmlFor="email" error={form.formState.errors.email?.message}>
            <Input id="email" type="email" autoComplete="email" {...form.register("email")} />
          </Field>
          <Field label="Password" htmlFor="password" error={form.formState.errors.password?.message}>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              {...form.register("password")}
            />
          </Field>
          <Button type="submit" className="w-full" loading={form.formState.isSubmitting}>
            Sign in
          </Button>
          <Button
            type="button"
            variant="outline"
            className="w-full"
            onClick={() => (window.location.href = `${API_URL}/v1/auth/oauth/google/start`)}
          >
            Continue with Google
          </Button>
        </form>
        <div className="mt-4 flex justify-between text-sm">
          <Link href="/forgot-password" className="text-muted-foreground hover:underline">
            Forgot password?
          </Link>
          <Link href="/signup" className="text-primary hover:underline">
            Create an account
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

export function SignupForm() {
  const router = useRouter();
  const qc = useQueryClient();
  const form = useForm<z.infer<typeof signupSchema>>({ resolver: zodResolver(signupSchema) });
  const [error, setError] = React.useState<string | null>(null);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Create your account</CardTitle>
        <CardDescription>Build your first agent in under 15 minutes.</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit(async (values) => {
            setError(null);
            try {
              const me = await api<MeOut>("/auth/signup", { method: "POST", body: values });
              qc.setQueryData(["me"], me);
              toast.success("Account created. Check your email to verify it.");
              router.push("/onboarding");
            } catch (e) {
              setError(errorMessage(e));
            }
          })}
        >
          {error ? <Alert variant="destructive">{error}</Alert> : null}
          <Field label="Name" htmlFor="name" error={form.formState.errors.name?.message}>
            <Input id="name" autoComplete="name" {...form.register("name")} />
          </Field>
          <Field label="Work email" htmlFor="email" error={form.formState.errors.email?.message}>
            <Input id="email" type="email" autoComplete="email" {...form.register("email")} />
          </Field>
          <Field
            label="Password"
            htmlFor="password"
            hint="At least 10 characters."
            error={form.formState.errors.password?.message}
          >
            <Input id="password" type="password" autoComplete="new-password" {...form.register("password")} />
          </Field>
          <Button type="submit" className="w-full" loading={form.formState.isSubmitting}>
            Create account
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            By signing up you agree to the{" "}
            <Link href="/legal/terms" className="underline">
              Terms
            </Link>{" "}
            and{" "}
            <Link href="/legal/privacy" className="underline">
              Privacy Policy
            </Link>
            .
          </p>
        </form>
        <p className="mt-4 text-center text-sm">
          Already have an account?{" "}
          <Link href="/login" className="text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}

export function ForgotPasswordForm() {
  const [sent, setSent] = React.useState(false);
  const form = useForm<{ email: string }>({
    resolver: zodResolver(z.object({ email: z.string().email("Enter a valid email") })),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Reset your password</CardTitle>
        <CardDescription>We&apos;ll email you a link that works for 30 minutes.</CardDescription>
      </CardHeader>
      <CardContent>
        {sent ? (
          <Alert variant="success">If an account exists for that email, a reset link has been sent.</Alert>
        ) : (
          <form
            className="space-y-4"
            onSubmit={form.handleSubmit(async (values) => {
              await api("/auth/forgot-password", { method: "POST", body: values });
              setSent(true);
            })}
          >
            <Field label="Email" htmlFor="email" error={form.formState.errors.email?.message}>
              <Input id="email" type="email" {...form.register("email")} />
            </Field>
            <Button type="submit" className="w-full" loading={form.formState.isSubmitting}>
              Send reset link
            </Button>
          </form>
        )}
        <p className="mt-4 text-center text-sm">
          <Link href="/login" className="text-primary hover:underline">
            Back to sign in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}

export function ResetPasswordForm() {
  const search = useSearchParams();
  const router = useRouter();
  const token = search.get("token") ?? "";
  const form = useForm<{ password: string }>({
    resolver: zodResolver(z.object({ password: z.string().min(10, "Use at least 10 characters") })),
  });
  const [error, setError] = React.useState<string | null>(null);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Choose a new password</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit(async (values) => {
            try {
              await api("/auth/reset-password", {
                method: "POST",
                body: { token, password: values.password },
              });
              toast.success("Password updated. Please sign in.");
              router.push("/login");
            } catch (e) {
              setError(errorMessage(e));
            }
          })}
        >
          {error ? <Alert variant="destructive">{error}</Alert> : null}
          <Field label="New password" htmlFor="password" error={form.formState.errors.password?.message}>
            <Input id="password" type="password" autoComplete="new-password" {...form.register("password")} />
          </Field>
          <Button type="submit" className="w-full" loading={form.formState.isSubmitting} disabled={!token}>
            Update password
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

export function VerifyEmail() {
  const search = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const token = search.get("token") ?? "";
  const [state, setState] = React.useState<"pending" | "ok" | "error">("pending");
  const [error, setError] = React.useState("");
  React.useEffect(() => {
    if (!token) {
      setState("error");
      setError("Missing token");
      return;
    }
    api("/auth/verify-email", { method: "POST", body: { token } })
      .then(() => {
        setState("ok");
        qc.invalidateQueries({ queryKey: ["me"] });
        setTimeout(() => router.push("/onboarding"), 1200);
      })
      .catch((e) => {
        setState("error");
        setError(errorMessage(e));
      });
  }, [token, router, qc]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xl">Email verification</CardTitle>
      </CardHeader>
      <CardContent>
        {state === "pending" ? <p className="text-sm text-muted-foreground">Verifying…</p> : null}
        {state === "ok" ? <Alert variant="success">Your email is verified. Redirecting…</Alert> : null}
        {state === "error" ? (
          <Alert variant="destructive">
            {error}.{" "}
            <Link href="/login" className="underline">
              Sign in
            </Link>{" "}
            and request a new link.
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}
