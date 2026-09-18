#!/usr/bin/env bash
# Restore the app DB from a dump produced by backup.sh: infra/scripts/restore.sh backups/agentdesk-<stamp>.sql.gz
set -euo pipefail
FILE="${1:?usage: restore.sh <dump.sql.gz>}"
: "${DATABASE_URL:=postgresql://agentdesk:agentdesk@localhost:5433/agentdesk}"
PG_URL="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
echo "Restoring $FILE into $PG_URL (existing objects are dropped)"; read -r -p "Continue? [y/N] " a; [ "$a" = "y" ] || exit 1
psql "$PG_URL" -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
gunzip -c "$FILE" | psql "$PG_URL" -v ON_ERROR_STOP=1 -q
echo "restored; run 'make migrate' to apply any newer migrations"
