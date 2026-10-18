# nano-airbridge relay — free hosting instead of per-user ngrok

A two-piece bridge so a **web chat (ChatGPT) can reach users' local machines**
with **nothing but a Python script on the user's PC** — no ngrok, no cloudflared,
no tunnel binary, no open ports. The middle piece is one PHP file you deploy
**once** on any free PHP host.

```text
ChatGPT cloud ──HTTPS──▶ nano_relay.php (free PHP host) ──▲── long-poll ── nano_agent.py (user PC)
                          queue + MCP endpoint       │                │ stdio
                          (public URL + secret)      │                ▼
                                                     └── outbound only ── nano_airbridge.py ── project
```

The protocol mirrors OpenAI's Secure MCP Tunnel (queue + outbound long-poll +
response post) [architecture](https://github.com/openai/tunnel-client/blob/master/docs/architecture.md),
with free PHP hosting as the control plane.

## The two pieces

| File | Runs where | Installs needed |
| --- | --- | --- |
| `nano_relay.php` | **Once**, on any free PHP host (your account) | none — upload one file |
| `nano_agent.py` | **Each user's machine** | Python 3.11+ only (stdlib, no pip) |

## 1. Deploy the relay (once, on hosting **you** control)

**Self-hosting is the privacy-preserving default.** `nano_relay.php` is the complete
server — there is no other backend, no account, and no phone-home. If you (or
your users) deploy it on your own free PHP account, every tool call, file read,
and command result stays on machines you control and **the package author never
sees any of it**. To use it fully independently: upload the file, generate your
own secrets, run the agent, and you are done — nothing in this package contacts
its author.

Requirements: **PHP 8.0+** with a writable directory and **≥2 concurrent
PHP requests** (nearly every host; needed so a waiting MCP call and an agent
poll can run at the same time). No database, no composer.

1. Upload `nano_relay.php` into your web root (e.g. `public_html/nano_relay.php`).
2. Open `https://your-host.example/nano_relay.php?op=healthz` once — on first run it
   auto-generates `nano_relay_config.php` with two random secrets (like OM Core's
   first-run init) and logs the path. **Read it, keep it private, and back it up.**
   (Or set `NANO_RELAY_CLIENT_SECRET` / `NANO_RELAY_AGENT_TOKEN` env vars yourself.)
3. Optionally deny web access to `nano_relay_config.php` (the file writes a
   `data/.htaccess` deny rule for its queue; add one for the config too if your
   host allows it).

Free PHP hosts that fit (PHP 8, HTTPS, writable disk): InfinityFree, AwardSpace,
Byet/iFastNet, or any €0 tier of a normal host. Caveats of free tiers: fair-use
CPU limits, occasional slow requests, and you should treat the host as
**semi-trusted** (see Security). If PHP hosting proves flaky, the same relay
logic ports directly to a free **Cloudflare Worker** (100k req/day, no sleep) —
the queue becomes KV/Durable Objects.

## 2. Set up each user (one command, zero installs)

```bash
python3 nano_agent.py \
  --relay https://your-host.example/nano_relay.php \
  --token AGENT_TOKEN_FROM_RELAY_CONFIG \
  --command "python3 nano_airbridge.py --root /home/user/project --enable-writes --enable-execution"
```

That's the entire "installation". The agent dials **outbound only** (works behind
NAT/firewalls), registers the tool list, then long-polls for work and feeds it to
`nano_airbridge.py` over stdio. Auto-reconnects with backoff; SIGTERM cleans up the
child. Users can also bundle this script inside your app's installer.

## 3. Connect ChatGPT

ChatGPT → **Settings → Connectors → Create custom connector**:

