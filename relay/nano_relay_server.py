#!/usr/bin/env python3
"""nano_relay_server.py — Zero-dependency Python cloud relay for nano-airbridge.

Pure Python 3 stdlib (no pip, no PHP, no binaries).
Provides the exact same protocol as nano_relay.php:
- Client (ChatGPT / Kimi / Claude) sends JSON-RPC to ?op=mcp
- Local agent (nano_agent.py) long-polls ?op=poll from user's machine (outbound-only)
- No port forwarding, no ngrok, works behind strict firewalls and NAT.
- Cross-platform: runs on Linux, macOS, Windows, Docker, Render, Fly.io, Railway.

Usage:
    python3 nano_relay_server.py --port 8080 [--client-secret SECRET] [--agent-token TOKEN]
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import sys
import threading
import time
import urllib.parse

RELAY_VERSION = "0.1.0"


class RelayState:
    def __init__(self, client_secret: str, agent_token: str):
        self.client_secret = client_secret
        self.agent_token = agent_token
        self.cached_tools: list[dict] = []
        self.server_info: dict = {"name": "nano-airbridge", "version": "0.1.0"}
        self.agent_last_seen: float | None = None
        self.lock = threading.Condition()
        self.queue: list[dict] = []
        self.responses: dict[str, dict] = {}
        self.response_events: dict[str, threading.Event] = {}

    def is_agent_online(self) -> bool:
        if self.agent_last_seen is None:
            return False
        return (time.time() - self.agent_last_seen) < 30.0


class RelayHandler(BaseHTTPRequestHandler):
    state: RelayState

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Agent-Token, X-MCP-Secret")
        self.end_headers()

    def _parse_params(self) -> tuple[str, str, str]:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        op = qs.get("op", [""])[0]
        if not op:
            path_parts = [p for p in parsed.path.split("/") if p]
            if path_parts:
                op = path_parts[0]
        secret = qs.get("s", [""])[0] or self.headers.get("X-MCP-Secret", "")
        token = qs.get("t", [""])[0] or self.headers.get("X-Agent-Token", "")
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = token or auth[7:]
        return op, secret, token

    def do_GET(self):
        op, secret, token = self._parse_params()
        if op == "healthz" or self.path == "/healthz":
            resp = {
                "relay": "nano-airbridge-relay-python",
                "version": RELAY_VERSION,
                "agent_registered": len(self.state.cached_tools) > 0,
                "agent_last_seen": self.state.agent_last_seen,
                "agent_online": self.state.is_agent_online()
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode())
            return

        if op == "poll" or self.path.startswith("/poll"):
            if token != self.state.agent_token:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"bad_agent_token"}\n')
                return

            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            wait_sec = min(25, int(qs.get("wait", [20])[0]))

            with self.state.lock:
                self.state.agent_last_seen = time.time()
                if not self.state.queue:
                    self.state.lock.wait(timeout=wait_sec)
                batch = list(self.state.queue)
                self.state.queue.clear()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "requests": batch}).encode())
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"nano-airbridge Python Relay is running. Connect nano_agent.py to ?op=poll\n")

    def do_POST(self):
        op, secret, token = self._parse_params()
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8", errors="replace")
        try:
            body = json.loads(raw_body) if raw_body else {}
        except Exception:
            body = {}

        if op == "register" or self.path.startswith("/register"):
            if token != self.state.agent_token:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"bad_agent_token"}\n')
                return

            with self.state.lock:
                self.state.cached_tools = body.get("tools", [])
                if "server" in body:
                    self.state.server_info.update(body["server"])
                self.state.agent_last_seen = time.time()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "tool_count": len(self.state.cached_tools)}).encode())
            return

        if op == "response" or self.path.startswith("/response"):
            if token != self.state.agent_token:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"bad_agent_token"}\n')
                return

            req_id = str(body.get("internal_id") or body.get("id") or "")
            with self.state.lock:
                self.state.agent_last_seen = time.time()
                self.state.responses[req_id] = body.get("response", body)
                evt = self.state.response_events.get(req_id)
                if evt:
                    evt.set()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"ok":true}\n')
            return

        if op == "mcp" or self.path.startswith("/mcp"):
            if secret != self.state.client_secret:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"bad_client_secret"}\n')
                return

            method = body.get("method", "")
            msg_id = body.get("id")

            # Answer initialize and tools/list immediately from cache
            if method == "initialize":
                res = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": self.state.server_info,
                        "instructions": "nano-airbridge relay bridge active."
                    }
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode())
                return

            if method == "tools/list":
                res = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": self.state.cached_tools}
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode())
                return

            # Forward tool calls to the long-polling local agent
            internal_id = secrets.token_hex(16)
            queued_msg = dict(body)
            queued_msg["internal_id"] = internal_id

            evt = threading.Event()
            with self.state.lock:
                self.state.response_events[internal_id] = evt
                self.state.queue.append(queued_msg)
                self.state.lock.notify()

            # Wait for local agent to execute and respond
            got = evt.wait(timeout=25.0)
            with self.state.lock:
                self.state.response_events.pop(internal_id, None)
                reply = self.state.responses.pop(internal_id, None)

            if not got or reply is None:
                self.send_response(504)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32000, "message": "Agent timeout: local agent did not respond within 25 seconds"}
                }).encode())
                return

            reply["id"] = msg_id
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(reply).encode())
            return

        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"unknown_operation"}\n')

    def log_message(self, format, *args):
        print(f"[nano-relay] {args[0]} - {args[1]}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Zero-dependency Python Cloud Relay for nano-airbridge")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default 8080)")
    parser.add_argument("--client-secret", type=str, default="airbridge_client_secret_998877", help="Client secret for MCP callers")
    parser.add_argument("--agent-token", type=str, default="airbridge_agent_token_112233", help="Token for local agent")
    args = parser.parse_args()

    state = RelayState(args.client_secret, args.agent_token)
    RelayHandler.state = state

    server = ThreadingHTTPServer(("0.0.0.0", args.port), RelayHandler)
    print(f"nano-airbridge Python Relay listening on port {args.port}")
    print(f"Client Secret: {args.client_secret}")
    print(f"Agent Token:   {args.agent_token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down relay...")
        server.server_close()


if __name__ == "__main__":
    main()
