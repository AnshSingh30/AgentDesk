export interface UserOut {
  id: string;
  email: string;
  name: string;
  email_verified: boolean;
  is_superadmin: boolean;
  created_at: string;
}

export interface MembershipOut {
  id: string;
  org_id: string;
  org_name: string;
  org_slug: string;
  role: "owner" | "admin" | "editor" | "viewer";
}

export interface MeOut {
  user: UserOut;
  memberships: MembershipOut[];
  csrf_token?: string | null;
}

export interface OrgOut {
  id: string;
  name: string;
  slug: string;
  industry: string | null;
  size: string | null;
  settings: Record<string, unknown>;
  role?: string | null;
  created_at: string;
}

export type ToolName = "rag" | "sql" | "web_search";

export interface AgentConfig {
  instructions: {
    system_prompt: string;
    starter_questions: string[];
    welcome_message: string | null;
    ai_disclosure: boolean;
  };
  tools: {
    rag: {
      enabled: boolean;
      kb_ids: string[];
      top_k: number;
      threshold: number;
      search_mode: "hybrid" | "dense" | "sparse";
      rerank: { enabled: boolean; provider: "cohere" | "local" | null; candidates: number };
      query_rewrite: boolean;
      citations: boolean;
      mmr: boolean;
    };
    sql: {
      enabled: boolean;
      dataset_id: string | null;
      max_rows: number;
      max_attempts: number;
      confirm_in_playground: boolean;
    };
    web_search: {
      enabled: boolean;
      max_results: number;
      depth: "basic" | "advanced";
      include_domains: string[];
      exclude_domains: string[];
    };
  };
  routing: { rag_instructions: string; sql_instructions: string; web_instructions: string };
  model: {
    mode: "platform" | "byo" | "custom";
    credential_id: string | null;
    provider: string | null;
    model: string | null;
    api_base: string | null;
    temperature: number;
    max_output_tokens: number;
    utility_model: string | null;
    context_window?: number | null;
  };
  guardrails: {
    no_answer_message: string;
    allowed_topics: string;
    blocked_topics: string[];
    blocked_keywords: string[];
    language: string;
    tone: "friendly" | "professional" | "concise";
    web_fallback: boolean;
    max_input_chars: number;
    citations: boolean;
    advanced: {
      input_classifier: boolean;
      grounding_check: boolean;
      injection_sensitivity: "low" | "medium" | "high";
      custom_instructions: string;
      log_pii_redaction: boolean;
    };
  };
  memory: { max_turns: number; history_token_budget: number; session_timeout_minutes: number };
  annotations?: { enabled: boolean; threshold: number };
  meta?: Record<string, unknown>;
}

export interface AgentSummary {
  id: string;
  name: string;
  description: string | null;
  template_key: string | null;
  status: "draft" | "published" | "deployed";
  latest_version_number: number | null;
  tools: ToolName[];
  created_by: string | null;
  created_at: string;
  updated_at: string;
  draft_updated_at: string;
}

export interface AgentOut extends AgentSummary {
  draft_config: AgentConfig;
  etag: string;
  latest_version_id: string | null;
}

export interface TemplateOut {
  key: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  version: number;
  enabled_tools: Record<string, boolean>;
  starter_questions: string[];
  recommended_kb_docs: string[];
  recommended_dataset_schema: string[];
  system_prompt: string;
}

export interface VersionOut {
  id: string;
  agent_id: string;
  version_number: number;
  changelog: string | null;
  published_by: string | null;
  created_at: string;
  config?: AgentConfig | null;
}

