#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

STARTED_AT="$(date +%s)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_SUCCEEDED=false
STAGING_CREATED=false

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM

    if [[ "$STAGING_CREATED" == true && -n "${BACKUP_STAGING_DIR:-}" ]]; then
        rm -rf -- "$BACKUP_STAGING_DIR"
    fi

    local elapsed=$(( $(date +%s) - STARTED_AT ))
    if [[ "$BACKUP_SUCCEEDED" == true && "$exit_code" -eq 0 ]]; then
        log INFO "FINDER database backup completed successfully in ${elapsed}s"
        if is_true "${DATABASE_BACKUP_NOTIFY_SUCCESS:-false}"; then
            notify_telegram "✅ FINDER database backup completed on ${BACKUP_HOSTNAME} in ${elapsed}s (${STAMP})."
        fi
    else
        log ERROR "FINDER database backup failed with exit code $exit_code after ${elapsed}s"
        notify_telegram "❌ FINDER database backup FAILED on ${BACKUP_HOSTNAME:-unknown} after ${elapsed}s (${STAMP}). Check: journalctl -u finder-database-backup.service"
    fi
    exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

load_backup_config
require_command docker
require_command restic
require_command sha256sum
acquire_backup_lock

[[ -f "$PROJECT_DIR/.env.production" ]] || die "Production environment file is missing: $PROJECT_DIR/.env.production"

rm -rf -- "$BACKUP_STAGING_DIR"
mkdir -p "$BACKUP_STAGING_DIR"
STAGING_CREATED=true
DUMP_PATH="$BACKUP_STAGING_DIR/postgres.dump"
MANIFEST_PATH="$BACKUP_STAGING_DIR/manifest.txt"

log INFO "Creating frequent PostgreSQL custom-format dump from service '$POSTGRES_SERVICE'"
compose exec -T "$POSTGRES_SERVICE" sh -ec \
    'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump \
        --username="$POSTGRES_USER" \
        --dbname="$POSTGRES_DB" \
        --format=custom \
        --compress=6 \
        --no-owner \
        --no-acl' \
    > "$DUMP_PATH"

[[ -s "$DUMP_PATH" ]] || die "PostgreSQL dump is empty"
chmod 0600 "$DUMP_PATH"

log INFO "Validating PostgreSQL dump with pg_restore --list"
compose exec -T "$POSTGRES_SERVICE" pg_restore --list < "$DUMP_PATH" >/dev/null

{
    printf 'application=FINDER\n'
    printf 'backup_type=database\n'
    printf 'created_at_utc=%s\n' "$STAMP"
    printf 'backup_host=%s\n' "$BACKUP_HOSTNAME"
    printf 'postgres_service=%s\n' "$POSTGRES_SERVICE"
    printf 'dump_sha256=%s\n' "$(sha256sum "$DUMP_PATH" | awk '{print $1}')"
    printf 'dump_bytes=%s\n' "$(stat -c '%s' "$DUMP_PATH")"
} > "$MANIFEST_PATH"
chmod 0600 "$MANIFEST_PATH"

log INFO "Uploading encrypted database-only snapshot to Restic repository"
(
    cd "$PROJECT_DIR"
    restic_run backup \
        --host "$BACKUP_HOSTNAME" \
        --tag finder \
        --tag "$DATABASE_BACKUP_TAG" \
        --tag production \
        "$BACKUP_STAGING_RELATIVE/postgres.dump" \
        "$BACKUP_STAGING_RELATIVE/manifest.txt"
)

log INFO "Applying database-backup retention: $DATABASE_BACKUP_KEEP_WITHIN"
forget_database_snapshots

log INFO "Confirming that the new database snapshot is visible"
restic_run snapshots --host "$BACKUP_HOSTNAME" --tag "$DATABASE_BACKUP_TAG" --compact

BACKUP_SUCCEEDED=true