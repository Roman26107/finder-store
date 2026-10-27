#!/usr/bin/env bash

# Shared helpers. This file is sourced by the backup scripts.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'This file must be sourced, not executed.\n' >&2
    exit 2
fi

BACKUP_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_PROJECT_DIR="$(cd -- "$BACKUP_SCRIPT_DIR/../.." && pwd -P)"
BACKUP_ENV_FILE="${FINDER_BACKUP_ENV:-/etc/finder/backup.env}"

log() {
    printf '%s [%s] %s\n' "$(date --iso-8601=seconds)" "${1:-INFO}" "${*:2}"
}

die() {
    log ERROR "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command is not installed: $1"
}

is_true() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

assert_secret_file() {
    local path="$1"
    local label="$2"

    [[ -f "$path" && -r "$path" ]] || die "$label is not a readable regular file: $path"
    [[ -s "$path" ]] || die "$label is empty: $path"

    if [[ "$(id -u)" -eq 0 ]] && ! is_true "${BACKUP_ALLOW_INSECURE_PERMISSIONS:-false}"; then
        local owner mode
        owner="$(stat -c '%u' "$path")"
        mode="$(stat -c '%a' "$path")"
        [[ "$owner" == "0" ]] || die "$label must be owned by root: $path"
        case "$mode" in
            400|600) ;;
            *) die "$label must have mode 0600 or 0400, found $mode: $path" ;;
        esac
    fi
}

validate_positive_integer() {
    local name="$1"
    local value="$2"
    [[ "$value" =~ ^[0-9]+$ ]] || die "$name must be a non-negative integer, found: $value"
}

validate_restic_duration() {
    local name="$1"
    local value="$2"
    [[ "$value" =~ ^[1-9][0-9]*(h|d|w|m|y)$ ]] || \
        die "$name must be a positive Restic duration such as 48h or 3d, found: $value"
}

