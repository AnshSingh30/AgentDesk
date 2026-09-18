import { ArrowRight, BookOpen, Database, Globe, MessageCircle, ShieldCheck, Sparkles } from "lucide-react";
import Link from "next/link";

import { Logo } from "@/components/app-shell";
import { Button } from "@/components/ui/button";

const STEPS = [
  {
    n: 1,
    title: "Pick a template",
    text: "HR Onboarding, HR Analytics or School Parent Helpdesk - or start blank. The prompt, tools and guardrails come pre-filled.",
  },
  {
    n: 2,
    title: "Connect your knowledge",
    text: "Upload PDFs and policies, or connect a read-only Postgres database or spreadsheet. We introspect the schema and draft descriptions for you.",
  },
  {
    n: 3,
    title: "Test in the Playground",
    text: "See the route taken, the chunks retrieved, the SQL generated and the cost - before anyone else does.",
  },
  {
    n: 4,
    title: "Deploy to WhatsApp",
    text: "Paste your WhatsApp Cloud API credentials, verify the webhook and go live. Versions, rollback and analytics included.",
  },
];

const TEMPLATES = [
  {
    icon: BookOpen,
    name: "HR Onboarding Assistant",
    text: "Answers leave, benefits, policy and IT-setup questions from your handbook, with citations.",
  },
  {
    icon: Database,
    name: "HR Analytics Assistant",
    text: "Headcount, attrition, hiring funnel and leave trends from your HRIS, in plain English. Hidden columns stay hidden.",
  },
  {
    icon: MessageCircle,
    name: "School Parent Helpdesk",
    text: "Fees, timings, calendar, transport and admissions - in English, Hindi or Hinglish, straight to parents' phones.",
  },
];

const FAQ = [
  [
    "Do I need an engineer?",
    "No. Templates, defaults and the guided builder cover the common cases. Developers can bring their own model keys and tune retrieval and SQL settings in Advanced.",
  ],
  [
    "Is my data safe?",
    "Datasets are read-only by construction, every SQL query is validated before it runs, secrets are encrypted at rest and tenants are fully isolated. You control retention and can export or delete everything.",
  ],
  [
    "Which models can I use?",
    "The platform default, your own OpenAI, Anthropic, Gemini, Groq or OpenRouter key, or any OpenAI-compatible endpoint such as vLLM or Ollama.",
  ],
  [
    "How does WhatsApp work?",
    "You connect your own WhatsApp Cloud API number (a free test number works). We give you a webhook URL and verify token; messages are answered in seconds.",
  ],
];

