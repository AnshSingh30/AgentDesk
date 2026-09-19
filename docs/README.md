# AgentDesk documentation

## Using AgentDesk
- [Configuration](configuration.md): environment variables, models, embeddings, security settings
- [Service API](service-api.md): call a published agent from your own backend
- [Annotation replies](annotations.md): curated answers returned without a model call
- Help: [WhatsApp setup](help/whatsapp-setup.md) · [read-only database user](help/read-only-db-user.md) · [schema review](help/schema-review.md)

## How it works
- [Architecture](architecture.md): services, request flows, agent runtime, RAG and text-to-SQL pipelines
- [Security model](security.md)
- [Evaluation](evaluation.md): eval datasets, metrics and how to run them
- Architecture decision records: [LangGraph runtime](adr/0001-langgraph-runtime.md) · [Qdrant multitenancy](adr/0002-qdrant-multitenancy.md) · [DuckDB for spreadsheets](adr/0003-duckdb-spreadsheets.md) · [SQL validator](adr/0004-sql-validator.md)

## Operating it
- Runbooks: [WhatsApp token expired](runbooks/whatsapp-token-expired.md) · [ingestion stuck](runbooks/ingestion-stuck.md) · [restore a backup](runbooks/restore-backup.md) · [rotate the encryption key](runbooks/rotate-encryption-key.md)
