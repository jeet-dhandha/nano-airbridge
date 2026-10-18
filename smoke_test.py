#!/usr/bin/env python3
"""End-to-end smoke test for nano_airbridge.py — drives the real stdio MCP protocol.

Run:  python3 smoke_test.py
Exits 0 on success. Creates a throwaway project + state dir under /tmp.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SERVER = HERE / "nano_airbridge.py"


class Client:
    def __init__(self, root: Path, state: Path, writes: bool, execution: bool):
        args = [sys.executable, str(SERVER), "--root", str(root), "--state-dir", str(state)]
        if writes:
            args.append("--enable-writes")
        if execution:
            args.append("--enable-execution")
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        self.next_id = 0

    def request(self, method, params=None):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        assert line, f"server died before replying to {method}"
        reply = json.loads(line)
        assert reply["id"] == self.next_id, f"id mismatch for {method}"
        return reply

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def tool(self, name, **arguments):
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        result = reply["result"]
        payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
        return payload, result.get("isError", False)

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)
        try:
            self.proc.kill()
        except Exception:
            pass


def expect(condition, label):
    if not condition:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ok: {label}")


def main():
    base = Path(tempfile.mkdtemp(prefix="nano-airbridge-test-"))
    root, state = base / "project", base / "state"
    root.mkdir()
    (root / "README.md").write_text("OpenMachine connection check\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def main():\n    return 'hello'\n")
    (root / ".env").write_text("SECRET=abc\n")
    try:
        # ---- handshake ------------------------------------------------------
        c = Client(root, state, writes=True, execution=True)
        reply = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "smoke", "version": "0"}})
        expect(reply["result"]["serverInfo"]["name"] == "nano-airbridge", "initialize returns serverInfo")
        expect(reply["result"]["protocolVersion"] == "2025-06-18", "protocol version negotiated")
        c.notify("notifications/initialized")

        # ---- tools/list -----------------------------------------------------
        reply = c.request("tools/list")
        names = [t["name"] for t in reply["result"]["tools"]]
        expect(len(names) == 9, f"tools/list exposes 9 tools ({len(names)})")

        # ---- capabilities ---------------------------------------------------
        payload, err = c.tool("get_capabilities")
        expect(not err and payload["grants"]["execution_enabled"] is True, "get_capabilities shows grants")
        sha_identity = payload["workspace_identity"]

        # ---- reads ----------------------------------------------------------
        payload, err = c.tool("read_file", path="README.md")
        expect(not err and payload["text"] == "OpenMachine connection check\n", "read_file returns text")
        expect(payload["sha256"] == hashlib.sha256(b"OpenMachine connection check\n").hexdigest(),
               "read_file whole-file sha256 matches")
        readme_sha = payload["sha256"]

        payload, err = c.tool("read_file", path="../etc/passwd")
        expect(err and payload["error"]["code"] == "path_denied", "path traversal is denied")
        payload, err = c.tool("read_file", path=".env")
        expect(err and payload["error"]["code"] == "path_denied", "credential path .env is denied")
        payload, err = c.tool("read_file", path=".git/config")
        expect(err and payload["error"]["code"] == "path_denied", ".git is denied")

        # ---- list / search --------------------------------------------------
        payload, err = c.tool("inspect_workspace", action="list", path_glob="*.md")
        expect(not err and [e["path"] for e in payload["entries"]] == ["README.md"],
               "inspect_workspace list with path_glob")
        payload, err = c.tool("inspect_workspace", action="search", query="hello")
        expect(not err and payload["matches"][0]["path"] == "src/app.py", "inspect_workspace search")

        # ---- write with CAS -------------------------------------------------
        payload, err = c.tool("write_file", path="notes.txt", content="first\n", expected_sha256="")
        expect(not err and payload["status"] == "published", "write_file creates new file (expected_sha256='')")
        note_sha = payload["sha256"]

        payload, err = c.tool("write_file", path="notes.txt", content="second\n", expected_sha256="")
        expect(err and payload["error"]["code"] == "source_changed", "write_file rejects stale precondition")

        payload, err = c.tool("write_file", path="sub/deep.txt", content="x", expected_sha256="")
        expect(err and payload["error"]["code"] == "publish_failed", "write_file does not create parents")

        # ---- edit -----------------------------------------------------------
        edit_id = "a" * 32
        payload, err = c.tool("edit_file", path="notes.txt", expected_sha256=note_sha,
                              edits=[{"old_text": "first", "new_text": "FIRST"},
                                     {"old_text": "\n", "new_text": " line\n"}],
                              change_id=edit_id, expected_workspace_identity=sha_identity)
        expect(not err and payload["status"] == "published" and payload["change_id"] == edit_id,
               "edit_file applies exact edits with change_id")
        expect((root / "notes.txt").read_text() == "FIRST line\n", "edit_file content verified on disk")

        payload, err = c.tool("edit_file", path="notes.txt", expected_sha256=note_sha,
                              edits=[{"old_text": "first", "new_text": "x"}], change_id="b" * 32)
        expect(err and payload["error"]["code"] == "source_changed", "edit_file rejects stale sha256")

        payload, err = c.tool("edit_file", path="README.md", expected_sha256=readme_sha,
                              edits=[{"old_text": "zzz-missing", "new_text": "x"}], dry_run=True)
        expect(err and payload["error"]["code"] == "ambiguous_edit", "edit_file fails on non-unique old_text")

        payload, err = c.tool("edit_file", path="README.md", expected_sha256=readme_sha,
                              edits=[{"old_text": "check", "new_text": "CHECK"}], dry_run=True)
        expect(not err and payload["status"] == "preview" and (root / "README.md").read_text().endswith("check\n"),
               "dry_run previews without writing")

        # ---- execution ------------------------------------------------------
        run_id = "c" * 32
        payload, err = c.tool("run_command", execution_id=run_id, cwd=".", argv=["/bin/sh", "-c", "echo hi from run"])
        expect(not err and payload["status"] in ("running", "succeeded"), "run_command starts with receipt")

        payload, err = c.tool("read_execution", execution_id=run_id, wait_seconds=2)
        expect(payload["status"] == "succeeded", "read_execution observes terminal status")
        expect("hi from run" in payload["output"]["text"], "read_execution returns output")

        # observe-never-replay
        payload, err = c.tool("run_command", execution_id=run_id, cwd=".", argv=["/bin/sh", "-c", "echo hi from run"])
        expect(not err and payload["status"] == "succeeded", "same execution_id + request observes, never replays")
        payload, err = c.tool("run_command", execution_id=run_id, cwd=".", argv=["/bin/echo", "different"])
        expect(err and payload["error"]["code"] == "execution_conflict", "same execution_id + different request conflicts")

        payload, err = c.tool("run_command", execution_id="d" * 32, cwd="/etc", argv=["/bin/echo", "x"])
        expect(err and payload["error"]["code"] == "invalid_cwd", "cwd outside workspace is denied")
        payload, err = c.tool("run_command", execution_id="not-hex", cwd=".", argv=["/bin/echo", "x"])
        expect(err and payload["error"]["code"] == "invalid_execution_id", "execution_id must be 32-hex")

        # cancel a long-running command
        sleep_id = "e" * 32
        payload, err = c.tool("run_command", execution_id=sleep_id, cwd=".", argv=["/bin/sh", "-c", "sleep 30"])
        payload, err = c.tool("cancel_execution", execution_id=sleep_id)
        expect(not err and payload["status"] in ("stopping", "cancelled", "failed", "succeeded"),
               "cancel_execution requests termination")
        payload, err = c.tool("read_execution", execution_id=sleep_id, wait_seconds=3)
        expect(payload["status"] == "cancelled", "cancelled execution reaches terminal receipt")

        payload, err = c.tool("list_executions")
        expect(not err and len(payload["executions"]) >= 2, "list_executions lists receipts")

        # unknown tool
        payload, err = c.tool("nope")
        expect(err and payload["error"]["code"] == "unknown_tool", "unknown tool is a bounded error")

        c.close()

        # ---- grants: read-only server ---------------------------------------
        c2 = Client(root, state, writes=False, execution=False)
        c2.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "smoke", "version": "0"}})
        payload, err = c2.tool("write_file", path="notes.txt", content="x", expected_sha256=note_sha)
        expect(err and payload["error"]["code"] == "workspace_write_disabled", "writes denied without grant")
        payload, err = c2.tool("run_command", execution_id="f" * 32, cwd=".", argv=["/bin/echo", "x"])
        expect(err and payload["error"]["code"] == "execution_disabled", "execution denied without grant")
        payload, err = c2.tool("read_file", path="README.md")
        expect(not err and payload["exists"], "reads still allowed without grants")
        c2.close()

        # ---- idempotent change receipt survives restart ---------------------
        c3 = Client(root, state, writes=True, execution=False)
        c3.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "smoke", "version": "0"}})
        payload, err = c3.tool("edit_file", path="notes.txt", expected_sha256=note_sha,
                               edits=[{"old_text": "first", "new_text": "FIRST"},
                                      {"old_text": "\n", "new_text": " line\n"}], change_id=edit_id)
        expect(not err and payload["status"] == "published", "identical change_id retry observes receipt")
        expect((root / "notes.txt").read_text() == "FIRST line\n", "retry did not replay the edit")
        c3.close()

        print("\nALL SMOKE TESTS PASSED")
        return 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
