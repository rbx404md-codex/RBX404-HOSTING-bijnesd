#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

STAMP=$(date -u +%Y%m%d_%H%M%S)
OUT="./backups/rbx404_${STAMP}.sqlite3"

# use sqlite3 .backup (WAL-safe) inside the container
docker compose exec -T bot sqlite3 /app/data/rbx404.sqlite3 ".backup /tmp/bk.sqlite3"
docker compose cp bot:/tmp/bk.sqlite3 "$OUT"
docker compose exec -T bot rm -f /tmp/bk.sqlite3 || true

# rotate: keep 14
ls -1t ./backups/rbx404_*.sqlite3 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "backup → $OUT"