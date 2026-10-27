#!/usr/bin/env bash
# Download a consistent snapshot of the server database for DBeaver / sqlite3 (read-only copy).
#   ./pull-db.sh                 -> data/jobwatch_snapshot.db
# The live file is in WAL mode, so a plain scp can miss recent writes: SQLite's backup API makes the copy.
set -euo pipefail
SERVER="${JOBWATCH_SERVER:-yeakbay@57.129.40.172}"
OUT="${1:-$(dirname "$0")/data/jobwatch_snapshot.db}"

ssh -t "$SERVER" 'cd ~/jobwatch && sudo docker compose exec -T collector python -c "
import sqlite3
sqlite3.connect(\"/data/jobwatch.db\").backup(sqlite3.connect(\"/data/snapshot.db\"))"'
mkdir -p "$(dirname "$OUT")"
scp -q "$SERVER:~/jobwatch/data/snapshot.db" "$OUT"
echo "snapshot: $OUT"