* Connection type: **no auth** (or OAuth if you front the relay with one)
* MCP URL: `https://your-host.example/nano_relay.php?op=mcp&s=CLIENT_SECRET`
  (the `s=` path/param *is* the auth — make it long and unguessable; ChatGPT
  requires a remote HTTPS endpoint and can't run local processes)

`initialize`, `ping` and `tools/list` are answered by the relay from the cached
registration (so the connector works even when the user's PC is offline — tools
are listed; calls return a bounded `agent_offline` error). Everything else is
queued to the agent.

## How a tool call flows

1. ChatGPT → `POST ?op=mcp&s=…` `tools/call read_file` → relay enqueues
   `{internal_id, method, params}` (caller's JSON-RPC id kept aside).
2. Agent's next `GET ?op=poll` claims it → writes JSON-RPC to `nano_airbridge.py` stdin.
3. Agent `POST ?op=response` with the result → relay re-sends it under the
   **caller's original id** to ChatGPT.

Timeouts are OM-style bounded errors with effect classification: if the request
was never claimed → `agent_offline` / `known_no_effect` (nothing happened); if
claimed but unanswered → `agent_timeout` / `effect_unknown` ("read the original
record before any retry"). That second case is why `nano-airbridge`'s execution/change
receipts matter: a retry with the same `execution_id`/`change_id` **observes,
never replays**.

## Security model (read this before offering it to users)

* **The local grants remain the real boundary.** `nano_airbridge.py` refuses writes and
  commands unless the operator enabled them; the relay only moves bytes.
* The relay sees **plaintext tool calls and results** (it terminates MCP). Treat
  a free PHP host as *semi-trusted*: fine for your own projects and users you
  trust, not ideal for secrets. Don't return secrets through it (nano-airbridge already
  redacts tokens/keys).
* Auth = unguessable `s=` client secret + Bearer agent token. Rotate by editing
  `nano_relay_config.php`. Both travel only over HTTPS.
* A malicious relay operator could *inject* tool calls to a connected agent —
  again limited by grants + path confinement + CAS preconditions on the user's
  machine. For stronger guarantees, run the relay on hosting you control.
* The relay is a live remote-control path when an agent is connected. Users
  should stop `nano_agent.py` when done (it is outbound-only, but it is a path).
* Add host-level rate limiting if you expose this beyond personal use
  (the relay itself caps queue depth at 100 and waits at ~25 s max per call).

## Tests

```bash
python3 relay/smoke_test.py   # needs php + python3; 15 end-to-end checks
python3 ../smoke_test.py      # the local-only server suite (33 checks)
```

## Verifying there's nothing hidden (truly open source)

Every file here is short, plain, MIT-licensed source — read it, fork it, change
it. For a mechanical first pass, run the bundled audit from the package root:

```bash
python3 ../audit.py
```

It verifies, with file:line evidence you can check yourself:

* **`nano_airbridge.py` has zero network code** — no socket/http/urllib imports at all.
  The local server provably cannot phone home.
* **`nano_relay.php` makes no outbound calls and runs no shell** — no curl, no PHP
  sockets, no exec/eval/base64 tricks; it only reads and writes files under its
  own `data/` directory. A self-hosted relay cannot exfiltrate to anyone else.
* **`nano_agent.py` talks to exactly two places**: the `--relay` URL **you**
  configure (two `urlopen` call sites) and your local MCP server over stdio.

What transits a relay you host: MCP tool calls and their results (which can
include project file contents and command output — same as any MCP client
connection). Retention: queued/inflight/response files are deleted after 10
minutes (`STALE_SECONDS`); receipts stay on the **user's** machine only. To wipe
the relay: delete `nano_relay.php`, `nano_relay_config.php`, and `data/` — nothing exists
anywhere else.

## Why not just ngrok per user?

You can (and it's simpler ops-wise): `mcp-proxy` + `ngrok http` works, but every
user must install and run a third-party tunnel with a **public URL**. This relay
keeps the same architecture while the per-user footprint is one stdlib Python
script, and the hosted URL can be secret-pathed and disabled at any time by
deleting one file.

## License

MIT. Part of the nano-airbridge distillation of Open Machine (OM Core) — MIT © 2026
OpenMachineLabs.
