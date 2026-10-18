#!/usr/bin/env bash
# nano-airbridge — One-click launcher for macOS and Linux (Pure Go Standalone Binary)
# Zero Python • Zero PHP • Zero Node Runtime Dependencies
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"

detect_native_binary() {
    local os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    local arch="$(uname -m)"
    local bin=""
    if [[ "$os" == "darwin" ]]; then
        if [[ "$arch" == "arm64" ]]; then
            bin="$BIN_DIR/nano-airbridge"
        else
            bin="$BIN_DIR/nano-airbridge-darwin-amd64"
        fi
    elif [[ "$os" == "linux" ]]; then
        if [[ "$arch" == "aarch64" || "$arch" == "arm64" ]]; then
            bin="$BIN_DIR/nano-airbridge-linux-arm64"
        else
            bin="$BIN_DIR/nano-airbridge-linux-amd64"
        fi
    fi
    if [[ -n "$bin" && -x "$bin" ]]; then
        echo "$bin"
        return 0
    fi
    if [[ -x "$BIN_DIR/nano-airbridge" ]]; then
        echo "$BIN_DIR/nano-airbridge"
        return 0
    fi
    return 1
}

NATIVE_BIN="$(detect_native_binary 2>/dev/null || true)"

# If binary is not compiled yet, auto-build if Go is present
if [[ -z "$NATIVE_BIN" ]]; then
    if command -v go &>/dev/null; then
        echo "⚡ Building native standalone binary with Go..."
        "$SCRIPT_DIR/build.sh"
        NATIVE_BIN="$(detect_native_binary 2>/dev/null || true)"
    fi
fi

if [[ -z "$NATIVE_BIN" ]]; then
    echo "================================================================================" >&2
    echo " ⚠️  Precompiled standalone binary not found in ./bin/" >&2
    echo "--------------------------------------------------------------------------------" >&2
    echo " To compile your own binary with Go (takes 2 seconds):" >&2
    echo "   ./build.sh" >&2
    echo "" >&2
    echo " Or install Go from https://go.dev/dl/ (brew install go / apt install golang)" >&2
    echo "================================================================================" >&2
    exit 1
fi

ROOT_DIR=""
RELAY_URL=""
AGENT_TOKEN="${AGENT_TOKEN:-airbridge_agent_token_112233}"
SESSION_KEY="${SESSION_KEY:-}"
PORT="${PORT:-8899}"
USE_TUNNEL="${USE_TUNNEL:-true}"
MODE=""

# Parse flags and arguments
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
        gateway|agent)
            MODE="$1"
            shift
            ;;
        -h|--help)
            echo "Usage: ./start.sh [workspace_path] [options]"
            echo ""
            echo "Options:"
            echo "  --no-tunnel       Disable localtunnel (bind strictly to localhost)"
            echo "  --relay <URL>     Connect to cloud relay URL (optional)"
            echo "  --session <ID>    Unique session key for workspace isolation"
            echo "  --port <PORT>     Port for local HTTP gateway (default: 8899)"
            echo ""
            echo "Examples:"
            echo "  ./start.sh demo_workspace"
            echo "  ./start.sh /path/to/project --port 9000"
            echo "  ./start.sh demo_workspace --no-tunnel"
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

# Auto-discover npx in standard paths or version managers
resolve_npx() {
    if command -v npx &>/dev/null; then
        return 0
    fi
    local candidates=(
        "/opt/homebrew/bin/npx"
        "/usr/local/bin/npx"
        "/opt/local/bin/npx"
        "$HOME/.local/share/fnm/current/bin/npx"
        "$HOME/.fnm/current/bin/npx"
        "$HOME/.volta/bin/npx"
        "$HOME/.asdf/shims/npx"
        "/snap/bin/npx"
    )
    for c in "${candidates[@]}"; do
        if [[ -x "$c" ]]; then
            export PATH="$(dirname "$c"):$PATH"
            return 0
        fi
    done
    if [[ -d "$HOME/.nvm/versions/node" ]]; then
        local latest_nvm
        latest_nvm="$(ls -1d "$HOME"/.nvm/versions/node/*/bin/npx 2>/dev/null | sort -V | tail -n 1 || true)"
        if [[ -n "$latest_nvm" && -x "$latest_nvm" ]]; then
            export PATH="$(dirname "$latest_nvm"):$PATH"
            return 0
        fi
    fi
    return 1
}

