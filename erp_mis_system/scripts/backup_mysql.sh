#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

DB_NAME="${MYSQL_DATABASE:-erp_mis_system}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/erp_mis_system}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
MYSQL_CNF="${MYSQL_CNF:-/etc/erp-mis/mysql-backup.cnf}"

if [[ ! "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "MYSQL_DATABASE must contain only letters, numbers, and underscores." >&2
  exit 2
fi
if [[ ! "$RETENTION_DAYS" =~ ^[1-9][0-9]*$ ]]; then
  echo "RETENTION_DAYS must be a positive integer." >&2
  exit 2
fi
if [[ ! -r "$MYSQL_CNF" ]]; then
  echo "MySQL client credentials file is not readable: $MYSQL_CNF" >&2
  exit 2
fi

mkdir -p "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$BACKUP_DIR/${DB_NAME}_${timestamp}.sql.gz"
temporary_file="${backup_file}.partial"
trap 'rm -f -- "$temporary_file"' EXIT

mysqldump \
  --defaults-extra-file="$MYSQL_CNF" \
  --single-transaction \
  --quick \
  --routines \
  --triggers \
  --events \
  --hex-blob \
  --no-tablespaces \
  --default-character-set=utf8mb4 \
  "$DB_NAME" | gzip -c > "$temporary_file"

gzip -t "$temporary_file"
mv -- "$temporary_file" "$backup_file"
find "$BACKUP_DIR" -maxdepth 1 -type f \
  -name "${DB_NAME}_*.sql.gz" \
  -mtime "+$RETENTION_DAYS" \
  -delete
trap - EXIT
echo "Backup written: $backup_file"
