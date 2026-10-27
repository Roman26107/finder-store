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
        log INFO "FINDER full backup completed successfully in ${elapsed}s"
        if is_true "${BACKUP_NOTIFY_SUCCESS:-true}"; then
            notify_telegram "✅ FINDER full backup completed on ${BACKUP_HOSTNAME} in ${elapsed}s (${STAMP})."
        fi
    else
        log ERROR "FINDER full backup failed with exit code $exit_code after ${elapsed}s"
        notify_telegram "❌ FINDER full backup FAILED on ${BACKUP_HOSTNAME:-unknown} after ${elapsed}s (${STAMP}). Check: journalctl -u finder-backup.service"
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
[[ -d "$PROJECT_DIR/media" ]] || die "Media directory is missing: $PROJECT_DIR/media"

rm -rf -- "$BACKUP_STAGING_DIR"
mkdir -p "$BACKUP_STAGING_DIR"
STAGING_CREATED=true
DUMP_PATH="$BACKUP_STAGING_DIR/postgres.dump"
MANIFEST_PATH="$BACKUP_STAGING_DIR/manifest.txt"

log INFO "Creating PostgreSQL custom-format dump from service '$POSTGRES_SERVICE'"
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

RESTIC_VERSION="$(restic version | head -n 1)"
DOCKER_COMPOSE_VERSION="$(docker compose version --short 2>/dev/null || printf unknown)"
POSTGRES_VERSION="$(compose exec -T "$POSTGRES_SERVICE" postgres --version 2>/dev/null || printf unknown)"

{
    printf 'application=FINDER\n'
    printf 'backup_type=full\n'
    printf 'created_at_utc=%s\n' "$STAMP"
    printf 'backup_host=%s\n' "$BACKUP_HOSTNAME"
    printf 'project_dir=%s\n' "$PROJECT_DIR"
    printf 'postgres_service=%s\n' "$POSTGRES_SERVICE"
    printf 'dump_sha256=%s\n' "$(sha256sum "$DUMP_PATH" | awk '{print $1}')"
    printf 'dump_bytes=%s\n' "$(stat -c '%s' "$DUMP_PATH")"
    printf 'restic_version=%s\n' "$RESTIC_VERSION"
    printf 'docker_compose_version=%s\n' "$DOCKER_COMPOSE_VERSION"
    printf 'postgres_version=%s\n' "$POSTGRES_VERSION"
} > "$MANIFEST_PATH"
chmod 0600 "$MANIFEST_PATH"

backup_paths=(
    media
    .env.production
    docker-compose.yml
    deploy/nginx
    deploy/backup
    "$BACKUP_STAGING_RELATIVE/postgres.dump"
    "$BACKUP_STAGING_RELATIVE/manifest.txt"
)

log INFO "Uploading encrypted snapshot to Restic repository"
(
    cd "$PROJECT_DIR"
    restic_run backup \
        --host "$BACKUP_HOSTNAME" \
        --tag finder \
        --tag "$FULL_BACKUP_TAG" \
        --tag production \
        --exclude-caches \
        "${backup_paths[@]}"
)

log INFO "Applying full-backup retention: $FULL_BACKUP_KEEP_WITHIN"
forget_full_snapshots

log INFO "Confirming that the new snapshot is visible"
restic_run snapshots --host "$BACKUP_HOSTNAME" --tag "$FULL_BACKUP_TAG" --compact

BACKUP_SUCCEEDED=true