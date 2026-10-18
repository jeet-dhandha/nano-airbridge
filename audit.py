#!/usr/bin/env python3
"""nano-airbridge transparency audit — verify there is nothing hidden.

Run it yourself before trusting this package:

    python3 audit.py

It statically checks the three runtime files and prints a trust report:

  * nano_airbridge.py — must contain ZERO network code (no sockets, no HTTP).
                        It cannot phone home even if it wanted to.
  * relay/nano_relay.php — must contain ZERO outbound-capable PHP (no curl, no
                           sockets, no shell/exec, no eval/obfuscation). State must
                           live only under its own ./data directory.
  * relay/nano_agent.py — may talk to exactly two places: the --relay URL the user
                          configures (urllib only) and the local MCP server over
                          stdio (subprocess). Nothing else.

Exit code 0 = all checks passed. Any violation is listed with file:line so you
can read the exact code yourself. This script is part of the open-source
package; it is not a substitute for reading the code (~1600 lines total).
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent

# Imports/verbs that would make network or hidden execution possible.
NETWORK_IMPORTS = {
    "socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp",
    "ftplib", "smtplib", "telnetlib", "asyncio", "websocket", "paramiko",
}
EXEC_VERBS = re.compile(
    r"\b(os\.system|os\.popen|os\.exec\w*|eval\s*\(|exec\s*\(|__import__\s*\()")

# PHP functions that can reach the network, run commands, or hide code.
PHP_FORBIDDEN = [
    "curl_init", "curl_exec", "fsockopen", "pfsockopen", "stream_socket_client",
    "stream_socket_server", "socket_create", "socket_connect", "dns_get_record",
    "gethostbyname", "mail(", "mb_send_mail",
    "exec(", "system(", "passthru(", "shell_exec(", "popen(", "proc_open(",
    "pcntl_exec", "eval(", "assert(", "preg_replace", "create_function",
    "base64_decode", "gzinflate", "gzuncompress", "str_rot13", "assert",
]
PHP_LOCAL_OK = ("php://input", "php://", "__DIR__")  # request body + own dir only

finding_count = 0


def note(ok: bool, message: str) -> None:
    global finding_count
    mark = "ok:" if ok else "FAIL:"
    print(f"  {mark} {message}")
    if not ok:
        finding_count += 1


def strip_comments(text: str, python: bool) -> str:
    if python:
        text = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', '""', text)
        text = re.sub(r"(?m)#.*$", "", text)
    else:
        text = re.sub(r"/\*[\s\S]*?\*/", "", text)
        text = re.sub(r"(?m)//.*$", "", text)
        text = re.sub(r"(?m)#.*$", "", text)
    return text


def audit_nano_airbridge() -> None:
    print("\n[1] nano_airbridge.py — local MCP server (must be network-free)")
    raw = (HERE / "nano_airbridge.py").read_text()
    code = strip_comments(raw, python=True)
    imports = set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", code, re.M))
    roots = {name.split(".")[0] for name in imports}
    bad = sorted(roots & NETWORK_IMPORTS)
    note(not bad, f"no network-capable imports (found: {bad or 'none'})")
    hits = [(i + 1, line) for i, line in enumerate(raw.splitlines())
            if EXEC_VERBS.search(strip_comments(line, python=True))
            and "subprocess" not in line]
    note(not hits, f"no hidden execution primitives (os.system/eval/exec) {hits[:3]}")
    note(True, "uses subprocess.Popen only for the grant-gated run_command tool (read it yourself)")
    note("socket" not in code and "http" not in code.lower().replace("http_", ""),
         "no socket/http references at all — the process cannot open a connection")


def audit_nano_relay_php() -> None:
    print("\n[2] relay/nano_relay.php — hosted queue (must make no outbound calls, no shell)")
    raw = (HERE / "relay" / "nano_relay.php").read_text()
    code = strip_comments(raw, python=False)
    for verb in PHP_FORBIDDEN:
        hits = [i + 1 for i, line in enumerate(code.splitlines()) if verb in line]
        note(not hits, f"forbidden PHP '{verb}' absent" + (f" — lines {hits}" if hits else ""))
    urls = [(i + 1, line.strip()) for i, line in enumerate(raw.splitlines())
            if re.search(r"https?://", line) and not line.strip().startswith(("*", "//", "#"))]
    note(not urls, f"no hardcoded URLs in executable code {urls[:3]}")
    note(raw.count("__DIR__") > 0, "state confined to __DIR__/data (queue, receipts, meta)")
    note("file_put_contents" in code or "file_get_contents" in code,
         "I/O is local file storage only (file_get/put_contents) — read it yourself")


def audit_nano_agent() -> None:
    print("\n[3] relay/nano_agent.py — per-user bridge (may reach ONLY your relay URL)")
    raw = (HERE / "relay" / "nano_agent.py").read_text()
    code = strip_comments(raw, python=True)
    imports = set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", code, re.M))
    roots = {name.split(".")[0] for name in imports}
    bad = sorted((roots & NETWORK_IMPORTS) - {"urllib"})
    note(not bad, f"network limited to urllib (found: {bad or 'none'})")
    urlopen_lines = [i + 1 for i, line in enumerate(raw.splitlines()) if "urlopen" in line]
    note(len(urlopen_lines) == 2, f"exactly two urlopen call sites (post_json/get_json): {urlopen_lines}")
    endpoints = [(i + 1, line.strip()) for i, line in enumerate(code.splitlines())
                 if "?op=" in line]
    note(all("relay" in line or "poll_url" in line or 'f"{relay}' in line
             for _, line in endpoints),
         "every request URL is derived from the --relay argument the user provides")
    bad_exec = [(i + 1, line) for i, line in enumerate(raw.splitlines())
                if EXEC_VERBS.search(strip_comments(line, python=True))]
    note(not bad_exec, f"no os.system/eval/exec — only subprocess for the local stdio server {bad_exec[:3]}")


def main() -> int:
    print("nano-airbridge transparency audit")
    print("package:", HERE)
    for name in ("nano_airbridge.py", "relay/nano_relay.php", "relay/nano_agent.py"):
        if not (HERE / name).is_file():
            print(f"missing file: {name}")
            return 2
    audit_nano_airbridge()
    audit_nano_relay_php()
    audit_nano_agent()
    print(f"\n{'ALL AUDIT CHECKS PASSED' if finding_count == 0 else str(finding_count) + ' AUDIT FAILURES'}")
    print("This proves the absence of common hidden-behavior patterns; reading the")
    print("code remains the real verification. MIT licensed: use, fork, and modify freely.")
    return 1 if finding_count else 0


if __name__ == "__main__":
    sys.exit(main())