# Display OS-specific official package manager installation instructions
show_node_install_guide() {
    echo "================================================================================"
    echo " ⚠️  Node.js & npx are not installed (needed to launch free localtunnel)."
    echo "--------------------------------------------------------------------------------"
    echo " To enable free public tunneling for ChatGPT / Claude (zero accounts required):"
    echo ""
    local found_pm=false
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo " [Platform: macOS / MacBook]"
        if command -v brew &>/dev/null; then
            echo "   • Homebrew (Detected):     brew install node"
            found_pm=true
        fi
        if command -v port &>/dev/null; then
            echo "   • MacPorts (Detected):     sudo port install nodejs npm"
            found_pm=true
        fi
        if [[ "$found_pm" == "false" ]]; then
            echo "   • Fast No-Root Install:    curl -fsSL https://fnm.vercel.app/install | bash && fnm install --lts"
            echo "   • Official PKG Installer:  https://nodejs.org/"
        fi
    else
        echo " [Platform: Linux / Unix]"
        if command -v apt-get &>/dev/null || command -v apt &>/dev/null; then
            echo "   • Ubuntu / Debian (apt):   sudo apt update && sudo apt install -y nodejs npm"
            echo "   • NodeSource (LTS Repo):   curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt install -y nodejs"
            found_pm=true
        fi
        if command -v dnf &>/dev/null; then
            echo "   • Fedora / RHEL (dnf):     sudo dnf install -y nodejs npm"
            found_pm=true
        elif command -v yum &>/dev/null; then
            echo "   • CentOS / RHEL (yum):     sudo yum install -y nodejs npm"
            found_pm=true
        fi
        if command -v pacman &>/dev/null; then
            echo "   • Arch Linux (pacman):     sudo pacman -S nodejs npm"
            found_pm=true
        fi
        if command -v zypper &>/dev/null; then
            echo "   • openSUSE (zypper):       sudo zypper install nodejs npm"
            found_pm=true
        fi
        if command -v apk &>/dev/null; then
            echo "   • Alpine Linux (apk):      sudo apk add nodejs npm"
            found_pm=true
        fi
        if command -v pkg &>/dev/null; then
            echo "   • FreeBSD (pkg):           sudo pkg install node npm"
            found_pm=true
        fi
        if [[ "$found_pm" == "false" ]]; then
            echo "   • Fast No-Root Install:    curl -fsSL https://fnm.vercel.app/install | bash && fnm install --lts"
            echo "   • Official Downloads:      https://nodejs.org/en/download"
        fi
    fi
    echo ""
    echo " Note: Once installed, simply re-run ./start.sh and localtunnel will launch automatically."
    echo "--------------------------------------------------------------------------------"
    echo " Continuing with local gateway on http://localhost:${PORT}/mcp"
    echo "================================================================================"
}

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
    if [[ -z "$SESSION_KEY" ]]; then
        SESSION_KEY="$(openssl rand -hex 8 2>/dev/null || LC_ALL=C tr -dc 'a-f0-9' < /dev/urandom 2>/dev/null | head -c 16 || echo "sess_$(date +%s)")"
    fi
    MCP_ENDPOINT="${RELAY_URL}?op=mcp&s=${SESSION_KEY}"
elif [[ "$MODE" == "gateway" ]]; then
    if command -v curl &>/dev/null; then
        NGROK_URL=$(curl -s --connect-timeout 0.5 http://127.0.0.1:4040/api/tunnels 2>/dev/null | grep -o 'https://[^"]*ngrok[^"]*' 2>/dev/null | head -n 1 || true)
    fi

    if [[ -n "$NGROK_URL" ]]; then
        MCP_ENDPOINT="${NGROK_URL}/mcp"
    elif [[ "$USE_TUNNEL" != "false" ]]; then
        if ! resolve_npx; then
            show_node_install_guide
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
echo " nano-airbridge — Standalone Go MCP Runtime"
echo " Zero Python • Zero PHP • Zero Node Dependencies"
echo "================================================================================"
echo " Workspace:     $ROOT_DIR"
echo " Engine:        Native Standalone Binary ($(basename "$NATIVE_BIN"))"
if [[ -n "$TUNNEL_URL" ]]; then
echo " Public Tunnel: $TUNNEL_URL (localtunnel)"
elif [[ -n "$NGROK_URL" ]]; then
echo " Public Tunnel: $NGROK_URL (ngrok)"
fi
echo " MCP Endpoint:  $MCP_ENDPOINT"
echo " Local Port:    $PORT"
echo "================================================================================"
echo ""
echo "📋 COPY-PASTE SYSTEM INSTRUCTION FOR CHATGPT / CLAUDE / KIMI:"
echo "--------------------------------------------------------------------------------"
echo "[SYSTEM INSTRUCTION: NANO-AIRBRIDGE WORKSPACE INTEGRATION]"
echo "You are connected to the operator's local project workspace via nano-airbridge."
echo "- Bridge Endpoint: $MCP_ENDPOINT"
echo "- Help & Discovery: ${TUNNEL_URL:-${NGROK_URL:-http://localhost:${PORT}}}/help"
echo "- Protocol: Model Context Protocol (MCP) JSON-RPC 2.0 with SHA-256 CAS verification."
echo "- Concurrency Invariant: Optimistic concurrency control. Every \`read_file\` returns the file's current \`sha256\`. Every \`write_file\` and \`edit_file\` MUST supply \`expected_sha256\`. If the file was changed concurrently, the bridge aborts with \`source_changed\`."
echo ""
echo "Active Tools & Paths:"
echo "0. \`help\`: Discover all available endpoints, tools, and execution grants (GET /help)."
echo "1. \`inspect_workspace\`: List workspace files (GET /inspect or MCP)."
echo "2. \`read_file(path)\`: Read file contents and retrieve whole-file SHA-256 checksum (GET /read/<path> or MCP)."
echo "3. \`write_file(path, content, expected_sha256)\`: Create or overwrite file under CAS precondition."
echo "4. \`edit_file(path, old_text, new_text, expected_sha256)\`: Contiguous text replacement."
echo "5. \`run_command(command)\`: Execute shell command inside workspace root."
echo ""
echo "Workflow:"
echo "1. Inspect workspace to discover files: inspect_workspace."
echo "2. Read target file to get content and SHA-256 hash: read_file(path)."
echo "3. Update files with verified hash: write_file / edit_file."
echo "4. Execute required test or build commands: run_command."
echo "--------------------------------------------------------------------------------"
echo ""

if [[ "$MODE" == "agent" ]]; then
    exec "$NATIVE_BIN" \
        -root "$ROOT_DIR" \
        -relay "$RELAY_URL" \
        -token "$AGENT_TOKEN" \
        -enable-writes \
        -enable-execution
else
    exec "$NATIVE_BIN" \
        -root "$ROOT_DIR" \
        -port "$PORT" \
        -enable-writes \
        -enable-execution
fi
