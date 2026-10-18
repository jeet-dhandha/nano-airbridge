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

### 2. Connect Remote ChatGPT (Via Free Relay)
```bash
# 1. Deploy relay/nano_relay.php on your free PHP host
# 2. Start outbound long-polling agent on your PC:
python3 relay/nano_agent.py \
  --relay https://your-host.example/nano_relay.php \
  --token YOUR_AGENT_TOKEN \
  --command "python3 nano_airbridge.py --root /path/to/project --enable-writes --enable-execution"
# 3. Add Custom Connector in ChatGPT:
#    URL: https://your-host.example/nano_relay.php?op=mcp&s=YOUR_CLIENT_SECRET
```

### 3. Verification & Safety Audit
```bash
python3 smoke_test.py    # 33/33 protocol & concurrency tests
python3 audit.py         # Static zero-network verification
```
