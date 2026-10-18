#!/usr/bin/env bash
# build.sh — Build standalone Go binaries for nano-airbridge
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"
SRC="$SCRIPT_DIR/src/go/main.go"

mkdir -p "$BIN_DIR"

TARGET="${1:-host}"

if ! command -v go &>/dev/null; then
    echo "Error: Go compiler is not installed." >&2
    echo "Install Go from https://go.dev/dl/ (e.g. brew install go or apt install golang)" >&2
    exit 1
fi

echo "=================================================="
echo " Building nano-airbridge Go Standalone Binaries"
echo "=================================================="

build_target() {
    local os="$1"
    local arch="$2"
    local output="$3"
    echo "  • Building for $os/$arch -> $output"
    CGO_ENABLED=0 GOOS="$os" GOARCH="$arch" go build -ldflags="-s -w" -o "$BIN_DIR/$output" "$SRC"
    chmod +x "$BIN_DIR/$output" 2>/dev/null || true
}

if [[ "$TARGET" == "all" ]]; then
    echo "Building all release targets:"
    build_target "darwin"  "arm64" "nano-airbridge"
    build_target "darwin"  "amd64" "nano-airbridge-darwin-amd64"
    build_target "linux"   "amd64" "nano-airbridge-linux-amd64"
    build_target "linux"   "arm64" "nano-airbridge-linux-arm64"
    build_target "windows" "amd64" "nano-airbridge.exe"
else
    HOST_OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
    HOST_ARCH="$(uname -m)"
    if [[ "$HOST_ARCH" == "x86_64" ]]; then
        GO_ARCH="amd64"
    elif [[ "$HOST_ARCH" == "aarch64" || "$HOST_ARCH" == "arm64" ]]; then
        GO_ARCH="arm64"
    else
        GO_ARCH="amd64"
    fi

    if [[ "$HOST_OS" == "darwin" ]]; then
        if [[ "$GO_ARCH" == "arm64" ]]; then
            build_target "darwin" "arm64" "nano-airbridge"
        else
            build_target "darwin" "amd64" "nano-airbridge-darwin-amd64"
            # Also symlink or copy to nano-airbridge
            cp "$BIN_DIR/nano-airbridge-darwin-amd64" "$BIN_DIR/nano-airbridge"
        fi
    elif [[ "$HOST_OS" == "linux" ]]; then
        build_target "linux" "$GO_ARCH" "nano-airbridge-linux-$GO_ARCH"
        cp "$BIN_DIR/nano-airbridge-linux-$GO_ARCH" "$BIN_DIR/nano-airbridge"
    else
        build_target "$HOST_OS" "$GO_ARCH" "nano-airbridge"
    fi
fi

echo "=================================================="
echo " Build successful! Binaries placed in ./bin/:"
ls -lh "$BIN_DIR"
echo "=================================================="
