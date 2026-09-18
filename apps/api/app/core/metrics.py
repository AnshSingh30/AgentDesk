"""Prometheus metrics (PRD §19.1)."""

from prometheus_client import Counter, Gauge, Histogram

http_requests_total = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
http_request_duration = Histogram(
    "http_request_duration_seconds", "HTTP request duration", ["method", "route"]
)
agent_runs_total = Counter("agent_runs_total", "Agent runs", ["route", "channel", "status"])
agent_run_duration = Histogram(
    "agent_run_duration_seconds",
    "Agent run duration",
    ["channel"],
    buckets=(0.5, 1, 2, 3, 5, 8, 15, 30, 60, 90),
)
llm_tokens_total = Counter("llm_tokens_total", "LLM tokens", ["model", "direction"])
sql_attempts_total = Counter("sql_attempts_total", "SQL agent attempts", ["result"])
ingestion_jobs_total = Counter("ingestion_jobs_total", "Ingestion jobs", ["status"])
wa_webhook_total = Counter("wa_webhook_total", "WhatsApp webhook requests", ["result"])
wa_send_failures_total = Counter("wa_send_failures_total", "WhatsApp send failures", ["reason"])
queue_depth = Gauge("queue_depth", "ARQ queue depth")
