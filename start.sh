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
SESSION_KEY="${SESSION_KEY:-}"
PORT="${PORT:-8899}"
USE_TUNNEL="${USE_TUNNEL:-true}"
MODE=""

# Parse flags and arguments in any order
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-tunnel)
            USE_TUNNEL="false"
            shift
            ;;
        --relay)
            RELAY_URL="${2:-}"
            MODE="agent"
            shift 2
            ;;
        --session)
            SESSION_KEY="${2:-}"
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
            echo "  --relay <URL>     Connect to remote PHP / cloud relay (e.g. https://my-server.com/nano_relay.php)"
            echo "  --session <ID>    Unique session key for workspace isolation (auto-generated if omitted)"
            echo "  --token <TOKEN>   Agent authentication token (default: airbridge_agent_token_112233)"
            echo "  --port <PORT>     Port for local HTTP gateway (default: 8899)"
            echo "  local             Start stdio MCP server for Claude Desktop / Cursor / Windsurf"
            echo ""
            echo "Examples:"
            echo "  ./start.sh demo_workspace"
            echo "  ./start.sh demo_workspace --relay https://nano-airbridge.gt.tc/nano_relay.php"
            echo "  ./start.sh demo_workspace --relay --session my_project_sess"
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

# Detect or generate session key and public endpoint URL
NGROK_URL=""
TUNNEL_URL=""
TUNNEL_PID=""
MCP_ENDPOINT=""

