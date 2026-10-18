# nano-airbridge — Zero-Dependency MCP Server & Cloud Relay

A **single-file, stdlib-only Model Context Protocol (MCP) server and free cloud relay** distilled from
[OpenMachine (OM Core)](https://github.com/OpenMachineLabs/openmachine)
that lets **any MCP-capable chat (Claude, Cursor, Windsurf, or web ChatGPT)** work on **one local project** — with OM's
safety kernel preserved and its complex remote/fleet/GitHub machinery removed.

```text
                                  ┌─── stdio (local) ─── Claude Desktop / Cursor / Windsurf
                                  │
Your Project Directory ◀──▶ nano_airbridge.py
                                  ▲
                                  │ stdio
                            nano_agent.py (user machine, outbound-only long-poll)
                                  │
                                  ▼ HTTPS
                         nano_relay.php (free PHP host or Cloudflare Worker)
                                  ▲
                                  │ HTTPS
                            ChatGPT Cloud (Custom Connector)
```

* `nano_airbridge.py` — the server (Python 3.11+ stdlib only, zero dependencies, **0 network imports**)
* `relay/nano_relay.php` — one-file hosted relay for free PHP hosting or Cloudflare Workers
* `relay/nano_agent.py` — outbound-only bridge agent (no ngrok, no port-forwarding, no binaries)
* `audit.py` — static transparency audit verifying zero network calls and strict containment
* `smoke_test.py` — 33/33 end-to-end protocol, concurrency, and safety tests
* `config.toml` — optional configuration; CLI flags also work

---

## Quick Start

```bash
# 1. Read-only (safest): the chat can inspect and search, but cannot modify anything
python3 nano_airbridge.py --root /absolute/path/to/project

# 2. Full local work: writes + command execution (explicit operator grants)
python3 nano_airbridge.py --root /absolute/path/to/project --enable-writes --enable-execution
```

Keep `--root` to the smallest project directory you want exposed. The server
never touches anything outside it (and strictly refuses `.git`, `.env`, keys, and credentials).

---

## The 9 Native Tools

| Tool | What it does |
| --- | --- |
| `get_capabilities` | Inspect active operator grants, root identity hash, and limits |
| `inspect_workspace` | `list` files or `search` text (bounded, glob-filtered, ignores hidden credentials) |
| `read_file` | Paged UTF-8 read (32 KiB pages) + whole-file SHA-256 hash |
| `write_file` | Whole-file publish under SHA-256 precondition (`""` = new file CAS) |
| `edit_file` | Exact fragment replacement, `dry_run` preview, change receipts |
| `run_command` | Start a command in the root with caller-chosen `execution_id` receipt |
| `read_execution` | Read original execution receipt + output cursor (never replays) |
| `cancel_execution` | Terminate the owned process session (kills process group) |
| `list_executions` | Find original execution IDs (crash recovery) |

---

## Core Safety Invariants (from OM Core)

* **Explicit operator grants** — `--enable-writes` / `--enable-execution` default to `false`. Without explicit flags, the server is strictly read-only.
* **SHA-256 preconditions (CAS concurrency)** — every read returns the whole-file hash; every write/edit must echo it. If the file changed in the background, edits fail with `source_changed` and have **no effect**.
* **Observe-never-replay** — `execution_id` and `change_id` are caller idempotency keys: identical retries return the original record; conflicting reuse fails with `execution_conflict`.
* **Path confinement** — relative paths only, no traversal, `O_NOFOLLOW` directory walk (symlinks refused), credentials (`.git`, `.env`, private keys) blocked.
* **POSIX session containment** — each command runs with `start_new_session=True`; cancel or timeout signals the whole process group.
* **Private state** — `~/.nano-airbridge` at mode 0700, atomic JSON receipts, `flock` leases.
* **Secret redaction** — API tokens, private keys, and passwords masked in returned text.
* **Bounded everything** — bounded page sizes (32 KiB), max scan entries, timeouts, and output cursors.

---

## Connecting Clients

### 1. Claude Desktop
Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "nano-airbridge": {
      "command": "python3",
      "args": [
        "/path/to/nano-airbridge/nano_airbridge.py",
        "--root", "/path/to/project",
        "--enable-writes",
        "--enable-execution"
      ]
    }
  }
}
```

### 2. Cursor / Windsurf / Zed / VS Code
* **Cursor**: Add to `.cursor/mcp.json` or `~/.cursor/mcp.json`.
* **Windsurf**: Add to `~/.codeium/windsurf/mcp_config.json`.
* **Claude Code** (CLI):
  ```bash
  claude mcp add nano-airbridge -- python3 /path/to/nano_airbridge.py --root /path/to/project --enable-writes --enable-execution
  ```

---

### 3. Remote Web Chats (ChatGPT Custom Connectors)
ChatGPT web cannot run local processes directly. Instead of paying for ngrok or running binary daemons, use the **free relay**:

1. **Deploy relay** (once): Upload `relay/nano_relay.php` to any free PHP host (InfinityFree, AwardSpace, or your own server).
2. **Start agent** (on your local machine):
   ```bash
   python3 relay/nano_agent.py \
     --relay https://your-host.example/nano_relay.php \
     --token YOUR_AGENT_TOKEN \
     --command "python3 nano_airbridge.py --root /path/to/project --enable-writes --enable-execution"
   ```
3. **Connect ChatGPT**:
   * ChatGPT → **Settings → Connectors → Create custom connector**
   * URL: `https://your-host.example/nano_relay.php?op=mcp&s=YOUR_CLIENT_SECRET`

The agent connects outbound only (works behind NAT and corporate firewalls).

---

## Verification & Transparency Audit

Run the static transparency auditor before trusting this package:

```bash
python3 audit.py
```

It statically analyzes the runtime files to guarantee:
* `nano_airbridge.py` contains **zero network code** (no sockets, no HTTP, cannot phone home).
* `relay/nano_relay.php` contains **no outbound network capabilities** (no curl, no sockets, no exec/shell).
* `relay/nano_agent.py` only connects to your explicitly configured `--relay` URL.

Run the protocol smoke test suite:
```bash
python3 smoke_test.py
```

---

## Agent Skill Integration

This repository also includes a native skill definition under [`skills/nano-airbridge/SKILL.md`](skills/nano-airbridge/SKILL.md) for automated agent frameworks (such as Antigravity, Claude Code, and Codex).

---

## License & Attribution

MIT License. Derived and distilled from [Open Machine (OM Core)](https://github.com/OpenMachineLabs/openmachine) — MIT © 2026 OpenMachineLabs. The local safety invariants and concurrency patterns are preserved; remote fleets, task databases, and service daemons have been cleanly eliminated.
