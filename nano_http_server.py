#!/usr/bin/env python3
"""nano_http_server.py — Zero-dependency HTTP gateway for nano-airbridge.

Exposes nano_airbridge.py stdio MCP protocol over standard HTTP POST JSON-RPC.
Works out of the box with ngrok, Cloudflare tunnels, or direct LAN connections.

Usage:
    python3 nano_http_server.py --root /path/to/project --port 8765 [--enable-writes] [--enable-execution]
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading

import urllib.parse

SERVER_PATH = Path(__file__).resolve().parent / "nano_airbridge.py"


class BridgeHandler(BaseHTTPRequestHandler):
    proc: subprocess.Popen
    lock: threading.Lock

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","server":"nano-airbridge-http"}\n')
            return

        if self.path.startswith("/read/"):
            subpath = urllib.parse.unquote(self.path[6:].split("?")[0])
            rpc = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "read_file", "arguments": {"path": subpath}}
            }
            with self.lock:
                self.proc.stdin.write(json.dumps(rpc) + "\n")
                self.proc.stdin.flush()
                reply_line = self.proc.stdout.readline()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(reply_line.encode("utf-8"))
            return

        if self.path.startswith("/inspect"):
            rpc = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "inspect_workspace", "arguments": {"action": "list"}}
            }
            with self.lock:
                self.proc.stdin.write(json.dumps(rpc) + "\n")
                self.proc.stdin.flush()
                reply_line = self.proc.stdout.readline()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(reply_line.encode("utf-8"))
            return

        if self.path.startswith("/run"):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            cmd_str = qs.get("cmd", [""])[0]
            import secrets, shlex
            exec_id = qs.get("execution_id", [""])[0] or secrets.token_hex(16)
            argv = shlex.split(cmd_str) if cmd_str else ["ls", "-la"]
            rpc = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "run_command", "arguments": {"argv": argv, "cwd": ".", "execution_id": exec_id}}
            }
            with self.lock:
                self.proc.stdin.write(json.dumps(rpc) + "\n")
                self.proc.stdin.flush()
                reply_line = self.proc.stdout.readline()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(reply_line.encode("utf-8"))
            return

        if self.path.startswith("/write"):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            file_path = qs.get("path", [""])[0]
            content = qs.get("content", [""])[0]
            expected_sha = qs.get("expected_sha256", [""])[0]
            rpc = {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "write_file", "arguments": {"path": file_path, "content": content, "expected_sha256": expected_sha}}
            }
            with self.lock:
                self.proc.stdin.write(json.dumps(rpc) + "\n")
                self.proc.stdin.flush()
                reply_line = self.proc.stdout.readline()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(reply_line.encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"nano-airbridge HTTP gateway is running. Send POST JSON-RPC to /mcp, or GET /read/<path> /inspect /run /write\n")

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8", errors="replace")
        try:
            msg = json.loads(body)
        except Exception as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": str(e)}}).encode())
            return

        if self.path.startswith("/run"):
            import secrets, shlex
            cmd_str = msg.get("command") or msg.get("cmd", "")
            argv = msg.get("argv") or (shlex.split(cmd_str) if cmd_str else ["ls", "-la"])
            exec_id = msg.get("execution_id") or secrets.token_hex(16)
            rpc = {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "run_command", "arguments": {"argv": argv, "cwd": msg.get("cwd", "."), "execution_id": exec_id}}
            }
            with self.lock:
                self.proc.stdin.write(json.dumps(rpc) + "\n")
                self.proc.stdin.flush()
                reply_line = self.proc.stdout.readline()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(reply_line.encode("utf-8"))
            return

        if self.path.startswith("/write"):
            file_path = msg.get("path") or urllib.parse.unquote(self.path[7:].split("?")[0])
            content = msg.get("content", "")
            expected_sha = msg.get("expected_sha256", "")
            rpc = {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "write_file", "arguments": {"path": file_path, "content": content, "expected_sha256": expected_sha}}
            }
            with self.lock:
                self.proc.stdin.write(json.dumps(rpc) + "\n")
                self.proc.stdin.flush()
                reply_line = self.proc.stdout.readline()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(reply_line.encode("utf-8"))
            return

        method = msg.get("method")
        msg_id = msg.get("id")

        # Handle notifications (no response needed)
        if method and method.startswith("notifications/"):
            self.send_response(202)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return

        with self.lock:
            try:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
                reply_line = self.proc.stdout.readline()
                if not reply_line:
                    raise RuntimeError("nano_airbridge process returned empty response")
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(e)}}).encode())
                return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(reply_line.encode("utf-8"))

    def log_message(self, format, *args):
        # Keep logs concise
        print(f"[nano-http] {args[0]} - {args[1]}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Zero-dependency HTTP gateway for nano-airbridge")
    parser.add_argument("--root", type=str, required=True, help="Absolute path to workspace root")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port to listen on (default 8765)")
    parser.add_argument("--enable-writes", action="store_true", help="Grant workspace writes")
    parser.add_argument("--enable-execution", action="store_true", help="Grant command execution")
    args = parser.parse_args()

    sub_args = [sys.executable, str(SERVER_PATH), "--root", args.root]
    if args.enable_writes:
        sub_args.append("--enable-writes")
    if args.enable_execution:
        sub_args.append("--enable-execution")

    proc = subprocess.Popen(
        sub_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1
    )

    lock = threading.Lock()
    BridgeHandler.proc = proc
    BridgeHandler.lock = lock

    server = ThreadingHTTPServer(("127.0.0.1", args.port), BridgeHandler)
    print(f"nano-airbridge HTTP gateway listening on http://127.0.0.1:{args.port}/mcp")
    print(f"Workspace root: {args.root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