export default function LandingPage() {
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <Logo />
        <nav className="flex items-center gap-2">
          <Button variant="ghost" asChild>
            <Link href="/login">Sign in</Link>
          </Button>
          <Button asChild>
            <Link href="/signup">Get started</Link>
          </Button>
        </nav>
      </header>

      <section className="mx-auto max-w-6xl px-6 pb-16 pt-12 lg:pt-20">
        <div className="grid items-center gap-12 lg:grid-cols-2">
          <div>
            <span className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1 text-xs font-medium text-muted-foreground">
              <Sparkles className="h-3.5 w-3.5 text-primary" /> No-code custom AI agents
            </span>
            <h1 className="mt-5 text-4xl font-semibold tracking-tight lg:text-5xl">
              Your documents and data, answering questions on WhatsApp.
            </h1>
            <p className="mt-5 text-lg text-muted-foreground">
              AgentDesk lets HR teams, schools and ops leads build an assistant that answers from their own
              policies and databases - and deploy it on the channel people already use.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button size="lg" asChild>
                <Link href="/signup">
                  Build your first agent <ArrowRight />
                </Link>
              </Button>
              <Button size="lg" variant="outline" asChild>
                <Link href="#how">See how it works</Link>
              </Button>
            </div>
            <p className="mt-4 text-xs text-muted-foreground">
              Sign-up to a working bot in the Playground in under 15 minutes.
            </p>
          </div>
          <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
            <div className="rounded-xl bg-[#e5ddd5] p-4 dark:bg-[#0b141a]">
              <div className="mb-3 flex items-center gap-2 text-xs text-muted-foreground">
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-whatsapp text-white">
                  <MessageCircle className="h-4 w-4" />
                </span>
                Northwind HR Buddy · WhatsApp
              </div>
              <ChatBubble side="right">
                How many casual leaves do I get and can I carry them forward?
              </ChatBubble>
              <ChatBubble side="left">
                You get <b>12 casual leaves</b> per year, credited at 1 per month. They{" "}
                <b>can&apos;t be carried forward</b> and lapse on 31 December.
                <br />
                <span className="text-[11px] text-muted-foreground">📄 Sources: Leave Policy (p.1)</span>
              </ChatBubble>
              <ChatBubble side="right">Attrition rate by department, last 12 months?</ChatBubble>
              <ChatBubble side="left">
                Sales is highest at <b>14.2%</b>, Finance lowest at <b>5.1%</b>. Period: Sep 2025 - Sep 2026,
                attrition = exits / avg headcount.
              </ChatBubble>
            </div>
          </div>
        </div>
      </section>

      <section id="how" className="border-y border-border bg-card">
        <div className="mx-auto max-w-6xl px-6 py-16">
          <h2 className="text-2xl font-semibold tracking-tight">How it works</h2>
          <div className="mt-8 grid gap-6 md:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((s) => (
              <div key={s.n} className="rounded-xl border border-border p-5">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary-soft text-sm font-semibold text-primary">
                  {s.n}
                </span>
                <h3 className="mt-4 font-medium">{s.title}</h3>
                <p className="mt-2 text-sm text-muted-foreground">{s.text}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-6 py-16">
        <h2 className="text-2xl font-semibold tracking-tight">Templates that ship with demo data</h2>
        <div className="mt-8 grid gap-6 md:grid-cols-3">
          {TEMPLATES.map((t) => (
            <div key={t.name} className="rounded-xl border border-border bg-card p-5">
              <t.icon className="h-6 w-6 text-primary" />
              <h3 className="mt-4 font-medium">{t.name}</h3>
              <p className="mt-2 text-sm text-muted-foreground">{t.text}</p>
            </div>
          ))}
        </div>
        <div className="mt-10 grid gap-4 rounded-xl border border-border bg-card p-6 md:grid-cols-3">
          {[
            [
              ShieldCheck,
              "Safe by construction",
              "Read-only datasets, AST-validated SQL, encrypted secrets, tenant isolation, audit logs.",
            ],
            [
              Globe,
              "Any model",
              "Platform default, bring your own key, or a custom OpenAI-compatible endpoint.",
            ],
            [
              Database,
              "Transparent testing",
              "Route, chunks, SQL, tokens and latency for every answer in the Playground.",
            ],
          ].map(([Icon, title, text]) => {
            const I = Icon as typeof ShieldCheck;
            return (
              <div key={title as string} className="flex gap-3">
                <I className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
                <div>
                  <p className="font-medium">{title as string}</p>
                  <p className="text-sm text-muted-foreground">{text as string}</p>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="border-t border-border bg-card">
        <div className="mx-auto max-w-4xl px-6 py-16">
          <h2 className="text-2xl font-semibold tracking-tight">Questions</h2>
          <dl className="mt-6 divide-y divide-border">
            {FAQ.map(([q, a]) => (
              <div key={q} className="py-4">
                <dt className="font-medium">{q}</dt>
                <dd className="mt-1 text-sm text-muted-foreground">{a}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-10 rounded-2xl bg-sidebar px-8 py-10 text-center text-sidebar-foreground">
            <h3 className="text-2xl font-semibold">Ready to put your knowledge on WhatsApp?</h3>
            <Button size="lg" className="mt-6" asChild>
              <Link href="/signup">Create a free account</Link>
            </Button>
          </div>
        </div>
      </section>

      <footer className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-8 text-xs text-muted-foreground">
        <Logo className="text-sm" />
        <div className="flex gap-4">
          <Link href="/legal/terms" className="hover:underline">
            Terms
          </Link>
          <Link href="/legal/privacy" className="hover:underline">
            Privacy
          </Link>
          <Link href="/legal/acceptable-use" className="hover:underline">
            Acceptable use
          </Link>
        </div>
      </footer>
    </div>
  );
}

function ChatBubble({ side, children }: { side: "left" | "right"; children: React.ReactNode }) {
  return (
    <div className={side === "right" ? "mb-2 flex justify-end" : "mb-2 flex justify-start"}>
      <div
        className={
          side === "right"
            ? "max-w-[85%] rounded-lg rounded-tr-none bg-[#d9fdd3] px-3 py-2 text-sm text-black dark:bg-[#005c4b] dark:text-white"
            : "max-w-[85%] rounded-lg rounded-tl-none bg-white px-3 py-2 text-sm text-black dark:bg-[#202c33] dark:text-white"
        }
      >
        {children}
      </div>
    </div>
  );
}
