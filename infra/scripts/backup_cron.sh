#!/bin/sh
# Container entrypoint for the compose `backup` service: nightly at 02:30 UTC.
apt-get update >/dev/null && apt-get install -y --no-install-recommends curl gzip >/dev/null 2>&1 || true
while true; do
  now=$(date -u +%H%M)
  if [ "$now" = "0230" ]; then
    DATABASE_URL="postgresql://agentdesk:${PGPASSWORD}@postgres:5432/agentdesk" BACKUP_DIR=/backups sh /scripts/backup.sh || echo "backup failed"
    sleep 60
  fi
  sleep 30
done
