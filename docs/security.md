# Security model

AgentDesk is multi-tenant and connects to customers' documents, databases and messaging accounts, so it
is built to be safe by construction on the platform side. Report vulnerabilities as described in
[SECURITY.md](../SECURITY.md).

## Tenant isolation

- Every tenant table carries `org_id`. Queries go through an org-scoped repository (`app/db/repo.py`),
  and the membership dependency resolves the organisation from the URL and checks the caller's role.
- Cross-tenant requests get **404**, not 403, so resource ids can't be enumerated. An integration test
  calls every org-scoped endpoint as a member of another organisation.
- Qdrant searches always filter on `org_id` and the agent's knowledge base ids. Object storage keys are
  prefixed `orgs/{org_id}/`, and each spreadsheet dataset gets its own DuckDB file.

## Authentication and sessions

- Argon2id password hashes, login lockout after repeated failures, and single-use expiring email
  tokens.
- Short-lived access JWT plus a rotating refresh token in `HttpOnly` cookies. Reusing an old refresh
  token revokes the whole token family.
- CSRF double-submit token for cookie-authenticated writes.
- Roles: Owner, Admin, Editor, Viewer.
- Service API keys are random 256-bit tokens stored as SHA-256 hashes, scoped to one agent,
  rate-limited and revocable.

## Customer secrets

Database passwords, provider API keys and WhatsApp tokens are envelope-encrypted (AES-256-GCM data key
wrapped by a rotating master key). They are never returned by the API (only `last4`) and are redacted
from logs.

## Text-to-SQL safety

Generated SQL runs only after the validator (`app/sql/validator.py`) accepts it. The validator:

- parses the query with sqlglot and allows a single `SELECT` / `WITH … SELECT`
- rejects DML/DDL, `SELECT … INTO`, locking clauses and dangerous functions (`pg_sleep`, `dblink`,
  file readers, DuckDB `read_csv`, …)
- enforces the table allowlist and hidden columns, including when `*` is expanded
- injects a `LIMIT`

Queries then run in a read-only transaction with a statement timeout. 100 adversarial test
cases cover the validator.

## Network

User-supplied URLs (custom model endpoints, database hosts) are resolved and checked against private,
loopback, link-local and metadata ranges at connect time. `ALLOW_PRIVATE_ENDPOINTS` turns this off for
self-hosted setups. WhatsApp webhooks are verified with `X-Hub-Signature-256` (constant-time compare)
and deduplicated by message id.

## LLM-specific

- Retrieved documents, SQL results and web pages are treated as untrusted data and wrapped in delimiters
  below the platform instructions.
- Input guardrails flag injection patterns, and an optional classifier can block them.
- Output guardrails scrub secret patterns and system-prompt leakage.
- Each run has a token ceiling and a timeout.

## Audit log

Security-relevant actions are appended to the organisation's audit log: logins, role changes, key and
credential changes, dataset connection changes, publishes, deployments and API key creation or
revocation.
