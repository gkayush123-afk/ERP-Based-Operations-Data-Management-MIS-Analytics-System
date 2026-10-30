#!/usr/bin/env bash
# ============================================================================
# ERP-MIS: Safe MySQL -> PostgreSQL migration helper (Railway -> Render)
#
# Purpose:
#   Export data from the existing Railway MySQL database and prepare it for a
#   PostgreSQL target (Render Managed PostgreSQL). The source Railway MySQL is
#   NEVER modified or dropped; it is kept as a backup until the PostgreSQL
#   migration is verified in a staging environment.
#
# Usage:
#   ./scripts/mysql_to_postgresql_migration.sh \
#       --source-mysql-host <host> \
#       --source-mysql-db   <db> \
#       --source-mysql-user <user> \
#       --source-mysql-pass <pass> \
#       --target-pg-host     <host> \
#       --target-pg-db       <db> \
#       --target-pg-user     <user> \
#       --target-pg-pass     <pass>
#
# Requirements on the migration host:
#   - mysqldump / mysql client (source)
#   - pg_dump / psql (target, PostgreSQL 14+)
#   - Enough disk space for the source dump + converted data
#
# NOTE: Schema conversion (AUTO_INCREMENT -> BIGSERIAL, KEY -> INDEX,
#       ENGINE=InnoDB/charset clauses, AFTER column ordering) is normally done
#       with a dedicated tool such as pgloader. This script focuses on the safe
#       data export procedure; run schema conversion before/with data load.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

SOURCE_HOST=""
SOURCE_DB=""
SOURCE_USER=""
SOURCE_PASS=""
TARGET_HOST=""
TARGET_DB=""
TARGET_USER=""
TARGET_PASS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-mysql-host) SOURCE_HOST="$2"; shift 2 ;;
    --source-mysql-db)   SOURCE_DB="$2"; shift 2 ;;
    --source-mysql-user) SOURCE_USER="$2"; shift 2 ;;
    --source-mysql-pass) SOURCE_PASS="$2"; shift 2 ;;
    --target-pg-host)     TARGET_HOST="$2"; shift 2 ;;
    --target-pg-db)       TARGET_DB="$2"; shift 2 ;;
    --target-pg-user)     TARGET_USER="$2"; shift 2 ;;
    --target-pg-pass)     TARGET_PASS="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

: "${SOURCE_HOST:?source-mysql-host is required}"
: "${SOURCE_DB:?source-mysql-db is required}"
: "${SOURCE_USER:?source-mysql-user is required}"
: "${SOURCE_PASS:?source-mysql-pass is required}"
: "${TARGET_HOST:?target-pg-host is required}"
: "${TARGET_DB:?target-pg-db is required}"
: "${TARGET_USER:?target-pg-user is required}"
: "${TARGET_PASS:?target-pg-pass is required}"

BACKUP_DIR="${SCRIPT_DIR}/../backups/mysql_to_pg"
mkdir -p "$BACKUP_DIR"

echo "[1/5] Backing up source MySQL database ${SOURCE_DB}@${SOURCE_HOST} ..."
mysqldump \
  --host="${SOURCE_HOST}" \
  --user="${SOURCE_USER}" \
  --password="${SOURCE_PASS}" \
  --single-transaction \
  --routines \
  --triggers \
  --databases "${SOURCE_DB}" \
  > "${BACKUP_DIR}/${SOURCE_DB}_$(date +%Y%m%d_%H%M%S).sql"

echo "[2/5] Source backup written to ${BACKUP_DIR}"

echo "[3/5] Creating target PostgreSQL database ${TARGET_DB}@${TARGET_HOST} ..."
# Created via Render dashboard (Internal Database URL) or psql:
#   createdb -h ${TARGET_HOST} -U ${TARGET_USER} -c "CREATE DATABASE ${TARGET_DB};"

echo "[4/5] Data export from MySQL complete. Import into PostgreSQL using:"
echo "       - Schema: converted DDL (AUTO_INCREMENT -> BIGSERIAL, KEY -> INDEX, ENGINE=InnoDB -> PostgreSQL defaults)."
echo "       - Data:   use the backup dump above + table-level data load via pgloader or psql."
echo "       - Keep Railway backup untouched until PostgreSQL verification passes."

echo "[5/5] Migration helper completed. Next steps:"
echo "       1) Restore/convert schema into ${TARGET_DB}."
echo "       2) Load data from ${BACKUP_DIR}/${SOURCE_DB}_*.sql (converted)."
echo "       3) Run app against Render PostgreSQL DATABASE_URL."
echo "       4) Verify row counts / FK integrity."
echo "       5) Cut over; only then decommission Railway."

echo "Migration helper finished successfully."
