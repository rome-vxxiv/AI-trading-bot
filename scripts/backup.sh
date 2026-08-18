#!/usr/bin/env bash
# Nightly SQLite dump. Run from cron on your VPS:
#   15 3 * * * /opt/capital-agent/scripts/backup.sh
# or inside the docker deploy:
#   docker exec capital-agent /app/scripts/backup.sh
set -euo pipefail

STATE_DB="${CAPITAL_AGENT_STATE_DIR:-./state}/state.db"
BACKUP_DIR="${CAPITAL_AGENT_BACKUP_DIR:-/var/backups/capital-agent}"
RETENTION_DAYS="${CAPITAL_AGENT_BACKUP_RETENTION_DAYS:-30}"

if [[ ! -f "$STATE_DB" ]]; then
    echo "state.db not found at $STATE_DB; nothing to back up" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y-%m-%d)
OUT="$BACKUP_DIR/state-$STAMP.db.gz"

# Use SQLite's own online backup so we don't race the scheduler.
sqlite3 "$STATE_DB" ".backup '/tmp/state-$STAMP.db'"
gzip -9 -c "/tmp/state-$STAMP.db" > "$OUT"
rm -f "/tmp/state-$STAMP.db"

# Prune anything older than retention.
find "$BACKUP_DIR" -name 'state-*.db.gz' -mtime "+$RETENTION_DAYS" -delete

echo "wrote $OUT ($(stat -c %s "$OUT") bytes)"