export interface KBOut {
  id: string;
  name: string;
  description: string | null;
  embedding_model: string;
  embedding_dim: number;
  chunk_config: Record<string, unknown>;
  status: string;
  doc_count: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface DocumentOut {
  id: string;
  kb_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  version: number;
  status: "queued" | "parsing" | "chunking" | "embedding" | "ready" | "failed";
  error: string | null;
  page_count: number | null;
  chunk_count: number;
  sha256: string | null;
  created_at: string;
  ingested_at: string | null;
}

export interface DatasetOut {
  id: string;
  name: string;
  type: "postgres" | "spreadsheet";
  status: string;
  connection_config: Record<string, unknown>;
  password_configured: boolean;
  allow_sampling: boolean;
  current_schema_version: number;
  error: string | null;
  storage_key: string | null;
  created_at: string;
  updated_at: string;
  table_stats: { tables?: number; reviewed?: number; exposed?: number };
}

export interface DatasetColumnOut {
  id: string;
  column_name: string;
  data_type: string;
  nullable: boolean;
  is_pk: boolean;
  ordinal: number;
  description: string | null;
  description_source: string | null;
  semantic_type: string | null;
  sample_values: unknown[] | null;
  hidden: boolean;
  reviewed: boolean;
}

export interface DatasetTableOut {
  id: string;
  schema_name: string;
  table_name: string;
  kind: string;
  row_estimate: number | null;
  description: string | null;
  description_source: string | null;
  reviewed: boolean;
  exposed: boolean;
  hidden_columns: number;
  columns: DatasetColumnOut[];
}

export interface DatasetSchemaOut {
  dataset: DatasetOut;
  tables: DatasetTableOut[];
  relationships: {
    id: string;
    from_table_id: string;
    from_column: string;
    to_table_id: string;
    to_column: string;
    type: string;
    confidence: number | null;
  }[];
  glossary: { id: string; term: string; definition: string }[];
  example_queries: {
    id: string;
    question: string;
    sql: string;
    validated_at: string | null;
    last_error: string | null;
  }[];
}

export interface DeploymentOut {
  id: string;
  agent_id: string;
  agent_name: string | null;
  agent_version_id: string;
  version_number: number | null;
  channel: string;
  status: "pending_webhook" | "live" | "paused" | "error";
  phone_number_id: string | null;
  waba_id: string | null;
  display_phone: string | null;
  verified_name: string | null;
  quality_rating: string | null;
  graph_version: string | null;
  credentials_valid: boolean;
  token_last4: string | null;
  webhook_url: string;
  webhook_verified_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  rate_limits: Record<string, number>;
  message_count: number;
  created_at: string;
  verify_token?: string;
}

export interface ConversationOut {
  id: string;
  agent_id: string;
  agent_name: string | null;
  deployment_id: string | null;
  channel: "playground" | "whatsapp";
  contact: { id: string; wa_id: string; profile_name: string | null } | null;
  started_at: string;
  last_message_at: string;
  closed_at: string | null;
  message_count: number;
  flags: Record<string, unknown>;
  messages?: MessageOut[];
}

export interface MessageOut {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  status?: string;
  route?: { tools: ToolName[]; reason: string } | null;
  tool_summary?: string | null;
  citations?: Citation[] | null;
  guardrail_events?: Record<string, unknown>[] | null;
  latency_ms?: number | null;
  error?: string | null;
  run_id?: string | null;
  feedback?: { rating: number; comment: string | null } | null;
  created_at: string;
}

export interface Citation {
  n: number;
  chunk_id?: string;
  document_id?: string;
  filename?: string;
  page_start?: number | null;
  page_end?: number | null;
  section_title?: string | null;
  snippet?: string;
}

export interface CredentialOut {
  id: string;
  provider: string;
  label: string;
  last4: string;
  api_base: string | null;
  created_at: string;
  last_used_at: string | null;
}

export interface ModelCatalog {
  providers: {
    key: string;
    display: string;
    byo: boolean;
    models: {
      id: string;
      display: string;
      context: number;
      supports_tools: boolean;
      supports_json_schema: boolean;
      default: boolean;
    }[];
  }[];
  embedding_models: { id: string; display: string; dim: number; provider: string }[];
}

export interface AnalyticsOverview {
  from: string;
  to: string;
  totals: {
    messages_in: number;
    messages_out: number;
    conversations: number;
    unique_contacts: number;
    errors: number;
  };
  messages_per_day: { date: string; in: number; out: number }[];
  tool_split: Record<string, number>;
  latency_ms: { p50: number | null; p95: number | null; avg: number | null };
  sql: { runs: number; failed: number; success_rate: number | null };
  no_answer_rate: number | null;
  feedback: { positive: number; negative: number; ratio: number | null };
  usage_by_model: { model: string; input_tokens: number; output_tokens: number; est_cost_usd: number }[];
}
