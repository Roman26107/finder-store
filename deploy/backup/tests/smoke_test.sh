#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEMP_ROOT="$(mktemp -d -t finder-backup-smoke.XXXXXX)"
trap 'rm -rf -- "$TEMP_ROOT"' EXIT

PROJECT_DIR="$TEMP_ROOT/project"
MOCK_BIN="$TEMP_ROOT/bin"
MOCK_LOG="$TEMP_ROOT/mock.log"
ENV_FILE="$TEMP_ROOT/backup.env"
PASSWORD_FILE="$TEMP_ROOT/restic-password"

mkdir -p "$PROJECT_DIR/media" "$PROJECT_DIR/deploy/nginx" \
    "$PROJECT_DIR/deploy/backup" "$PROJECT_DIR/backups" "$MOCK_BIN"
cp "$SOURCE_DIR"/*.sh "$PROJECT_DIR/deploy/backup/"
printf 'image' > "$PROJECT_DIR/media/product.jpg"
printf 'DOMAIN=example.test\n' > "$PROJECT_DIR/.env.production"
printf 'services: {}\n' > "$PROJECT_DIR/docker-compose.yml"
printf 'server {}\n' > "$PROJECT_DIR/deploy/nginx/site.conf"
printf 'test-password\n' > "$PASSWORD_FILE"

cat > "$ENV_FILE" <<EOF
RESTIC_REPOSITORY=s3:https://s3.example.test/finder/finder
AWS_ACCESS_KEY_ID=test-access-key
AWS_SECRET_ACCESS_KEY=test-secret-key
AWS_DEFAULT_REGION=ru-1
RESTIC_S3_BUCKET_LOOKUP=path
RESTIC_PASSWORD_FILE=$PASSWORD_FILE
RESTIC_CACHE_DIR=$TEMP_ROOT/cache
PROJECT_DIR=$PROJECT_DIR
COMPOSE_FILE=$PROJECT_DIR/docker-compose.yml
POSTGRES_SERVICE=db
BACKUP_STATE_DIR=$PROJECT_DIR/backups
BACKUP_STAGING_DIR=$PROJECT_DIR/backups/staging
BACKUP_LOCK_FILE=$TEMP_ROOT/finder-backup.lock
BACKUP_HOSTNAME=finder-smoke-test
FULL_BACKUP_TAG=finder-full
DATABASE_BACKUP_TAG=finder-db
FULL_BACKUP_KEEP_WITHIN=3d
DATABASE_BACKUP_KEEP_WITHIN=48h
BACKUP_LOCK_WAIT_SECONDS=7200
BACKUP_VERIFY_READ_DATA_SUBSET=5%
BACKUP_NOTIFY_SUCCESS=false
DATABASE_BACKUP_NOTIFY_SUCCESS=false
MAINTENANCE_NOTIFY_SUCCESS=false
EOF

cat > "$MOCK_BIN/docker" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'docker %s\n' "$*" >> "$MOCK_LOG"
case "$*" in
    *"compose version --short"*) printf '2.30.0\n' ;;
    *"postgres --version"*) printf 'postgres (PostgreSQL) 16.4\n' ;;
    *"pg_dump"*) printf 'PGDMP-finder-smoke-test\n' ;;
    *"pg_restore --list"*) cat >/dev/null ;;
    *) printf 'Unexpected docker call: %s\n' "$*" >&2; exit 1 ;;
esac
EOF

cat > "$MOCK_BIN/restic" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
printf 'restic %s\n' "$*" >> "$MOCK_LOG"
case "$*" in
    version|*" version") printf 'restic 0.16.4 compiled with go1.22\n' ;;
    *" restore "*)
        target=''
        previous=''
        for argument in "$@"; do
            if [[ "$previous" == '--target' ]]; then
                target="$argument"
                break
            fi
            previous="$argument"
        done
        [[ -n "$target" ]] || { printf 'Missing restore target\n' >&2; exit 1; }
        mkdir -p "$target/backups/staging"
        printf 'PGDMP-restored-smoke-test\n' > "$target/backups/staging/postgres.dump"
        ;;
    *" backup "*|*" forget "*|*" snapshots "*|*" check"*|*" prune"*|*" ls "*) ;;
    *) printf 'Unexpected restic call: %s\n' "$*" >&2; exit 1 ;;
esac
EOF

cat > "$MOCK_BIN/flock" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

chmod +x "$MOCK_BIN/docker" "$MOCK_BIN/restic" "$MOCK_BIN/flock"
export PATH="$MOCK_BIN:$PATH"
export MOCK_LOG
export FINDER_BACKUP_ENV="$ENV_FILE"
export BACKUP_ALLOW_INSECURE_PERMISSIONS=true

bash "$SOURCE_DIR/backup.sh"
[[ ! -e "$PROJECT_DIR/backups/staging" ]] || {
    printf 'Staging directory was not removed after backup\n' >&2
    exit 1
}
grep -q 'restic .* backup .*media .*\.env.production .*backups/staging/postgres.dump' "$MOCK_LOG"
grep -q 'restic .* backup .*--tag finder-full .*--tag production' "$MOCK_LOG"
grep -q 'restic .* forget .*--tag finder-full .*--keep-within 3d' "$MOCK_LOG"

: > "$MOCK_LOG"
bash "$SOURCE_DIR/database-backup.sh"
[[ ! -e "$PROJECT_DIR/backups/staging" ]] || {
    printf 'Staging directory was not removed after database backup\n' >&2
    exit 1
}
grep -q 'restic .* backup .*--tag finder-db .*backups/staging/postgres.dump .*backups/staging/manifest.txt' "$MOCK_LOG"
if grep -q 'restic .* backup .*media' "$MOCK_LOG"; then
    printf 'Database-only backup unexpectedly included media\n' >&2
    exit 1
fi
grep -q 'restic .* forget .*--tag finder-db .*--keep-within 48h' "$MOCK_LOG"

: > "$MOCK_LOG"
bash "$SOURCE_DIR/maintenance.sh"
check_line="$(grep -n 'restic .* check' "$MOCK_LOG" | cut -d: -f1)"
full_forget_line="$(grep -n 'restic .* forget .*--tag finder-full .*--keep-within 3d' "$MOCK_LOG" | cut -d: -f1)"
database_forget_line="$(grep -n 'restic .* forget .*--tag finder-db .*--keep-within 48h' "$MOCK_LOG" | cut -d: -f1)"
prune_line="$(grep -n 'restic .* prune' "$MOCK_LOG" | cut -d: -f1)"
[[ -n "$check_line" && -n "$full_forget_line" && -n "$database_forget_line" && -n "$prune_line" \
    && "$check_line" -lt "$full_forget_line" \
    && "$full_forget_line" -lt "$database_forget_line" \
    && "$database_forget_line" -lt "$prune_line" ]] || {
    printf 'Repository check must run before prune\n' >&2
    exit 1
}

: > "$MOCK_LOG"
bash "$SOURCE_DIR/verify.sh" --quick
grep -q 'restic .* snapshots .*--tag finder-full' "$MOCK_LOG"
grep -q 'restic .* snapshots .*--tag finder-db' "$MOCK_LOG"
grep -q 'restic .* check' "$MOCK_LOG"
if grep -q 'restic .* prune' "$MOCK_LOG"; then
    printf 'Weekly verification unexpectedly pruned the repository\n' >&2
    exit 1
fi

: > "$MOCK_LOG"
RESTORED_DUMP="$TEMP_ROOT/restored.dump"
bash "$SOURCE_DIR/restore.sh" database-dump latest "$RESTORED_DUMP"
grep -q '^PGDMP-restored-smoke-test$' "$RESTORED_DUMP"
grep -q 'restic .* restore latest .*--tag finder-db' "$MOCK_LOG"

: > "$MOCK_LOG"
bash "$SOURCE_DIR/restore.sh" files latest
grep -q 'restic .* ls latest .*--tag finder-full' "$MOCK_LOG"

bash "$SOURCE_DIR/restore.sh" --help >/dev/null
bash "$SOURCE_DIR/verify.sh" --help >/dev/null
bash "$SOURCE_DIR/install.sh" --help >/dev/null

UNSAFE_ENV_FILE="$TEMP_ROOT/unsafe-backup.env"
sed "s|^BACKUP_STAGING_DIR=.*|BACKUP_STAGING_DIR=$PROJECT_DIR|" \
    "$ENV_FILE" > "$UNSAFE_ENV_FILE"
if FINDER_BACKUP_ENV="$UNSAFE_ENV_FILE" bash "$SOURCE_DIR/backup.sh" >/dev/null 2>&1; then
    printf 'Unsafe staging directory was accepted\n' >&2
    exit 1
fi
[[ -f "$PROJECT_DIR/docker-compose.yml" ]] || {
    printf 'Unsafe staging test modified the project directory\n' >&2
    exit 1
}

printf 'FINDER backup smoke test passed.\n'