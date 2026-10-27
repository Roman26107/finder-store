#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

STARTED_AT="$(date +%s)"
MAINTENANCE_SUCCEEDED=false

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM
    local elapsed=$(( $(date +%s) - STARTED_AT ))
    if [[ "$MAINTENANCE_SUCCEEDED" == true && "$exit_code" -eq 0 ]]; then
        log INFO "FINDER backup maintenance completed successfully in ${elapsed}s"
        if is_true "${MAINTENANCE_NOTIFY_SUCCESS:-false}"; then
            notify_telegram "✅ FINDER backup maintenance completed on ${BACKUP_HOSTNAME} in ${elapsed}s."
        fi
    else
        log ERROR "FINDER backup maintenance failed with exit code $exit_code after ${elapsed}s"
        notify_telegram "❌ FINDER backup maintenance FAILED on ${BACKUP_HOSTNAME:-unknown}. Check: journalctl -u finder-backup-maintenance.service"
    fi
    exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

load_backup_config
require_command restic
acquire_backup_lock

log INFO "Checking Restic repository structure before cleanup"
restic_run check

log INFO "Removing expired full-backup snapshots older than $FULL_BACKUP_KEEP_WITHIN"
forget_full_snapshots

log INFO "Removing expired database snapshots older than $DATABASE_BACKUP_KEEP_WITHIN"
forget_database_snapshots

log INFO "Pruning unreferenced Restic data from S3"
restic_run prune

MAINTENANCE_SUCCEEDED=true