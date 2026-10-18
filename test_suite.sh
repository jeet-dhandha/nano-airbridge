#!/usr/bin/env bash
# test_suite.sh — Complete test suite for standalone Go nano-airbridge binary
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$SCRIPT_DIR/bin/nano-airbridge"
TEST_WS="/tmp/airbridge_test_workspace_$$"
PORT=8977

rm -rf "$TEST_WS"
mkdir -p "$TEST_WS"
echo "hello nano airbridge" > "$TEST_WS/sample.txt"

cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$TEST_WS"
}
trap cleanup EXIT INT TERM

echo "========================================================"
echo " nano-airbridge Go Standalone Binary Test Suite"
echo "========================================================"

if [[ ! -x "$BIN" ]]; then
    echo "Binary not found at $BIN. Building..."
    "$SCRIPT_DIR/build.sh"
fi

# 1. Start Server
"$BIN" -root "$TEST_WS" -port "$PORT" -enable-writes -enable-execution &
SERVER_PID=$!
sleep 1

# Helper for JSON-RPC POST
rpc() {
    curl -sS -X POST "http://127.0.0.1:$PORT/mcp" \
        -H "Content-Type: application/json" \
        -d "$1"
}

echo " [1/10] Testing /healthz..."
HEALTH=$(curl -sS "http://127.0.0.1:$PORT/healthz")
if [[ "$HEALTH" != *"nano-airbridge-go"* ]]; then
    echo "FAIL: Unexpected health response: $HEALTH" >&2
    exit 1
fi
echo "  ✓ Health check passed: $HEALTH"

echo " [2/10] Testing /inspect..."
INSPECT=$(curl -sS "http://127.0.0.1:$PORT/inspect")
if [[ "$INSPECT" != *"sample.txt"* ]]; then
    echo "FAIL: sample.txt not found in inspect output: $INSPECT" >&2
    exit 1
fi
echo "  ✓ Inspect workspace passed"

echo " [3/10] Testing MCP initialize..."
INIT=$(rpc '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
if [[ "$INIT" != *"nano-airbridge"* ]]; then
    echo "FAIL: Initialize response invalid: $INIT" >&2
    exit 1
fi
echo "  ✓ MCP initialize passed"

echo " [4/10] Testing tools/list..."
TLIST=$(rpc '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')
if [[ "$TLIST" != *"read_file"* || "$TLIST" != *"write_file"* || "$TLIST" != *"inspect_workspace"* ]]; then
    echo "FAIL: Tools list missing expected tools: $TLIST" >&2
    exit 1
fi
echo "  ✓ tools/list returned complete tool catalog"

echo " [5/10] Testing read_file with SHA-256 CAS..."
READ_RES=$(rpc '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"read_file","arguments":{"path":"sample.txt"}}}')
if [[ "$READ_RES" != *"hello nano airbridge"* ]]; then
    echo "FAIL: read_file content mismatch: $READ_RES" >&2
    exit 1
fi
ORIG_SHA=$(echo "$READ_RES" | grep -o '[0-9a-f]\{64\}' | head -n 1 || true)
if [[ -z "$ORIG_SHA" ]]; then
    echo "FAIL: Missing SHA-256 in read_file response: $READ_RES" >&2
    exit 1
fi
echo "  ✓ read_file passed (SHA-256: $ORIG_SHA)"

echo " [6/10] Testing write_file CAS precondition..."
# Test writing a new file with expected_sha256 = ""
WRITE_RES=$(rpc '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"write_file","arguments":{"path":"new_doc.txt","content":"created by test","expected_sha256":""}}}')
if [[ "$WRITE_RES" != *"written"* || "$WRITE_RES" != *"true"* ]]; then
    echo "FAIL: write_file failed: $WRITE_RES" >&2
    exit 1
fi
if [[ ! -f "$TEST_WS/new_doc.txt" ]]; then
    echo "FAIL: File was not created on disk" >&2
    exit 1
fi
echo "  ✓ write_file created file on disk under empty CAS precondition"

echo " [7/10] Testing edit_file..."
EDIT_RES=$(rpc "{\"jsonrpc\":\"2.0\",\"id\":5,\"method\":\"tools/call\",\"params\":{\"name\":\"edit_file\",\"arguments\":{\"path\":\"sample.txt\",\"old_text\":\"hello nano airbridge\",\"new_text\":\"hello edited airbridge\",\"expected_sha256\":\"$ORIG_SHA\"}}}")
if [[ "$EDIT_RES" != *"edited"* || "$EDIT_RES" != *"true"* ]]; then
    echo "FAIL: edit_file failed: $EDIT_RES" >&2
    exit 1
fi
EDITED_CONTENT=$(cat "$TEST_WS/sample.txt")
if [[ "$EDITED_CONTENT" != *"hello edited airbridge"* ]]; then
    echo "FAIL: File content was not updated: $EDITED_CONTENT" >&2
    exit 1
fi
echo "  ✓ edit_file successfully replaced string with verified hash"

echo " [8/10] Testing CAS concurrency conflict rejection (source_changed)..."
# Try editing again with the OLD hash (which should fail now)
STALE_EDIT=$(rpc "{\"jsonrpc\":\"2.0\",\"id\":6,\"method\":\"tools/call\",\"params\":{\"name\":\"edit_file\",\"arguments\":{\"path\":\"sample.txt\",\"old_text\":\"hello edited airbridge\",\"new_text\":\"conflict\",\"expected_sha256\":\"$ORIG_SHA\"}}}")
if [[ "$STALE_EDIT" != *"source_changed"* ]]; then
    echo "FAIL: Stale CAS edit was not rejected: $STALE_EDIT" >&2
    exit 1
fi
echo "  ✓ Stale CAS hash correctly rejected with source_changed error"

echo " [9/10] Testing run_command..."
CMD_RES=$(rpc '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"run_command","arguments":{"command":"echo test_exec_output"}}}')
if [[ "$CMD_RES" != *"test_exec_output"* ]]; then
    echo "FAIL: run_command output missing: $CMD_RES" >&2
    exit 1
fi
echo "  ✓ run_command executed shell command and returned stdout"

echo " [10/10] Testing path confinement security..."
TRAVERSAL=$(rpc '{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"read_file","arguments":{"path":"../etc/passwd"}}}')
if [[ "$TRAVERSAL" != *"denied"* ]]; then
    echo "FAIL: Path traversal was not denied: $TRAVERSAL" >&2
    exit 1
fi
CREDENTIAL=$(rpc '{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"read_file","arguments":{"path":".env"}}}')
if [[ "$CREDENTIAL" != *"denied"* ]]; then
    echo "FAIL: Credential file was not denied: $CREDENTIAL" >&2
    exit 1
fi
echo "  ✓ Directory traversal (../) and credential paths (.env) strictly denied"

echo ""
echo "========================================================"
echo " ★ ALL 10 TESTS PASSED (100% SUCCESS) — ZERO PYTHON, ZERO PHP"
echo "========================================================"
