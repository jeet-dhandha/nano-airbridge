#!/usr/bin/env bash
# nano-airbridge — One-click launcher for macOS and Linux
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_CMD="python3"

if ! command -v python3 &>/dev/null; then
    if command -v python &>/dev/null; then
        PYTHON_CMD="python"
    else
        echo "Error: Python 3 is required. Please install Python 3." >&2
        exit 1
    fi
fi

ROOT_DIR="${1:-$SCRIPT_DIR/demo_workspace}"
PORT="${PORT:-8899}"
MODE="${2:-gateway}"

echo "=================================================="
echo " nano-airbridge — Zero-Dependency MCP Runtime"
echo "=================================================="
echo " Workspace: $ROOT_DIR"
echo " Mode:      $MODE"
echo " OS:        macOS / Linux"
echo " Python:    $($PYTHON_CMD --version)"
echo "=================================================="

case "$MODE" in
    gateway)
        echo "Starting HTTP/MCP Gateway on port $PORT..."
        echo "Pass --enable-writes and --enable-execution if needed."
        exec "$PYTHON_CMD" "$SCRIPT_DIR/nano_http_server.py" \
            --root "$ROOT_DIR" \
            --port "$PORT" \
            --enable-writes \
            --enable-execution
        ;;
    agent)
        RELAY_URL="${RELAY_URL:-https://nano-airbridge.gt.tc/nano_relay.php}"
        AGENT_TOKEN="${AGENT_TOKEN:-airbridge_agent_token_112233}"
        echo "Connecting to Cloud Relay via outbound long-poll:"
        echo " Relay: $RELAY_URL"
        echo " No open ports or ngrok needed."
        exec "$PYTHON_CMD" "$SCRIPT_DIR/relay/nano_agent.py" \
            --relay "$RELAY_URL" \
            --token "$AGENT_TOKEN" \
            --command "$PYTHON_CMD $SCRIPT_DIR/nano_airbridge.py --root $ROOT_DIR --enable-writes --enable-execution"
        ;;
    local)
        echo "Starting stdio MCP server for Claude Desktop / Cursor / Windsurf..."
        exec "$PYTHON_CMD" "$SCRIPT_DIR/nano_airbridge.py" \
            --root "$ROOT_DIR" \
            --enable-writes \
            --enable-execution
        ;;
    *)
        echo "Usage: ./start.sh [path_to_workspace] [gateway|agent|local]"
        exit 1
        ;;
esac
