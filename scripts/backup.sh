#!/usr/bin/env bash
# Database backup for Carmanac (F9).
#
# What one run does, in order:
#   1. pg_dump the whole database (custom format, compressed) from the
#      running Docker container into BACKUP_DIR, stamped with the UTC time
#      and the Alembic revision it was taken at.
#   2. Verify the dump is readable (pg_restore --list). A file pg_restore
#      cannot read is not a backup.
#   3. Rotate local dumps: keep the newest LOCAL_KEEP, delete older ones.
#   4. Upload the new dump to RCLONE_REMOTE (Google Drive) and confirm the
#      uploaded size matches. Prune remote copies older than REMOTE_KEEP_DAYS.
#
# Flags:
#   --no-upload        skip step 4 (e.g. rclone not configured yet)
#   --verify-restore   additionally restore the dump into a scratch database
#                      and count tables — the only real proof a backup works.
#                      Slower; run it occasionally, not necessarily every time.
#
# Everything is overridable via environment variables (defaults below), per
# the charter rule that operational details never get hard-coded.

set -euo pipefail

CONTAINER="${CARMANAC_DB_CONTAINER:-carmanac_postgres}"
DB_USER="${CARMANAC_DB_USER:-carmanac}"
DB_NAME="${CARMANAC_DB_NAME:-carmanac}"
BACKUP_DIR="${CARMANAC_BACKUP_DIR:-$HOME/backups/carmanac}"
RCLONE_REMOTE="${CARMANAC_BACKUP_REMOTE:-gdrive:Carmanac/backups}"
LOCAL_KEEP="${CARMANAC_BACKUP_LOCAL_KEEP:-7}"
REMOTE_KEEP_DAYS="${CARMANAC_BACKUP_REMOTE_KEEP_DAYS:-90}"

UPLOAD=1
VERIFY_RESTORE=0
for arg in "$@"; do
  case "$arg" in
    --no-upload) UPLOAD=0 ;;
    --verify-restore) VERIFY_RESTORE=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

fail() { echo "BACKUP FAILED: $*" >&2; exit 1; }

# --- preconditions -----------------------------------------------------------

docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null \
  | grep -q true || fail "container '$CONTAINER' is not running (cd infra && docker compose up -d)"

mkdir -p "$BACKUP_DIR"

# --- 1. dump -----------------------------------------------------------------

ALEMBIC_REV=$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  'SELECT version_num FROM alembic_version' 2>/dev/null || echo "norev")
STAMP=$(date -u +%Y%m%d_%H%M%SZ)
DUMP="$BACKUP_DIR/carmanac_${STAMP}_${ALEMBIC_REV}.dump"

echo "1/4 dumping ${DB_NAME} (alembic ${ALEMBIC_REV}) -> ${DUMP}"
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" \
  --format=custom --compress=zstd > "$DUMP" \
  || { rm -f "$DUMP"; fail "pg_dump exited nonzero"; }
echo "    $(du -h "$DUMP" | cut -f1) written"

# --- 2. verify readability ---------------------------------------------------

# pg_restore --list parses the dump's table of contents; it catches
# truncation and corruption without touching any database.
ITEMS=$(docker exec -i "$CONTAINER" pg_restore --list < "$DUMP" | grep -c '^[0-9]') \
  || { rm -f "$DUMP"; fail "dump is unreadable by pg_restore"; }
echo "2/4 dump verified readable (${ITEMS} archive items)"

if [ "$VERIFY_RESTORE" = 1 ]; then
  SCRATCH="carmanac_restore_test"
  echo "    restoring into scratch db '${SCRATCH}' for full verification..."
  docker exec "$CONTAINER" psql -U "$DB_USER" -d postgres -qc \
    "DROP DATABASE IF EXISTS ${SCRATCH}" >/dev/null
  docker exec "$CONTAINER" psql -U "$DB_USER" -d postgres -qc \
    "CREATE DATABASE ${SCRATCH}" >/dev/null
  # --no-owner: the scratch restore doesn't need to reproduce ownership.
  docker exec -i "$CONTAINER" pg_restore -U "$DB_USER" -d "$SCRATCH" \
    --no-owner < "$DUMP" || fail "restore into scratch database failed"
  TABLES=$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$SCRATCH" -tAc \
    "SELECT count(*) FROM information_schema.tables
     WHERE table_schema IN ('public','raw_scrape')")
  RAW=$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$SCRATCH" -tAc \
    "SELECT count(*) FROM raw_scrape.raw_records")
  docker exec "$CONTAINER" psql -U "$DB_USER" -d postgres -qc \
    "DROP DATABASE ${SCRATCH}" >/dev/null
  [ "$TABLES" -gt 0 ] || fail "scratch restore produced no tables"
  echo "    full restore verified: ${TABLES} tables, ${RAW} raw records"
fi

# --- 3. rotate local ---------------------------------------------------------

# Newest LOCAL_KEEP stay; names sort chronologically because of the UTC stamp.
DELETED=0
while IFS= read -r old; do
  rm -f "$old"; DELETED=$((DELETED + 1))
done < <(ls -1 "$BACKUP_DIR"/carmanac_*.dump 2>/dev/null | sort | head -n -"$LOCAL_KEEP")
echo "3/4 local rotation: kept newest ${LOCAL_KEEP}, deleted ${DELETED}"

# --- 4. upload off-machine ---------------------------------------------------

if [ "$UPLOAD" = 0 ]; then
  echo "4/4 upload SKIPPED (--no-upload). This run does NOT satisfy off-machine backup."
  exit 0
fi

command -v rclone >/dev/null || fail "rclone not installed"
echo "4/4 uploading to ${RCLONE_REMOTE}"
rclone copy "$DUMP" "$RCLONE_REMOTE" || fail "rclone upload failed"

LOCAL_SIZE=$(stat -c %s "$DUMP")
REMOTE_SIZE=$(rclone lsjson "$RCLONE_REMOTE/$(basename "$DUMP")" 2>/dev/null \
  | grep -o '"Size":[0-9]*' | head -1 | cut -d: -f2)
[ "$LOCAL_SIZE" = "${REMOTE_SIZE:-}" ] \
  || fail "uploaded size mismatch (local ${LOCAL_SIZE}, remote ${REMOTE_SIZE:-missing})"

# Age-based prune, deliberately longer than local retention: the remote is
# the archive, the local dir is just a working buffer.
rclone delete --min-age "${REMOTE_KEEP_DAYS}d" "$RCLONE_REMOTE" 2>/dev/null || true

echo "backup complete: $(basename "$DUMP") is local and on ${RCLONE_REMOTE%%:*}"
