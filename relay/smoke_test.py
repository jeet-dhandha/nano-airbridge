#!/usr/bin/env python3
"""End-to-end test of the relay chain: fake ChatGPT → nano_relay.php → agent → om_local.

Run:  python3 relay/smoke_test.py
Requires: php (any 8.x, for the relay) and python3 (agent + om_local). Exits 0 on success.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
NANO_AIRBRIDGE = HERE.parent / "nano_airbridge.py"
RELAY_PHP = HERE / "nano_relay.php"
AGENT = HERE / "nano_agent.py"

CLIENT_SECRET = "c" * 48
AGENT_TOKEN = "t" * 48


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def expect(condition, label):
    if not condition:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ok: {label}")


def rpc(url: str, method: str, params=None, msg_id=1) -> tuple[int, dict]:
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode() or "{}")


def rpc_notify(url: str, method: str) -> int:
    body = {"jsonrpc": "2.0", "method": method, "params": {}}
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status


def tool(url: str, name: str, msg_id: int, **arguments) -> tuple[dict, bool]:
    status, reply = rpc(url, "tools/call", {"name": name, "arguments": arguments}, msg_id)
    result = reply.get("result") or {}
    payload = result.get("structuredContent") or {}
    return payload, result.get("isError", False)


def main() -> int:
    if shutil.which("php") is None:
        print("SKIP: php not installed (needed to run nano_relay.php)")
        return 1
    base = Path(tempfile.mkdtemp(prefix="om-relay-test-"))
    project = base / "project"
    project.mkdir()
    (project / "README.md").write_text("relay connection check\n")
    relay_dir = base / "www"
    relay_dir.mkdir()
    shutil.copy(RELAY_PHP, relay_dir / "nano_relay.php")

    port = free_port()
    mcp_url = f"http://127.0.0.1:{port}/nano_relay.php?op=mcp&s={CLIENT_SECRET}"
    env = {**os.environ, "PHP_CLI_SERVER_WORKERS": "4",  # php -S must serve poll + mcp concurrently
           "OM_RELAY_CLIENT_SECRET": CLIENT_SECRET, "OM_RELAY_AGENT_TOKEN": AGENT_TOKEN,
           "OM_RELAY_MCP_WAIT": "8", "OM_RELAY_POLL_WAIT": "6"}
    php = subprocess.Popen(["php", "-S", f"127.0.0.1:{port}", "-t", str(relay_dir)],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    agent = None
    try:
        for _ in range(50):  # wait for php
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/nano_relay.php?op=healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.1)
        health = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/nano_relay.php?op=healthz", timeout=2).read())
        expect(health["agent_online"] is False, "healthz reports agent offline before start")

        status, reply = rpc(mcp_url, "initialize",
                            {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "smoke", "version": "0"}})
        expect(status == 200 and reply["result"]["serverInfo"]["name"] == "nano-airbridge-relay",
               "MCP initialize answered by relay")
        expect(rpc_notify(mcp_url, "notifications/initialized") == 202, "notification accepted (202)")

        status, reply = rpc(mcp_url, "tools/list")
        expect(reply["result"]["tools"] == [], "tools/list empty before agent registers")

        status, reply = rpc(mcp_url, "tools/call", {"name": "read_file", "arguments": {"path": "README.md"}})
        expect(reply["result"]["isError"] is True and
               reply["result"]["structuredContent"]["error"]["code"] == "agent_offline" and
               reply["result"]["structuredContent"]["effect"] == "known_no_effect",
               "tools/call with no agent → bounded agent_offline, known_no_effect")

        status, _ = rpc(mcp_url.replace(f"s={CLIENT_SECRET}", "s=wrong"), "tools/list")
        expect(status == 401, "wrong client secret rejected")

        # ---- start the agent on the user's "machine" ----------------------
        agent = subprocess.Popen([sys.executable, str(AGENT),
                                  "--relay", f"http://127.0.0.1:{port}/nano_relay.php",
                                  "--token", AGENT_TOKEN,
                                  "--command",
                                  f"{sys.executable} {NANO_AIRBRIDGE} --root {project} "
                                  f"--state-dir {base / 'state'} --enable-writes --enable-execution",
                                  "--poll-wait", "5"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            health = json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{port}/nano_relay.php?op=healthz", timeout=2).read())
            if health["agent_online"]:
                break
            time.sleep(0.25)
        expect(health["agent_online"] is True and health["agent_registered"] is True,
               "agent connected and registered")

        status, reply = rpc(mcp_url, "tools/list")
        names = [t["name"] for t in reply["result"]["tools"]]
        expect(len(names) == 9, f"tools/list served from agent registration ({len(names)} tools)")

        payload, err = tool(mcp_url, "read_file", 10, path="README.md")
        expect(not err and payload["text"] == "relay connection check\n",
               "read_file round-trips ChatGPT → relay → agent → om_local")
        sha = payload["sha256"]

        payload, err = tool(mcp_url, "write_file", 11, path="notes.txt", content="via relay\n",
                            expected_sha256="")
        expect(not err and payload["status"] == "published", "write_file through the relay")
        expect((project / "notes.txt").read_text() == "via relay\n", "write landed on the local disk")

        payload, err = tool(mcp_url, "run_command", 12, execution_id="a" * 32, cwd=".",
                            argv=["/bin/sh", "-c", "echo relayed-run"])
        expect(not err and payload["execution_id"] == "a" * 32, "run_command through the relay")
        payload, err = tool(mcp_url, "read_execution", 13, execution_id="a" * 32, wait_seconds=2)
        expect(payload["status"] == "succeeded" and "relayed-run" in payload["output"]["text"],
               "execution receipt + output through the relay")

        payload, err = tool(mcp_url, "read_file", 14, path="../../etc/passwd")
        expect(err and payload["error"]["code"] == "path_denied",
               "path confinement enforced on the local machine")

        # ---- agent goes offline -------------------------------------------
        agent.send_signal(signal.SIGTERM)
        agent.wait(timeout=5)
        time.sleep(0.5)
        # drain any poll-inflight noise by waiting one poll cycle
        time.sleep(1)
        status, reply = rpc(mcp_url, "tools/call", {"name": "read_file", "arguments": {"path": "README.md"}})
        expect(reply["result"]["isError"] is True and
               reply["result"]["structuredContent"]["error"]["code"] in ("agent_offline", "agent_timeout"),
               "agent shutdown → bounded failure for the next call")

        print("\nALL RELAY SMOKE TESTS PASSED")
        return 0
    finally:
        for proc in (agent, php):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
