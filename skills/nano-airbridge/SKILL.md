---
name: nano-airbridge
description: >-
  Connect cloud AI chats (ChatGPT Custom Connectors, Claude Desktop, Cursor) to local project directories with zero dependencies, zero npm/pip installs, and zero tunnel binaries (no ngrok). Runs a single-file stdlib MCP server and an outbound-only long-polling cloud relay.
---

# nano-airbridge — Skill & Operator Guide

`nano-airbridge` gives AI coding agents and web chats (ChatGPT, Claude Desktop, Cursor, Windsurf) guarded access to ONE local project workspace over standard Model Context Protocol (MCP).

## When to Use

1. **Remote AI to Local Workspace**: When an AI in the cloud (such as ChatGPT via Custom Connectors) needs to read, edit, or test code on a local machine behind NAT/firewall without paying for ngrok or running complex daemons.
2. **Guarded Local Agent Execution**: When running an autonomous agent locally where you want strict safety boundaries:
   - Path confinement to `--root` (no directory traversal, no symlink escapes `O_NOFOLLOW`, credentials like `.env` / `.git` blocked).
   - Optimistic concurrency control (SHA-256 CAS preconditions; no blind file overwrites).
   - POSIX process group session containment (signals kill whole subprocess trees).
   - Secrets masking and token redaction.
   - Idempotency receipts (observe-never-replay).

## Components

| Component | Path | Responsibility |
| --- | --- | --- |
| **Local Server** | `nano_airbridge.py` | Single-file Python 3.11+ stdlib MCP server. Zero network calls. |
| **Cloud Relay** | `relay/nano_relay.php` | Single-file PHP 8.0+ script deployed on any free host or Cloudflare Worker. |
| **Relay Agent** | `relay/nano_agent.py` | Pure stdlib client connecting local stdio server to cloud relay via outbound long-poll. |
| **Trust Auditor** | `audit.py` | Static AST analyzer proving 0 network imports in server and 0 shell/curl in relay. |

## Quick Commands

### 1. Run Local MCP (Claude Desktop, Cursor, Zed)
```bash
# Read-only inspection (safest)
python3 nano_airbridge.py --root /path/to/project

# Full read/write/execution
python3 nano_airbridge.py --root /path/to/project --enable-writes --enable-execution
```

### 2. Connect Remote Chats (ChatGPT, Kimi, Claude, Qwen)

#### Option A: Instant Local Gateway (ngrok)
```bash
# Start zero-dependency local HTTP gateway
python3 nano_http_server.py --root /path/to/project --port 8899 --enable-writes --enable-execution
# Expose publicly
ngrok http 8899
# Endpoint: https://<subdomain>.ngrok-free.dev/mcp
```

#### Option B: 100% Free Remote PHP Relay (InfinityFree / Cloudflare / cPanel)
* Reference instance: `https://nano-airbridge.gt.tc/nano_relay.php`
* Run local long-polling client:
```bash
python3 relay/nano_agent.py \
  --relay https://nano-airbridge.gt.tc/nano_relay.php \
  --token YOUR_AGENT_TOKEN \
  --command "python3 nano_airbridge.py --root /path/to/project --enable-writes --enable-execution"
```
* Custom connector endpoint: `https://nano-airbridge.gt.tc/nano_relay.php?op=mcp&s=YOUR_CLIENT_SECRET`

### 3. Universal SYSTEM Prompt for AI Chat Prompts
Paste this system instruction into your prompt for agentic platforms:
```markdown
[SYSTEM INSTRUCTION: NANO-AIRBRIDGE WORKSPACE INTEGRATION]
You are connected to the operator's local project workspace via nano-airbridge.
- Bridge Endpoint: <YOUR_BRIDGE_URL>/mcp
- Protocol: Model Context Protocol (MCP) JSON-RPC 2.0 with SHA-256 CAS verification.
- Concurrency: Every `read_file` returns the file's current `sha256`. Every `write_file` and `edit_file` MUST supply `expected_sha256`.
- Idempotency: Retries with the same `execution_id` or `change_id` observe the original receipt and never replay.
```

### 4. Verification & Safety Audit
```bash
python3 smoke_test.py    # 33/33 protocol & concurrency tests
python3 audit.py         # Static zero-network verification
```

