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

ROOT_DIR=""
RELAY_URL=""
AGENT_TOKEN="${AGENT_TOKEN:-airbridge_agent_token_112233}"
PORT="${PORT:-8899}"
MODE=""

# Parse flags and arguments in any order
while [[ $# -gt 0 ]]; do
    case "$1" in
        --relay)
            RELAY_URL="${2:-}"
            MODE="agent"
            shift 2
            ;;
        --token)
            AGENT_TOKEN="${2:-}"
            shift 2
            ;;
        --port)
            PORT="${2:-}"
            shift 2
            ;;
        gateway|agent|local)
            MODE="$1"
            shift
            ;;
        -h|--help)
            echo "Usage: ./start.sh [workspace_path] [options]"
            echo ""
            echo "Options:"
            echo "  --relay <URL>   Connect to remote PHP / cloud relay (e.g. https://my-server.com/nano_relay.php)"
            echo "  --token <TOKEN> Agent authentication token (default: airbridge_agent_token_112233)"
            echo "  --port <PORT>   Port for local HTTP gateway (default: 8899)"
            echo "  local           Start stdio MCP server for Claude Desktop / Cursor / Windsurf"
            echo ""
            echo "Examples:"
            echo "  ./start.sh demo_workspace"
            echo "  ./start.sh demo_workspace --relay https://my-server.com/nano_relay.php"
            echo "  ./start.sh --relay https://my-server.com/nano_relay.php"
            exit 0
            ;;
        *)
            if [[ -z "$ROOT_DIR" ]]; then
                ROOT_DIR="$1"
            fi
            shift
            ;;
    esac
done

if [[ -z "$ROOT_DIR" ]]; then
    ROOT_DIR="$SCRIPT_DIR/demo_workspace"
fi

if [[ -d "$ROOT_DIR" ]]; then
    ROOT_DIR="$(cd "$ROOT_DIR" && pwd)"
fi

if [[ -z "$MODE" ]]; then
    if [[ -n "$RELAY_URL" ]]; then
        MODE="agent"
    else
        MODE="gateway"
    fi
fi

echo "=================================================="
echo " nano-airbridge — Zero-Dependency MCP Runtime"
echo "=================================================="
echo " Workspace: $ROOT_DIR"
echo " Mode:      $MODE"
echo " OS:        macOS / Linux"
echo " Python:    $($PYTHON_CMD --version)"
if [[ "$MODE" == "agent" ]]; then
echo " Relay URL: $RELAY_URL"
echo " Token:     ${AGENT_TOKEN:0:8}..."
else
echo " HTTP Port: $PORT"
fi
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
        if [[ -z "$RELAY_URL" ]]; then
            RELAY_URL="https://nano-airbridge.gt.tc/nano_relay.php"
        fi
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
        echo "Usage: ./start.sh [path_to_workspace] [--relay <URL>]"
        exit 1
        ;;
esac
