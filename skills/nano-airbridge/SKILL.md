---
name: nano-airbridge
description: >-
  Connect cloud AI chats (ChatGPT Custom Connectors, Claude Desktop, Cursor) to local project directories with zero dependencies, zero Python, zero PHP, and zero accounts. Runs a single standalone Go binary with automatic free public localtunnel.
---

# nano-airbridge — Skill & Operator Guide

`nano-airbridge` gives AI coding agents and web chats (ChatGPT, Claude Desktop, Cursor, Windsurf) guarded access to ONE local project workspace over standard Model Context Protocol (MCP). Built entirely in **pure Go** into a single standalone binary with **zero Python and zero PHP dependencies**.

## When to Use

1. **Remote AI to Local Workspace**: When an AI in the cloud (such as ChatGPT via Custom Connectors or web fetch) needs to read, edit, or test code on a local machine behind NAT/firewall without paying for ngrok or creating accounts.
2. **Guarded Local Agent Execution**: When running an autonomous agent locally where you want strict safety boundaries:
   - Path confinement to `--root` (no directory traversal `..`, credentials like `.env` / `.git` / `.pem` blocked).
   - Optimistic concurrency control (SHA-256 CAS preconditions; no blind file overwrites).
   - Clean execution containment.
   - Idempotency receipts.

## Architecture

| Component | Path | Responsibility |
| --- | --- | --- |
| **Standalone Binary** | `bin/nano-airbridge` | Single native static binary (Go). Serves `/healthz`, `/inspect`, and `/mcp`. Zero runtime dependencies. |
| **One-Click Launcher** | `start.sh` / `start.bat` | Launches standalone binary, detects OS, and auto-spawns free `localtunnel` for remote web AI access. |
| **Build Scripts** | `build.sh` / `build.bat` | 1-command cross-compilation for macOS (arm64/amd64), Linux (amd64/arm64), and Windows (amd64). |

## Quick Commands

### 1. Launch with One Command (Zero Python, Zero PHP)
```bash
# macOS / Linux
./start.sh /path/to/project

# Windows
start.bat C:\path\to\project
```

### 2. Compile Your Own Binary from Source
```bash
# Compile for host system (placed into bin/nano-airbridge)
./build.sh

# Compile all release targets (macOS arm64/x64, Linux x64/arm64, Windows x64)
./build.sh all
```

### 3. Verify Integrity
```bash
# Run 10/10 end-to-end protocol and CAS concurrency test suite
./test_suite.sh
```
