#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
INSTALL_DIR="/usr/local/lib/finder-backup"
CONFIG_DIR="/etc/finder"
INIT_REPOSITORY=false
ENABLE_TIMERS=false
SKIP_PACKAGES=false

usage() {
    cat <<'EOF'
Usage: install.sh [options]

Options:
  --project-dir PATH    Production project directory (default: script location)
  --init-repository    Initialize the configured Restic repository if needed
  --enable-timers      Validate the repository and enable all backup timers
  --skip-packages      Do not install restic and curl with apt
  -h, --help           Show this help

Recommended sequence:
  1. sudo bash deploy/backup/install.sh --project-dir /opt/finder
  2. sudoedit /etc/finder/backup.env
  3. sudo bash deploy/backup/install.sh --project-dir /opt/finder --init-repository
  4. Run and verify the first backup
  5. sudo bash deploy/backup/install.sh --project-dir /opt/finder --enable-timers
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-dir)
            [[ $# -ge 2 ]] || { usage >&2; exit 2; }
            PROJECT_DIR="$2"
            shift 2
            ;;
        --init-repository)
            INIT_REPOSITORY=true
            shift
            ;;
        --enable-timers)
            ENABLE_TIMERS=true
            shift
            ;;
        --skip-packages)
            SKIP_PACKAGES=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

[[ "$(id -u)" -eq 0 ]] || { printf 'Run this installer as root.\n' >&2; exit 1; }
PROJECT_DIR="$(cd -- "$PROJECT_DIR" && pwd -P)"
[[ -f "$PROJECT_DIR/docker-compose.yml" ]] || {
    printf 'docker-compose.yml was not found in %s\n' "$PROJECT_DIR" >&2
    exit 1
}

if [[ "$SKIP_PACKAGES" == false ]]; then
    command -v apt-get >/dev/null 2>&1 || {
        printf 'apt-get is unavailable; install restic and curl manually, then use --skip-packages.\n' >&2
        exit 1
    }
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y restic curl ca-certificates util-linux
fi

command -v restic >/dev/null 2>&1 || { printf 'restic is not installed.\n' >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { printf 'systemctl is not installed.\n' >&2; exit 1; }

install -d -o root -g root -m 0750 "$INSTALL_DIR" "$CONFIG_DIR"
install -d -o root -g root -m 0700 /var/cache/finder-restic
# Preserve backups/ permissions: existing backup jobs may still write there.
install -d -o root -g root -m 0700 "$PROJECT_DIR/backups/restic"

for script in common.sh backup.sh database-backup.sh maintenance.sh verify.sh restore.sh; do
    install -o root -g root -m 0750 "$SCRIPT_DIR/$script" "$INSTALL_DIR/$script"
done

if [[ ! -e "$CONFIG_DIR/backup.env" ]]; then
    install -o root -g root -m 0600 "$SCRIPT_DIR/backup.env.example" "$CONFIG_DIR/backup.env"
    sed -i "s|^PROJECT_DIR=.*|PROJECT_DIR=$PROJECT_DIR|" "$CONFIG_DIR/backup.env"
    sed -i "s|^COMPOSE_FILE=.*|COMPOSE_FILE=$PROJECT_DIR/docker-compose.yml|" "$CONFIG_DIR/backup.env"
    sed -i "s|^BACKUP_STATE_DIR=.*|BACKUP_STATE_DIR=$PROJECT_DIR/backups/restic|" "$CONFIG_DIR/backup.env"
    sed -i "s|^BACKUP_STAGING_DIR=.*|BACKUP_STAGING_DIR=$PROJECT_DIR/backups/restic/staging|" "$CONFIG_DIR/backup.env"
    printf 'Created %s. Fill in the bucket and S3 credentials with sudoedit.\n' "$CONFIG_DIR/backup.env"
else
    chown root:root "$CONFIG_DIR/backup.env"
    chmod 0600 "$CONFIG_DIR/backup.env"
    printf 'Preserved existing %s.\n' "$CONFIG_DIR/backup.env"
fi

append_default_setting() {
    local name="$1"
    local value="$2"
    if ! grep -q "^${name}=" "$CONFIG_DIR/backup.env"; then
        printf '%s=%s\n' "$name" "$value" >> "$CONFIG_DIR/backup.env"
    fi
}

append_default_setting FULL_BACKUP_TAG finder-full
append_default_setting DATABASE_BACKUP_TAG finder-db
append_default_setting FULL_BACKUP_KEEP_WITHIN 3d
append_default_setting DATABASE_BACKUP_KEEP_WITHIN 48h
append_default_setting BACKUP_LOCK_WAIT_SECONDS 7200
append_default_setting DATABASE_BACKUP_NOTIFY_SUCCESS false
append_default_setting MAINTENANCE_NOTIFY_SUCCESS false

if [[ ! -e "$CONFIG_DIR/restic-password" ]]; then
    head -c 64 /dev/urandom | base64 | tr -d '\n' > "$CONFIG_DIR/restic-password"
    printf '\n' >> "$CONFIG_DIR/restic-password"
    chown root:root "$CONFIG_DIR/restic-password"
    chmod 0600 "$CONFIG_DIR/restic-password"
    printf 'Generated %s. Save an offline copy; losing it makes backups unrecoverable.\n' \
        "$CONFIG_DIR/restic-password"
else
    chown root:root "$CONFIG_DIR/restic-password"
    chmod 0600 "$CONFIG_DIR/restic-password"
    printf 'Preserved existing %s.\n' "$CONFIG_DIR/restic-password"
fi

escaped_project_dir="${PROJECT_DIR//&/\\&}"
for unit in \
    finder-backup.service \
    finder-database-backup.service \
    finder-backup-maintenance.service \
    finder-backup-verify.service \
    finder-backup-restore-test.service; do
    sed "s|@PROJECT_DIR@|$escaped_project_dir|g" "$SCRIPT_DIR/systemd/$unit.in" \
        > "/etc/systemd/system/$unit"
    chown root:root "/etc/systemd/system/$unit"
    chmod 0644 "/etc/systemd/system/$unit"
done
for timer in \
    finder-backup.timer \
    finder-database-backup.timer \
    finder-backup-maintenance.timer \
    finder-backup-verify.timer \
    finder-backup-restore-test.timer; do
    install -o root -g root -m 0644 "$SCRIPT_DIR/systemd/$timer" "/etc/systemd/system/$timer"
done
systemctl daemon-reload

if [[ "$INIT_REPOSITORY" == true || "$ENABLE_TIMERS" == true ]]; then
    export FINDER_BACKUP_ENV="$CONFIG_DIR/backup.env"
    # shellcheck source=common.sh
    source "$INSTALL_DIR/common.sh"
    load_backup_config
    require_command restic
fi

if [[ "$INIT_REPOSITORY" == true ]]; then
    if restic_run cat config >/dev/null 2>&1; then
        printf 'Restic repository already exists; it was not reinitialized.\n'
    else
        printf 'Initializing encrypted Restic repository...\n'
        restic_run init
    fi
    restic_run snapshots --compact
fi

if [[ "$ENABLE_TIMERS" == true ]]; then
    restic_run cat config >/dev/null
    systemctl enable --now \
        finder-backup.timer \
        finder-database-backup.timer \
        finder-backup-maintenance.timer \
        finder-backup-verify.timer \
        finder-backup-restore-test.timer
    systemctl list-timers --all 'finder-backup*' 'finder-database-backup*'
else
    printf 'Timers are installed but not enabled. Complete configuration and verification first.\n'
fi