cleanup() {
    if [[ -n "$TUNNEL_PID" ]]; then
        kill "$TUNNEL_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

if [[ "$MODE" == "agent" ]]; then
    if [[ -z "$RELAY_URL" ]]; then
        RELAY_URL="https://nano-airbridge.gt.tc/nano_relay.php"
    fi
    if [[ -z "$SESSION_KEY" ]]; then
        SESSION_KEY="$($PYTHON_CMD -c 'import secrets; print(secrets.token_hex(8))' 2>/dev/null || echo "sess_$(date +%s)")"
    fi
    MCP_ENDPOINT="${RELAY_URL}?op=mcp&s=${SESSION_KEY}"
elif [[ "$MODE" == "gateway" ]]; then
    # 1. Check if ngrok is already running locally
    if command -v curl &>/dev/null; then
        NGROK_URL=$(curl -s --connect-timeout 0.5 http://127.0.0.1:4040/api/tunnels 2>/dev/null | $PYTHON_CMD -c 'import sys, json; data=json.load(sys.stdin); print(data["tunnels"][0]["public_url"])' 2>/dev/null || true)
    fi

    # 2. If ngrok is running, use it
    if [[ -n "$NGROK_URL" ]]; then
        MCP_ENDPOINT="${NGROK_URL}/mcp"
    elif [[ "$USE_TUNNEL" != "false" ]]; then
        # 3. Check for npx to launch localtunnel
        if ! command -v npx &>/dev/null; then
            echo "================================================================================"
            echo " ⚠️  Node.js & npx are not installed (needed to launch free localtunnel)."
            echo "--------------------------------------------------------------------------------"
            echo " To enable free public tunneling for ChatGPT / Claude without any accounts:"
            if [[ "$OSTYPE" == "darwin"* ]]; then
                echo "   macOS (Homebrew):   brew install node"
            elif [[ -f /etc/debian_version ]]; then
                echo "   Ubuntu / Debian:    sudo apt update && sudo apt install -y nodejs npm"
            elif [[ -f /etc/fedora-release ]] || [[ -f /etc/redhat-release ]]; then
                echo "   Fedora / RHEL:      sudo dnf install -y nodejs"
            elif [[ -f /etc/arch-release ]]; then
                echo "   Arch Linux:         sudo pacman -S nodejs npm"
            else
                echo "   Download Node.js:   https://nodejs.org/"
            fi
            echo "--------------------------------------------------------------------------------"
            echo " Continuing with local gateway on http://localhost:${PORT}/mcp"
            echo "================================================================================"
            MCP_ENDPOINT="http://localhost:${PORT}/mcp"
        else
            echo "⚡ Launching free localtunnel (zero accounts, no sign-up)..."
            TUNNEL_LOG="/tmp/airbridge_lt_${PORT}.log"
            rm -f "$TUNNEL_LOG"
            npx -y localtunnel --port "$PORT" > "$TUNNEL_LOG" 2>&1 &
            TUNNEL_PID=$!
            for i in {1..30}; do
                if [[ -f "$TUNNEL_LOG" ]]; then
                    TUNNEL_URL=$(grep -o 'https://[^ ]*loca\.lt' "$TUNNEL_LOG" 2>/dev/null | head -n 1 || true)
                    if [[ -n "$TUNNEL_URL" ]]; then
                        break
                    fi
                fi
                sleep 0.3
            done

            if [[ -n "$TUNNEL_URL" ]]; then
                MCP_ENDPOINT="${TUNNEL_URL}/mcp"
            else
                echo " [!] localtunnel timed out, falling back to local port."
                MCP_ENDPOINT="http://localhost:${PORT}/mcp"
            fi
        fi
    else
        MCP_ENDPOINT="http://localhost:${PORT}/mcp"
    fi
fi

echo "================================================================================"
echo " nano-airbridge — Zero-Dependency MCP Runtime"
echo "================================================================================"
echo " Workspace:    $ROOT_DIR"
echo " Mode:         $MODE"
echo " OS:           macOS / Linux"
echo " Python:       $($PYTHON_CMD --version)"
if [[ "$MODE" == "agent" ]]; then
echo " Relay URL:    $RELAY_URL"
echo " Session Key:  $SESSION_KEY"
echo " MCP Endpoint: $MCP_ENDPOINT"
echo " Token:        ${AGENT_TOKEN:0:8}..."
elif [[ "$MODE" == "gateway" ]]; then
if [[ -n "$TUNNEL_URL" ]]; then
echo " Public Tunnel: $TUNNEL_URL (localtunnel)"
elif [[ -n "$NGROK_URL" ]]; then
echo " Public Tunnel: $NGROK_URL (ngrok)"
fi
echo " MCP Endpoint: $MCP_ENDPOINT"
echo " Local Port:   $PORT"
fi
echo "================================================================================"
echo ""
if [[ "$MODE" != "local" ]]; then
echo "📋 COPY-PASTE SYSTEM INSTRUCTION FOR CHATGPT / CLAUDE / KIMI:"
echo "--------------------------------------------------------------------------------"
echo "[SYSTEM INSTRUCTION: NANO-AIRBRIDGE WORKSPACE INTEGRATION]"
echo "You are connected to the operator's local project workspace via nano-airbridge."
echo "- Bridge Endpoint: $MCP_ENDPOINT"
if [[ -n "$SESSION_KEY" ]]; then
echo "- Session Key: $SESSION_KEY"
fi
echo "- Protocol: Model Context Protocol (MCP) JSON-RPC 2.0 with SHA-256 CAS verification."
echo "- Concurrency Invariant: Optimistic concurrency control. Every \`read_file\` returns the file's current \`sha256\`. Every \`write_file\` and \`edit_file\` MUST supply \`expected_sha256\`. If the file was changed concurrently, the bridge aborts with \`source_changed\`."
echo "- Idempotency: Retries with the same \`execution_id\` or \`change_id\` observe the original receipt and never replay."
echo ""
echo "Active Tools:"
echo "1. \`get_capabilities\`: Inspect active grants, limits, and workspace identity."
echo "2. \`inspect_workspace(depth=2, include_hashes=True)\`: List directories or search contents."
echo "3. \`read_file(path, offset=0, limit=1000)\`: Read file page and retrieve SHA-256 hash."
echo "4. \`write_file(path, content, expected_sha256)\`: Overwrite file (use expected_sha256=\"\" for new file)."
echo "5. \`edit_file(path, old_text, new_text, expected_sha256)\`: Contiguous string replacement."
echo "6. \`run_command(command, execution_id)\`: Execute shell command if granted."
echo ""
echo "Workflow:"
echo "1. First, inspect workspace using \`inspect_workspace\` to discover project structure autonomously."
echo "2. Read target files with \`read_file\` to verify content and obtain the SHA-256 checksum."
echo "3. Perform edits or create files using \`edit_file\` / \`write_file\` with verified \`expected_sha256\`."
echo "4. Execute required test or build commands with \`run_command\` and verify execution receipt."
echo "5. Provide an audited nano-airbridge execution receipt in your final response."
echo "--------------------------------------------------------------------------------"
echo ""
fi

case "$MODE" in
    gateway)
        echo "Starting HTTP/MCP Gateway on port $PORT..."
        "$PYTHON_CMD" "$SCRIPT_DIR/nano_http_server.py" \
            --root "$ROOT_DIR" \
            --port "$PORT" \
            --enable-writes \
            --enable-execution
        ;;
    agent)
        echo "Connecting to Cloud Relay via outbound long-poll:"
        echo " Relay:   $RELAY_URL"
        echo " Session: $SESSION_KEY"
        echo " No open ports or ngrok needed."
        exec "$PYTHON_CMD" "$SCRIPT_DIR/relay/nano_agent.py" \
            --relay "$RELAY_URL" \
            --session "$SESSION_KEY" \
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
