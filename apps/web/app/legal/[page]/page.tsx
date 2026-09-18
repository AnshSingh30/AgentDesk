import Link from "next/link";
import { notFound } from "next/navigation";

import { Logo } from "@/components/app-shell";

const PAGES: Record<string, { title: string; sections: [string, string][] }> = {
  terms: {
    title: "Terms of Service",
    sections: [
      [
        "Service",
        "AgentDesk provides a platform to build AI assistants from your documents and data and deploy them on messaging channels. The service is provided on an as-is basis during the pilot phase.",
      ],
      [
        "Your data and your responsibility",
        "You are responsible for the lawful basis of any data you connect (documents, databases, spreadsheets), for the content of those materials, and for controlling who can message your assistants. Datasets are accessed read-only. End-user authentication is available as a paid custom add-on and is not part of the standard service.",
      ],
      [
        "Acceptable use",
        "You must comply with the Acceptable Use Policy and with the WhatsApp Business Messaging Policy when deploying to WhatsApp.",
      ],
      [
        "Accounts",
        "Keep your credentials secure. You are responsible for activity under your account and for the roles you grant to members.",
      ],
      [
        "Availability and changes",
        "We may change or discontinue features. We aim for high availability but do not guarantee uninterrupted service during the pilot.",
      ],
      [
        "Liability",
        "To the extent permitted by law, AgentDesk is not liable for indirect or consequential losses, including losses arising from AI-generated answers. Answers may be inaccurate; review them before relying on them for important decisions.",
      ],
      [
        "Termination",
        "You may delete your organisation at any time; data is permanently removed after 14 days. We may suspend accounts that breach these terms.",
      ],
    ],
  },
  privacy: {
    title: "Privacy Policy",
    sections: [
      [
        "What we process",
        "Account details (name, email), organisation data, uploaded documents and connected datasets, conversation transcripts, usage metrics and audit logs.",
      ],
      [
        "Why",
        "To provide the service, secure it, meter usage and improve reliability. We do not sell personal data.",
      ],
      [
        "Sub-processors",
        "LLM providers you select (OpenAI, Anthropic, Google, Groq, OpenRouter or your custom endpoint), Tavily (web search), Meta (WhatsApp Cloud API), LangSmith (optional tracing, can be disabled per organisation) and our email provider.",
      ],
      [
        "Retention",
        "Run traces are kept for 30 days. Messages are kept for the retention period configured by your organisation (default 180 days). You can export or delete your data at any time.",
      ],
      [
        "Security",
        "Customer secrets are encrypted at rest with envelope encryption, tenants are isolated, connections use TLS, and every SQL query is validated and executed read-only.",
      ],
      [
        "Your rights",
        "Positioned for the India DPDP Act 2023 and GDPR basics: access, correction, export and deletion requests can be made through Settings or by contacting your organisation owner.",
      ],
    ],
  },
  "acceptable-use": {
    title: "Acceptable Use Policy",
    sections: [
      [
        "Do not",
        "Use the service to send spam or unsolicited messages, harass individuals, process data you have no right to process, or attempt to bypass safety, tenant isolation or rate limits.",
      ],
      [
        "Personal data",
        "Do not expose per-person data (marks, attendance, salaries, health) to unauthenticated end users. Hide sensitive columns in the dataset review step.",
      ],
      [
        "WhatsApp",
        "Follow the WhatsApp Business Messaging Policy: opt-in, clear identification of the business, and no prohibited content. Keep the AI-assistant disclosure enabled.",
      ],
      ["Enforcement", "We may pause deployments or suspend organisations that breach this policy."],
    ],
  },
};

export function generateStaticParams() {
  return Object.keys(PAGES).map((page) => ({ page }));
}

export default async function LegalPage({ params }: { params: Promise<{ page: string }> }) {
  const { page } = await params;
  const doc = PAGES[page];
  if (!doc) notFound();
  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <Link href="/">
        <Logo className="mb-8" />
      </Link>
      <h1 className="text-3xl font-semibold tracking-tight">{doc.title}</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Last updated 16 September 2026. Template text for the pilot phase - review with legal counsel before
        commercial launch.
      </p>
      <div className="mt-8 space-y-6">
        {doc.sections.map(([h, t]) => (
          <section key={h}>
            <h2 className="text-lg font-medium">{h}</h2>
            <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{t}</p>
          </section>
        ))}
      </div>
    </div>
  );
}
