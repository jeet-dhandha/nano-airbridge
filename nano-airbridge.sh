#!/usr/bin/env bash
# nano-airbridge.sh — Zero-dependency Pure Bash launcher for macOS and Linux.
# Runs the standalone Go binary with zero external runtime dependencies.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/start.sh" "$@"
