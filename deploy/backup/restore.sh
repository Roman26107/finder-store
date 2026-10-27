#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage:
  restore.sh list [all|full|database]
  restore.sh files [SNAPSHOT]
  restore.sh extract [SNAPSHOT] TARGET_DIRECTORY
  restore.sh database-dump [SNAPSHOT] OUTPUT_FILE
  restore.sh database-test [SNAPSHOT]

SNAPSHOT defaults to "latest". Extraction is only allowed into a new or empty
directory. Production data is never overwritten automatically.
EOF
}

restore_database_dump() {
    local snapshot="$1"
    local output="$2"
    local temp_dir

    [[ ! -e "$output" ]] || die "Output file already exists: $output"
    mkdir -p "$(dirname -- "$output")"
    temp_dir="$(mktemp -d -t finder-dump-restore.XXXXXX)"

    local -a selection=(--host "$BACKUP_HOSTNAME")
    if [[ "$snapshot" == latest ]]; then
        selection+=(--tag "$DATABASE_BACKUP_TAG")
    fi

    if ! restic_run restore "$snapshot" \
        "${selection[@]}" \
        --include "/$BACKUP_STAGING_RELATIVE/postgres.dump" \
        --target "$temp_dir"; then
        rm -rf -- "$temp_dir"
        return 1
    fi
    [[ -s "$temp_dir/$BACKUP_STAGING_RELATIVE/postgres.dump" ]] || {
        rm -rf -- "$temp_dir"
        die "Dump was not found in snapshot $snapshot"
    }
    if ! install -m 0600 "$temp_dir/$BACKUP_STAGING_RELATIVE/postgres.dump" "$output"; then
        rm -rf -- "$temp_dir"
        die "Could not copy the restored dump to $output"
    fi
    rm -rf -- "$temp_dir"
}

command_name="${1:-}"
if [[ "$command_name" == "-h" || "$command_name" == "--help" ]]; then
    usage
    exit 0
fi

load_backup_config
require_command restic
acquire_backup_lock

case "$command_name" in
    list)
        [[ $# -le 2 ]] || { usage >&2; exit 2; }
        case "${2:-all}" in
            all) restic_run snapshots --host "$BACKUP_HOSTNAME" --tag finder ;;
            full) restic_run snapshots --host "$BACKUP_HOSTNAME" --tag "$FULL_BACKUP_TAG" ;;
            database) restic_run snapshots --host "$BACKUP_HOSTNAME" --tag "$DATABASE_BACKUP_TAG" ;;
            *) usage >&2; exit 2 ;;
        esac
        ;;
    files)
        [[ $# -le 2 ]] || { usage >&2; exit 2; }
        snapshot="${2:-latest}"
        selection=(--host "$BACKUP_HOSTNAME")
        if [[ "$snapshot" == latest ]]; then
            selection+=(--tag "$FULL_BACKUP_TAG")
        fi
        restic_run ls "$snapshot" "${selection[@]}"
        ;;
    extract)
        if [[ $# -eq 2 ]]; then
            snapshot=latest
            target="$2"
        elif [[ $# -eq 3 ]]; then
            snapshot="$2"
            target="$3"
        else
            usage >&2
            exit 2
        fi
        mkdir -p "$target"
        if find "$target" -mindepth 1 -print -quit | grep -q .; then
            die "Restore target must be empty: $target"
        fi
        chmod 0700 "$target"
        selection=(--host "$BACKUP_HOSTNAME")
        if [[ "$snapshot" == latest ]]; then
            selection+=(--tag "$FULL_BACKUP_TAG")
        fi
        restic_run restore "$snapshot" "${selection[@]}" --target "$target"
        log INFO "Snapshot restored to $target; production files were not modified"
        ;;
    database-dump)
        if [[ $# -eq 2 ]]; then
            snapshot=latest
            output="$2"
        elif [[ $# -eq 3 ]]; then
            snapshot="$2"
            output="$3"
        else
            usage >&2
            exit 2
        fi
        restore_database_dump "$snapshot" "$output"
        log INFO "PostgreSQL dump restored to $output"
        ;;
    database-test)
        [[ $# -le 2 ]] || { usage >&2; exit 2; }
        require_command docker
        snapshot="${2:-latest}"
        temp_dir="$(mktemp -d -t finder-database-test.XXXXXX)"
        dump_path="$temp_dir/postgres.dump"
        container="finder-postgres-manual-test-$$"
        trap 'docker rm -f "$container" >/dev/null 2>&1 || true; rm -rf -- "$temp_dir"' EXIT
        restore_database_dump "$snapshot" "$dump_path"

        password="$(head -c 48 /dev/urandom | base64 | tr -d '\n')"
        docker run -d --rm --name "$container" --tmpfs /var/lib/postgresql/data \
            -e "POSTGRES_PASSWORD=$password" -e POSTGRES_DB=finder_restore_test \
            "${BACKUP_TEST_POSTGRES_IMAGE:-postgres:16-alpine}" >/dev/null
        ready=false
        for _ in $(seq 1 60); do
            if docker exec "$container" pg_isready -U postgres -d finder_restore_test >/dev/null 2>&1; then
                ready=true
                break
            fi
            sleep 1
        done
        [[ "$ready" == true ]] || die "Temporary PostgreSQL container did not become ready"
        docker exec -i "$container" pg_restore --exit-on-error --no-owner --no-acl \
            -U postgres -d finder_restore_test < "$dump_path"
        table_count="$(docker exec "$container" psql -U postgres -d finder_restore_test \
            --tuples-only --no-align -c \
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
        [[ "$table_count" =~ ^[0-9]+$ && "$table_count" -gt 0 ]] || \
            die "Restore test completed without any public tables"
        log INFO "Database restore test succeeded; public table count: $table_count; production was not modified"
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac