#!/usr/bin/env bash
set -Eeuo pipefail

# Backward-compatible entry point. The implementation lives in deploy/backup/.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$SCRIPT_DIR/backup/backup.sh" "$@"