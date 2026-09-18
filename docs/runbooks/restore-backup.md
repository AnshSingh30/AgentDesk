# Runbook: restore from backup

Backups: nightly `pg_dump` (gzip) to `backups/` and S3 `backups/` with 14-day retention (`infra/scripts/backup.sh`); Qdrant snapshots via API; MinIO bucket versioning.

1. Stop API/worker (`docker compose stop api worker webhook`).
2. `infra/scripts/restore.sh backups/agentdesk-<stamp>.sql.gz` (drops and recreates `public`).
3. `make migrate` to apply newer migrations.
4. Restore Qdrant from the matching snapshot (`POST /collections/{name}/snapshots/recover`) or re-index knowledge bases.
5. Start services; run `make smoke`.
Tested: 2026-09-16 on the demo stack (restore + smoke passed).
