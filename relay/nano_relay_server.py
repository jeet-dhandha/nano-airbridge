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


class SessionState:
    def __init__(self, session_id: str):
        self.session_id = session_id
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


class RelayState:
    def __init__(self, client_secret: str, agent_token: str):
        self.client_secret = client_secret
        self.agent_token = agent_token
        self.global_lock = threading.Lock()
        self.default_session = SessionState("default")
        self.sessions: dict[str, SessionState] = {}

    def get_session(self, session_id: str, create: bool = False) -> SessionState:
        if not session_id or session_id == "default":
            return self.default_session
        with self.global_lock:
            if session_id not in self.sessions:
                if create:
                    self.sessions[session_id] = SessionState(session_id)
                else:
                    return self.default_session
            return self.sessions[session_id]

    def resolve_mcp_session(self, secret: str, session_hint: str = "") -> SessionState | None:
        with self.global_lock:
            if secret in self.sessions:
                return self.sessions[secret]
            if secret == self.client_secret:
                if session_hint and session_hint in self.sessions:
                    return self.sessions[session_hint]
                if self.default_session.cached_tools:
                    return self.default_session
                if self.sessions:
                    return max(self.sessions.values(), key=lambda s: s.agent_last_seen or 0)
                return self.default_session
        return None


class RelayHandler(BaseHTTPRequestHandler):
    state: RelayState

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Agent-Token, X-MCP-Secret, X-Session-ID")
        self.end_headers()

    def _parse_params(self) -> tuple[str, str, str, str]:
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
        session = qs.get("session", [""])[0] or self.headers.get("X-Session-ID", "")
        return op, secret, token, session

    def do_GET(self):
        op, secret, token, session = self._parse_params()
        if op == "healthz" or self.path == "/healthz":
            with self.state.global_lock:
                if session and session in self.state.sessions:
                    sess = self.state.sessions[session]
                    is_online = sess.is_agent_online()
                    is_registered = len(sess.cached_tools) > 0
                    last_seen = sess.agent_last_seen
                    sess_id = sess.session_id
                else:
                    all_sessions = list(self.state.sessions.values()) + [self.state.default_session]
                    is_online = any(s.is_agent_online() for s in all_sessions)
                    is_registered = any(len(s.cached_tools) > 0 for s in all_sessions)
                    last_seens = [s.agent_last_seen for s in all_sessions if s.agent_last_seen is not None]
                    last_seen = max(last_seens) if last_seens else None
                    sess_id = "any" if self.state.sessions else "default"

            resp = {
                "relay": "nano-airbridge-relay-python",
                "version": RELAY_VERSION,
                "session": sess_id,
                "agent_registered": is_registered,
                "agent_last_seen": last_seen,
                "agent_online": is_online
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

            sess = self.state.get_session(session, create=True)
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            wait_sec = min(25, int(qs.get("wait", [20])[0]))

            with sess.lock:
                sess.agent_last_seen = time.time()
                if not sess.queue:
                    sess.lock.wait(timeout=wait_sec)
                batch = list(sess.queue)
                sess.queue.clear()

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
        op, secret, token, session = self._parse_params()
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

            sess_id = session or body.get("session", "")
            sess = self.state.get_session(sess_id, create=True)
            with sess.lock:
                sess.cached_tools = body.get("tools", [])
                if "server" in body:
                    sess.server_info.update(body["server"])
                sess.agent_last_seen = time.time()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "tool_count": len(sess.cached_tools), "session": sess.session_id}).encode())
            return

        if op == "response" or self.path.startswith("/response"):
            if token != self.state.agent_token:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"bad_agent_token"}\n')
                return

            sess = self.state.get_session(session)
            req_id = str(body.get("internal_id") or body.get("id") or "")
            with sess.lock:
                sess.agent_last_seen = time.time()
                sess.responses[req_id] = body.get("response", body)
                evt = sess.response_events.get(req_id)
                if evt:
                    evt.set()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"ok":true}\n')
            return

        if op == "mcp" or self.path.startswith("/mcp"):
            sess = self.state.resolve_mcp_session(secret, session)
            if sess is None:
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
                        "serverInfo": sess.server_info,
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
                    "result": {"tools": sess.cached_tools}
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode())
                return

            # If agent is not online, fail fast with structured content
            if not sess.is_agent_online():
                res = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "{\"error\":{\"code\":\"agent_offline\",\"message\":\"The local agent is not connected.\"}}"}],
                        "structuredContent": {"error": {"code": "agent_offline", "message": "The local agent is not connected to the relay."}, "effect": "known_no_effect"}
                    }
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
            with sess.lock:
                sess.response_events[internal_id] = evt
                sess.queue.append(queued_msg)
                sess.lock.notify()

            # Wait for local agent to execute and respond
            got = evt.wait(timeout=25.0)
            with sess.lock:
                sess.response_events.pop(internal_id, None)
                reply = sess.responses.pop(internal_id, None)

            if not got or reply is None:
                res = {
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "{\"error\":{\"code\":\"agent_timeout\",\"message\":\"The local agent did not respond within 25 seconds.\"}}"}],
                        "structuredContent": {"error": {"code": "agent_timeout", "message": "The local agent did not respond in time."}, "effect": "effect_unknown"}
                    }
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode())
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
