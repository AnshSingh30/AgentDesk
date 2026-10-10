# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Service API**: per-agent API keys (hashed, revocable, audited) and `POST /v1/service/chat` with
  blocking JSON or SSE streaming and conversations scoped to the caller's end-user id.
- **Annotation replies**: curated Q&A answered verbatim above a similarity threshold, without a model
  call. Includes an Annotations page, "Save as annotation" in the Playground, hit counts and a builder
  toggle.
- OpenAI-compatible platform endpoint (`DEFAULT_LLM_API_BASE`) and self-hosted embeddings
  (`EMBEDDING_API_BASE`, text-embeddings-inference service in Docker Compose).
- Evaluation suite: 98 cases across the three templates with SQL ground truth, LLM-judged groundedness
  and declines, hidden-column privacy checks, JSON reports and a generated summary.
- Open-source project files: contributing guide, code of conduct, security policy, issue templates,
  Dependabot, `.editorconfig` / `.gitattributes`, and public documentation.

### Changed
- License is now the Business Source License 1.1 (non-production use; converts to Apache 2.0).
- The Makefile uses `uv run`, so it works on Linux, macOS and Windows (Git Bash).
- CI lint and type checks now fail the build. Release and eval workflows run on demand only.
- Demo PDFs are generated reproducibly.

### Fixed
- Provider failures that returned an empty completion were shown to users as "not in the documents".
  They are now reported as provider errors with a retry message.
- JSON-mode calls against servers that ignore `response_format` or truncate output.
- `run_once` returned only the final SSE payload, hiding retrieval, SQL and usage details from callers.
- The seeded "average tenure" example query failed on PostgreSQL.

## [0.1.0] - 2026-09-16

Initial release: builder, templates, knowledge base (RAG), datasets (text-to-SQL), web search, routing,
guardrails and memory, Playground, publish and versions, WhatsApp deployment, conversations, analytics,
settings and the operator console.
