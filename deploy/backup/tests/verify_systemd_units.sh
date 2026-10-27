#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${FINDER_SYSTEMD_SOURCE_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../systemd" && pwd -P)}"
TEMP_DIR="$(mktemp -d -t finder-systemd-verify.XXXXXX)"
trap 'rm -rf -- "$TEMP_DIR"' EXIT

for source in "$SOURCE_DIR"/*.service.in; do
    target="$TEMP_DIR/$(basename -- "${source%.in}")"
    tr -d '\r' < "$source" | sed \
        -e 's|@PROJECT_DIR@|/opt/finder|g' \
        -e 's|^ExecStart=.*|ExecStart=/bin/true|' \
        > "$target"
done
for source in "$SOURCE_DIR"/*.timer; do
    tr -d '\r' < "$source" > "$TEMP_DIR/$(basename -- "$source")"
done

# Keep verification isolated from the host's unit set.
cat > "$TEMP_DIR/docker.service" <<'EOF'
[Unit]
Description=Mock Docker dependency for unit verification

[Service]
Type=oneshot
ExecStart=/bin/true
EOF
cat > "$TEMP_DIR/network-online.target" <<'EOF'
[Unit]
Description=Mock network-online dependency for unit verification
EOF

SYSTEMD_UNIT_PATH="$TEMP_DIR:/etc/systemd/system:/run/systemd/system:/usr/local/lib/systemd/system:/usr/lib/systemd/system:/lib/systemd/system" \
systemd-analyze verify \
    "$TEMP_DIR"/*.service \
    "$TEMP_DIR"/*.timer

printf 'FINDER systemd unit verification passed.\n'