load_backup_config() {
    assert_secret_file "$BACKUP_ENV_FILE" "Backup environment file"

    set -a
    # shellcheck disable=SC1090
    source "$BACKUP_ENV_FILE"
    set +a

    PROJECT_DIR="$(readlink -m -- "${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}")"
    COMPOSE_FILE="$(readlink -m -- "${COMPOSE_FILE:-$PROJECT_DIR/docker-compose.yml}")"
    POSTGRES_SERVICE="${POSTGRES_SERVICE:-db}"
    RESTIC_PASSWORD_FILE="$(readlink -m -- "${RESTIC_PASSWORD_FILE:-/etc/finder/restic-password}")"
    RESTIC_CACHE_DIR="$(readlink -m -- "${RESTIC_CACHE_DIR:-/var/cache/finder-restic}")"
    BACKUP_STATE_DIR="$(readlink -m -- "${BACKUP_STATE_DIR:-$PROJECT_DIR/backups}")"
    BACKUP_STAGING_DIR="$(readlink -m -- "${BACKUP_STAGING_DIR:-$BACKUP_STATE_DIR/staging}")"
    BACKUP_LOCK_FILE="$(readlink -m -- "${BACKUP_LOCK_FILE:-/run/lock/finder-backup.lock}")"
    BACKUP_HOSTNAME="${BACKUP_HOSTNAME:-$(hostname -f 2>/dev/null || hostname)}"
    AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-ru-1}"
    RESTIC_S3_BUCKET_LOOKUP="${RESTIC_S3_BUCKET_LOOKUP:-path}"
    FULL_BACKUP_TAG="${FULL_BACKUP_TAG:-finder-full}"
    DATABASE_BACKUP_TAG="${DATABASE_BACKUP_TAG:-finder-db}"
    FULL_BACKUP_KEEP_WITHIN="${FULL_BACKUP_KEEP_WITHIN:-3d}"
    DATABASE_BACKUP_KEEP_WITHIN="${DATABASE_BACKUP_KEEP_WITHIN:-48h}"
    BACKUP_LOCK_WAIT_SECONDS="${BACKUP_LOCK_WAIT_SECONDS:-7200}"
    BACKUP_VERIFY_READ_DATA_SUBSET="${BACKUP_VERIFY_READ_DATA_SUBSET:-5%}"
    BACKUP_NOTIFY_SUCCESS="${BACKUP_NOTIFY_SUCCESS:-true}"
    DATABASE_BACKUP_NOTIFY_SUCCESS="${DATABASE_BACKUP_NOTIFY_SUCCESS:-false}"
    MAINTENANCE_NOTIFY_SUCCESS="${MAINTENANCE_NOTIFY_SUCCESS:-false}"

    export PROJECT_DIR COMPOSE_FILE POSTGRES_SERVICE RESTIC_PASSWORD_FILE
    export RESTIC_CACHE_DIR BACKUP_STATE_DIR BACKUP_STAGING_DIR BACKUP_LOCK_FILE
    export BACKUP_HOSTNAME AWS_DEFAULT_REGION RESTIC_S3_BUCKET_LOOKUP
    export FULL_BACKUP_TAG DATABASE_BACKUP_TAG

    [[ -d "$PROJECT_DIR" ]] || die "Project directory does not exist: $PROJECT_DIR"
    [[ -f "$COMPOSE_FILE" ]] || die "Compose file does not exist: $COMPOSE_FILE"
    [[ -n "${RESTIC_REPOSITORY:-}" ]] || die "RESTIC_REPOSITORY is not set in $BACKUP_ENV_FILE"
    [[ -n "${AWS_ACCESS_KEY_ID:-}" ]] || die "AWS_ACCESS_KEY_ID is not set in $BACKUP_ENV_FILE"
    [[ -n "${AWS_SECRET_ACCESS_KEY:-}" ]] || die "AWS_SECRET_ACCESS_KEY is not set in $BACKUP_ENV_FILE"

    case "$RESTIC_REPOSITORY $AWS_ACCESS_KEY_ID $AWS_SECRET_ACCESS_KEY" in
        *CHANGE-ME*|*BUCKET-NAME*) die "Replace all placeholder values in $BACKUP_ENV_FILE" ;;
    esac

    [[ -n "$FULL_BACKUP_TAG" ]] || die "FULL_BACKUP_TAG must not be empty"
    [[ -n "$DATABASE_BACKUP_TAG" ]] || die "DATABASE_BACKUP_TAG must not be empty"
    [[ "$FULL_BACKUP_TAG" != "$DATABASE_BACKUP_TAG" ]] || \
        die "FULL_BACKUP_TAG and DATABASE_BACKUP_TAG must be different"
    validate_restic_duration FULL_BACKUP_KEEP_WITHIN "$FULL_BACKUP_KEEP_WITHIN"
    validate_restic_duration DATABASE_BACKUP_KEEP_WITHIN "$DATABASE_BACKUP_KEEP_WITHIN"
    validate_positive_integer BACKUP_LOCK_WAIT_SECONDS "$BACKUP_LOCK_WAIT_SECONDS"
    assert_secret_file "$RESTIC_PASSWORD_FILE" "Restic password file"

    case "$BACKUP_STATE_DIR" in
        "$PROJECT_DIR"/*) ;;
        *) die "BACKUP_STATE_DIR must be strictly below PROJECT_DIR: $BACKUP_STATE_DIR" ;;
    esac
    case "$BACKUP_STAGING_DIR" in
        "$BACKUP_STATE_DIR"/*) ;;
        *) die "BACKUP_STAGING_DIR must be strictly below BACKUP_STATE_DIR: $BACKUP_STAGING_DIR" ;;
    esac
    BACKUP_STAGING_RELATIVE="${BACKUP_STAGING_DIR#"$PROJECT_DIR"/}"
    export BACKUP_STAGING_RELATIVE

    mkdir -p "$RESTIC_CACHE_DIR"
}

compose() {
    docker compose \
        --project-directory "$PROJECT_DIR" \
        --env-file "$PROJECT_DIR/.env.production" \
        -f "$COMPOSE_FILE" \
        "$@"
}

restic_run() {
    restic \
        -o "s3.region=$AWS_DEFAULT_REGION" \
        -o "s3.bucket-lookup=$RESTIC_S3_BUCKET_LOOKUP" \
        "$@"
}

acquire_backup_lock() {
    require_command flock
    mkdir -p "$(dirname -- "$BACKUP_LOCK_FILE")"
    exec 9>"$BACKUP_LOCK_FILE"
    flock -w "$BACKUP_LOCK_WAIT_SECONDS" 9 || \
        die "Timed out waiting for another backup, maintenance, verification, or restore process"
}

forget_full_snapshots() {
    restic_run forget \
        --host "$BACKUP_HOSTNAME" \
        --tag "$FULL_BACKUP_TAG" \
        --group-by host,tags \
        --keep-within "$FULL_BACKUP_KEEP_WITHIN"
}

forget_database_snapshots() {
    restic_run forget \
        --host "$BACKUP_HOSTNAME" \
        --tag "$DATABASE_BACKUP_TAG" \
        --group-by host,tags \
        --keep-within "$DATABASE_BACKUP_KEEP_WITHIN"
}

notify_telegram() {
    local text="$1"

    if [[ -z "${BACKUP_TELEGRAM_BOT_TOKEN:-}" || -z "${BACKUP_TELEGRAM_CHAT_ID:-}" ]]; then
        return 0
    fi
    if ! command -v curl >/dev/null 2>&1; then
        log WARN "Telegram notification skipped because curl is not installed"
        return 0
    fi

    local -a args=(
        --fail --silent --show-error --max-time 20
        --request POST
        --data-urlencode "chat_id=$BACKUP_TELEGRAM_CHAT_ID"
        --data-urlencode "text=${text:0:3900}"
    )
    if [[ -n "${BACKUP_TELEGRAM_MESSAGE_THREAD_ID:-}" ]]; then
        args+=(--data-urlencode "message_thread_id=$BACKUP_TELEGRAM_MESSAGE_THREAD_ID")
    fi

    if ! curl "${args[@]}" \
        "https://api.telegram.org/bot${BACKUP_TELEGRAM_BOT_TOKEN}/sendMessage" \
        >/dev/null; then
        log WARN "Telegram notification could not be delivered"
    fi
}