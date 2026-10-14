#!/usr/bin/env bash
# Restore a backup into an EMPTY database. Never overwrites a database that already has tables.
# Usage: DATABASE_URL=postgresql://.../target_db ./scripts/restore.sh backups/bre-XXXX.sql.gz
set -euo pipefail
: "${DATABASE_URL:?set DATABASE_URL}"
FILE="${1:?usage: restore.sh <backup.sql.gz>}"
gzip -t "$FILE"
N=$(psql "$DATABASE_URL" -Atc "select count(*) from information_schema.tables where table_schema='public'")
if [ "$N" != "0" ]; then echo "refusing: target database already has $N tables. Create a fresh database first." >&2; exit 1; fi
gunzip -c "$FILE" | psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q
echo "restored $FILE; run 'alembic upgrade head' if the backup is older than this release"
