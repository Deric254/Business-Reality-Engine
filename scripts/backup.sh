#!/usr/bin/env bash
# Logical backup of the database to a compressed, timestamped file; keeps the newest $KEEP (default 14).
# Usage: DATABASE_URL=postgresql://... BACKUP_DIR=/var/backups/bre ./scripts/backup.sh
# Also back up CONNECTOR_FILE_ROOT (uploaded/shared files) and your .env (secrets!) separately.
set -euo pipefail
: "${DATABASE_URL:?set DATABASE_URL}"
DIR="${BACKUP_DIR:-./backups}"; KEEP="${KEEP:-14}"
mkdir -p "$DIR"
OUT="$DIR/bre-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
pg_dump --no-owner --no-privileges "$DATABASE_URL" | gzip -9 > "$OUT.partial"
gzip -t "$OUT.partial"                 # refuse to keep a truncated file
mv "$OUT.partial" "$OUT"
ls -1t "$DIR"/bre-*.sql.gz | tail -n +"$((KEEP+1))" | xargs -r rm --
echo "backup written: $OUT ($(du -h "$OUT" | cut -f1))"
