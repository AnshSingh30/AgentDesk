#!/usr/bin/env bash
# Nightly app-DB dump to S3/MinIO with 14-day retention (PRD §19.2). Requires pg_dump + aws/mc CLI or curl.
set -euo pipefail
: "${DATABASE_URL:=postgresql://agentdesk:agentdesk@localhost:5433/agentdesk}"
: "${BACKUP_DIR:=./backups}"
: "${RETENTION_DAYS:=14}"
mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
FILE="$BACKUP_DIR/agentdesk-$STAMP.sql.gz"
PG_URL="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
pg_dump --no-owner --no-privileges "$PG_URL" | gzip -9 > "$FILE"
echo "wrote $FILE ($(du -h "$FILE" | cut -f1))"
if [ -n "${S3_BUCKET:-}" ] && command -v aws >/dev/null; then
  aws --endpoint-url "${S3_ENDPOINT:-}" s3 cp "$FILE" "s3://$S3_BUCKET/backups/$(basename "$FILE")"
  echo "uploaded to s3://$S3_BUCKET/backups/"
fi
find "$BACKUP_DIR" -name 'agentdesk-*.sql.gz' -mtime +"$RETENTION_DAYS" -delete
# Qdrant snapshot (best effort)
if [ -n "${QDRANT_URL:-}" ]; then
  curl -fsS -X POST "$QDRANT_URL/snapshots" -H "api-key: ${QDRANT_API_KEY:-}" >/dev/null && echo "qdrant snapshot created" || echo "qdrant snapshot skipped"
fi
