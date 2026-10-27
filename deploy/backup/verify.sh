#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

STARTED_AT="$(date +%s)"
VERIFY_SUCCEEDED=false
RESTORE_TEST=false
QUICK_CHECK=false
TEMP_DIR=""
TEST_CONTAINER=""

usage() {
    cat <<'EOF'
Usage: verify.sh [--quick|--restore-test]

  --quick         Check repository structure without reading data packs.
  --restore-test  Check the repository, restore the latest PostgreSQL dump,
                  and import it into an isolated temporary PostgreSQL container.
EOF
}

case "${1:-}" in
    "") ;;
    --quick) QUICK_CHECK=true ;;
    --restore-test) RESTORE_TEST=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM
    if [[ -n "$TEST_CONTAINER" ]]; then
        docker rm -f "$TEST_CONTAINER" >/dev/null 2>&1 || true
    fi
    if [[ -n "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi

    local elapsed=$(( $(date +%s) - STARTED_AT ))
    if [[ "$VERIFY_SUCCEEDED" == true && "$exit_code" -eq 0 ]]; then
        log INFO "FINDER backup verification completed successfully in ${elapsed}s"
        if is_true "${BACKUP_NOTIFY_SUCCESS:-true}"; then
            notify_telegram "✅ FINDER backup verification completed on ${BACKUP_HOSTNAME} in ${elapsed}s."
        fi
    else
        log ERROR "FINDER backup verification failed with exit code $exit_code after ${elapsed}s"
        notify_telegram "❌ FINDER backup verification FAILED on ${BACKUP_HOSTNAME:-unknown}. Check the systemd journal."
    fi
    exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

load_backup_config
require_command restic
acquire_backup_lock

if [[ "$QUICK_CHECK" == true ]]; then
    BACKUP_VERIFY_READ_DATA_SUBSET=""
fi

log INFO "Listing latest full FINDER snapshot"
restic_run snapshots --host "$BACKUP_HOSTNAME" --tag "$FULL_BACKUP_TAG" --compact

log INFO "Listing latest database-only FINDER snapshot"
restic_run snapshots --host "$BACKUP_HOSTNAME" --tag "$DATABASE_BACKUP_TAG" --compact

check_args=(check)
if [[ -n "$BACKUP_VERIFY_READ_DATA_SUBSET" ]]; then
    check_args+=("--read-data-subset=$BACKUP_VERIFY_READ_DATA_SUBSET")
fi
log INFO "Checking Restic repository integrity"
restic_run "${check_args[@]}"

if [[ "$RESTORE_TEST" == true ]]; then
    require_command docker
    TEMP_DIR="$(mktemp -d -t finder-restore-test.XXXXXX)"
    log INFO "Restoring the latest PostgreSQL dump into an isolated directory"
    restic_run restore latest \
        --host "$BACKUP_HOSTNAME" \
        --tag "$DATABASE_BACKUP_TAG" \
        --include "/$BACKUP_STAGING_RELATIVE/postgres.dump" \
        --target "$TEMP_DIR"

    DUMP_PATH="$TEMP_DIR/$BACKUP_STAGING_RELATIVE/postgres.dump"
    [[ -s "$DUMP_PATH" ]] || die "Restored PostgreSQL dump is missing or empty: $DUMP_PATH"

    POSTGRES_IMAGE="${BACKUP_TEST_POSTGRES_IMAGE:-postgres:16-alpine}"
    TEST_CONTAINER="finder-postgres-restore-test-$$"
    TEST_PASSWORD="$(head -c 48 /dev/urandom | base64 | tr -d '\n')"
    TEST_DATABASE="finder_restore_test"

    log INFO "Starting temporary PostgreSQL container using $POSTGRES_IMAGE"
    docker run -d --rm \
        --name "$TEST_CONTAINER" \
        --tmpfs /var/lib/postgresql/data \
        -e "POSTGRES_PASSWORD=$TEST_PASSWORD" \
        -e "POSTGRES_DB=$TEST_DATABASE" \
        "$POSTGRES_IMAGE" >/dev/null

    ready=false
    for _ in $(seq 1 60); do
        if docker exec "$TEST_CONTAINER" pg_isready -U postgres -d "$TEST_DATABASE" >/dev/null 2>&1; then
            ready=true
            break
        fi
        sleep 1
    done
    [[ "$ready" == true ]] || die "Temporary PostgreSQL container did not become ready"

    log INFO "Importing dump into temporary PostgreSQL container"
    docker exec -i "$TEST_CONTAINER" pg_restore \
        --exit-on-error \
        --no-owner \
        --no-acl \
        --username=postgres \
        --dbname="$TEST_DATABASE" \
        < "$DUMP_PATH"

    table_count="$(docker exec "$TEST_CONTAINER" psql \
        --username=postgres \
        --dbname="$TEST_DATABASE" \
        --tuples-only --no-align \
        --command="SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';")"
    [[ "$table_count" =~ ^[0-9]+$ && "$table_count" -gt 0 ]] || \
        die "Restore test completed without any public tables"
    log INFO "Restore test succeeded; public table count: $table_count"
fi

VERIFY_SUCCEEDED=true