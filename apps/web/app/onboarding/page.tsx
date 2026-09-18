"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Logo } from "@/components/app-shell";
import { TemplateCard } from "@/components/template-card";
import { Button } from "@/components/ui/button";
import { Alert, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/display";
import { Field, Input, Select } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { RequireAuth, useMe } from "@/lib/auth";
import type { OrgOut, TemplateOut } from "@/lib/types";

const schema = z.object({
  name: z.string().min(2, "Enter the organisation name").max(120),
  industry: z.string().optional(),
  size: z.string().optional(),
});

function Onboarding() {
  const me = useMe();
  const router = useRouter();
  const qc = useQueryClient();
  const [org, setOrg] = React.useState<OrgOut | null>(null);
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<TemplateOut[]>("/templates") });
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { industry: "", size: "" },
  });
  const verified = me.data?.user.email_verified;

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <Logo className="mb-8" />
      {!org ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-xl">Create your organisation</CardTitle>
            <CardDescription>
              All agents, documents and data are scoped to an organisation. You can invite your team
              afterwards.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!verified ? (
              <Alert variant="warning" className="mb-4">
                Verify your email first - we sent you a link. Creating an organisation is enabled after
                verification.
              </Alert>
            ) : null}
            <form
              className="space-y-4"
              onSubmit={form.handleSubmit(async (values) => {
                try {
                  const created = await api<OrgOut>("/orgs", {
                    method: "POST",
                    body: { name: values.name, industry: values.industry || null, size: values.size || null },
                  });
                  await qc.invalidateQueries({ queryKey: ["me"] });
                  setOrg(created);
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              })}
            >
              <Field label="Organisation name" htmlFor="name" error={form.formState.errors.name?.message}>
                <Input id="name" placeholder="Northwind Tech" {...form.register("name")} />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Industry (optional)">
                  <Select
                    value={form.watch("industry") || undefined}
                    onValueChange={(v) => form.setValue("industry", v)}
                    options={[
                      "Software",
                      "Education",
                      "Healthcare",
                      "Manufacturing",
                      "Retail",
                      "Services",
                      "Other",
                    ].map((v) => ({ value: v, label: v }))}
                    placeholder="Select"
                  />
                </Field>
                <Field label="Size (optional)">
                  <Select
                    value={form.watch("size") || undefined}
                    onValueChange={(v) => form.setValue("size", v)}
                    options={["1-10", "11-50", "51-200", "201-500", "501-1000", "1000+"].map((v) => ({
                      value: v,
                      label: v,
                    }))}
                    placeholder="Select"
                  />
                </Field>
              </div>
              <Button type="submit" loading={form.formState.isSubmitting} disabled={!verified}>
                Continue
              </Button>
            </form>
          </CardContent>
        </Card>
      ) : (
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Start from a template</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Templates pre-fill the prompt, tools, routing and guardrails. You can change everything later.
          </p>
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            {(templates.data ?? [])
              .filter((t) => t.key !== "blank")
              .map((t) => (
                <TemplateCard
                  key={t.key}
                  t={t}
                  onPick={() => router.push(`/o/${org.id}/agents/new?template=${t.key}`)}
                />
              ))}
          </div>
          <div className="mt-6 flex gap-3">
            <Button variant="outline" onClick={() => router.push(`/o/${org.id}/agents/new?template=blank`)}>
              Blank agent
            </Button>
            <Button variant="ghost" onClick={() => router.push(`/o/${org.id}`)}>
              Skip for now
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function OnboardingPage() {
  return (
    <RequireAuth>
      <Onboarding />
    </RequireAuth>
  );
}
