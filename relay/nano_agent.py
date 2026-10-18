#!/usr/bin/env python3
"""nano-airbridge relay agent — the per-user bridge that replaces ngrok.

Runs on the user's machine, next to nano_airbridge.py (or any MCP stdio server).
It makes OUTBOUND-ONLY HTTPS calls to the free-hosted relay.php:
registers the tool catalog, long-polls for queued MCP requests, feeds them to
the local server's stdin, and posts the JSON-RPC responses back.

    nano_agent.py --relay https://free-host.example/relay.php \
                   --token AGENT_TOKEN \
                   --command "python3 /path/nano_airbridge.py --root /path/to/project --enable-writes"

No inbound ports, no tunnel binary, no root. Python 3.11+ standard library only.
The local server's grants (--enable-writes / --enable-execution) remain the real
safety boundary: this agent forwards bytes and never executes anything itself.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

AGENT_VERSION = "0.1.0"


def log(message: str) -> None:
    print(f"[nano-agent] {message}", file=sys.stderr, flush=True)


def post_json(url: str, token: str, payload: dict, timeout: float = 30.0) -> dict | None:
    sep = "&" if "?" in url else "?"
    url_with_t = f"{url}{sep}t={token}" if token else url
    request = urllib.request.Request(
        url_with_t, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}", "X-Agent-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:200]
        raise RuntimeError(f"HTTP {error.code} from relay: {detail}") from error


def get_json(url: str, token: str, timeout: float = 40.0) -> dict | None:
    sep = "&" if "?" in url else "?"
    url_with_t = f"{url}{sep}t={token}" if token else url
    request = urllib.request.Request(
        url_with_t,
        headers={"Authorization": f"Bearer {token}", "X-Agent-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:200]
        raise RuntimeError(f"HTTP {error.code} from relay: {detail}") from error


class LocalServer:
    """One MCP stdio subprocess with request/response correlation on ids."""

    def __init__(self, command: list[str]):
        self.command = command
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        log(f"starting local MCP server: {shlex.join(self.command)}")
        self.proc = subprocess.Popen(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "relay-agent", "version": AGENT_VERSION}})
        self._notify("notifications/initialized", {})

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _write(self, message: dict) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict, msg_id: int | str = 0) -> dict:
        self._write({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        return self._read_response(msg_id)

    def _read_response(self, msg_id: int | str) -> dict:
        assert self.proc is not None and self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("local MCP server closed its stdout")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == msg_id:
                return message

    def list_tools(self) -> list[dict]:
        response = self._request("tools/list", {})
        return (response.get("result") or {}).get("tools", [])

    def forward(self, request: dict) -> dict:
        """Send one queued JSON-RPC request; return the server's response object."""
        msg_id = request.get("internal_id") or request.get("id")
        self._write({"jsonrpc": "2.0", "id": msg_id,
                     "method": request.get("method", ""), "params": request.get("params") or {}})
        response = self._read_response(msg_id)
        return {"jsonrpc": "2.0", "id": msg_id,
                **({"result": response["result"]} if "result" in response
                   else {"error": response.get("error", {"code": -32603, "message": "empty response"})})}


def register(relay: str, token: str, server: LocalServer, session: str = "") -> None:
    tools = server.list_tools()
    reg_url = f"{relay}?op=register" + (f"&session={session}" if session else "")
    result = post_json(reg_url, token, {
        "tools": tools, "agent_version": AGENT_VERSION, "session": session,
        "server": {"command": server.command[0], "pid": server.proc.pid if server.proc else None}})
    log(f"registered {result.get('tool_count', len(tools))} tools with relay" + (f" (session: {session})" if session else ""))


def main() -> int:
    parser = argparse.ArgumentParser(prog="nano-agent")
    parser.add_argument("--relay", required=True, help="URL of relay.php (https://host/path/relay.php)")
    parser.add_argument("--session", default="", help="Session ID / key for workspace isolation (or auto-generated)")
    parser.add_argument("--token", default="airbridge_agent_token_112233", help="Agent token (or env NANO_RELAY_AGENT_TOKEN)")
    parser.add_argument("--command", required=True,
                        help="MCP stdio server command, e.g. 'python3 nano_airbridge.py --root /project'")
    parser.add_argument("--poll-wait", type=int, default=0,
                        help="Override relay poll wait seconds (0 = use relay's own)")
    args = parser.parse_args()
    token = args.token or os.environ.get("NANO_RELAY_AGENT_TOKEN", "airbridge_agent_token_112233")
    session = args.session or os.environ.get("NANO_SESSION_KEY") or secrets.token_hex(8)
    def stop_on_sigterm(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_sigterm)
    relay = args.relay.split("?")[0].rstrip("/")  # keep the base relay.php URL
    server = LocalServer(shlex.split(args.command))
    backoff = 1.0
    while True:
        try:
            server.start()
            register(relay, token, server, session)
            backoff = 1.0
            while True:
                poll_url = f"{relay}?op=poll" + (f"&session={session}" if session else "")
                if args.poll_wait:
                    poll_url += f"&wait={args.poll_wait}"
                batch = get_json(poll_url, token)
                for request in (batch or {}).get("requests", []):
                    internal = request.get("internal_id", "?")
                    log(f"forwarding {request.get('method')} ({internal})")
                    try:
                        response = server.forward(request)
                    except Exception as error:  # subprocess died mid-request
                        log(f"local server failed: {error}; reporting bounded failure")
                        response = {"jsonrpc": "2.0", "id": internal, "result": {
                            "content": [{"type": "text", "text": json.dumps({
                                "error": {"code": "local_server_error",
                                          "message": "The local MCP server stopped before answering."}})}],
                            "structuredContent": {"error": {"code": "local_server_error",
                                                            "message": "The local MCP server stopped before answering."},
                                                  "effect": "effect_unknown"},
                            "isError": True}}
                        try:
                            server.proc.kill()
                        except Exception:
                            pass
                    resp_url = f"{relay}?op=response" + (f"&session={session}" if session else "")
                    post_json(resp_url, token, response)
                if not server.alive():
                    raise RuntimeError("local MCP server exited")
        except KeyboardInterrupt:
            log("stopping")
            try:
                if server.proc:
                    server.proc.terminate()
            except Exception:
                pass
            return 0
        except Exception as error:
            log(f"disconnected ({type(error).__name__}: {error}); retrying in {backoff:.0f}s")
            try:
                time.sleep(backoff)
            except KeyboardInterrupt:  # SIGTERM can land inside the retry sleep
                log("stopping")
                try:
                    if server.proc:
                        server.proc.terminate()
                except Exception:
                    pass
                return 0
            backoff = min(backoff * 2, 30.0)


if __name__ == "__main__":
    sys.exit(main())
