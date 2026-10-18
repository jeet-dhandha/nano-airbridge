# How Open Machine works in code — and the minimal local-first slice

A code-level walkthrough of [OpenMachineLabs/openmachine](https://github.com/OpenMachineLabs/openmachine)
("OM Core", ~17.8k LOC of runtime Python across `openmachine/` + `integrations/`,
plus ~200 contract/component/resilience tests), and the **minimal distilled
implementation** in [`om-local/`](om-local/) that keeps only the local-first path
for any chat client.

---

## 1. What it is, in one diagram

OM Core is **not a chat app and not a model host**. It is an MCP *server* that a
chat client (ChatGPT, etc.) talks to, and an *operator* of your machines:

```text
You + ChatGPT
     │  approved MCP calls / returned results
     ▼
Secure MCP Tunnel (separately installed)          ← chat side, not OM
     │  private Unix socket: <state>/mcp.sock
     ▼
OM Core  (openmachine/server.py)
     ├── configured local workspaces + native commands   (workspace/, execution/)
     ├── prepared Linux workers over SSH                 (integrations/ssh/)
     ├── scoped native GitHub connection                 (integrations/github/)
     ├── authorized native Codex browser adapter         (integrations/codex.py)
     └── SQLite task ledger + original operation receipts (tasks/, app/)
```

Key architectural claim (`docs/introduction/architecture.md`): **conversation,
transport, native operations and evidence are four separate layers.** A command
finishing, a file changing, and a task completing are three different facts, each
with its own receipt that the chat must read back before trusting an outcome.

## 2. The request path, layer by layer

### 2.1 Transport — `openmachine/server.py` (214 LOC)

* `serve()` binds a **Unix domain socket** (`<state>/mcp.sock`, mode 0600) — *no
  TCP port at all* — and runs `uvicorn` on it with the MCP SDK's
  `streamable_http_app(stateless_http=True)` plus DNS-rebinding protection
  (allowed hosts: `localhost`). ChatGPT reaches it via OpenAI's Secure MCP Tunnel
  bound to that socket (`docs/getting-started/connect-chatgpt.md`).
* `create_server()` builds an `MCPServer` (the `mcp==2.2.0` SDK) with one
  middleware, `safe_tool_call`: before **any** `tools/call` it validates the tool
  name and arguments against the JSON Schema of the catalog (loaded and SHA-256
  hashed from `tools/list`), and rejects unknown tools / invalid / extra
  arguments *before the handler runs* — i.e. before any side effect can occur.
* Every tool is registered through `guided_tool()`, which attaches
  `ToolAnnotations` (read-only / destructive / idempotent / open-world hints) and
  injects the tool's operating guide from the Skills catalog into the docstring.

### 2.2 Tool surface — `openmachine/mcp/` (564 LOC)

Thin typed adapters (Pydantic `Annotated` fields become the JSON Schema) that
delegate to the Application and wrap results via `protocol._reply()`:

| Module | Tools |
| --- | --- |
| `environment.py` | `inspect_context` (runtime/project/resources observation) |
| `workspace.py` | `inspect_workspace`, `inspect_skills`, `read/write/edit_workspace_file`, `apply_workspace_patch`, `read_workspace_change` |
| `execution.py` | `start_execution`, `read_execution`, `cancel_execution`, `control_execution`, `list_executions`, `create_worktree`, `reconcile_execution`, `start/read_codex_task` |
| `tasks.py` | `create_task`, `read_task`, `wait_task`, `control_task` |
| `connections.py` | integration connect/login/disconnect, `github_*` ops |

`mcp/protocol.py` defines the shared wire rules:
* `_reply()` — catches `Fault`/`OSError` into `{error: {code, message}}` payloads
  (a tool failure is `isError` in the *result*, not a JSON-RPC error), bounds
  oversized results by truncation flags, and sets `isError` for failed mutations.
* `_submit_owned()` — runs each mutation in its own 1-thread pool so a cancelled
  caller request cannot abandon an effect mid-flight.
* Annotated shapes: `ExactEdit{old_text,new_text}`, `PatchPrecondition` (SHA-256
  CAS entries), `TerminalSize`, etc.

### 2.3 Application — `openmachine/app/` (2.4k LOC)

`Application` (app/`__init__.py`) is the typed operations layer. Highlights:
* `_admit()` — **every mutation** passes through one lifecycle admission: a shared
  `flock` lease on `state/locks/lifecycle.lock` + `service.guard_writes()`.
  "Explicit grants, effects only under admission."
* `get_capabilities()` — a huge honest self-description: formats, limits,
  receipt schemas, what is *not* probed ("No live ChatGPT/Tunnel health probe").
* `app/machines.py` — routes each operation: `transport == "ssh"` ⇒ send to the
  remote worker via `integrations/ssh/client.call`, else run the local
  `workspace/` + `execution/` implementation. One code path, two transports.
* `task_admission` / `task_observer` / `task_receipts` / `task_attention` — the
  durable multi-stage task machinery (SQLite ledger in `tasks/`).

### 2.4 Workspace — `openmachine/workspace/` (2.6k LOC)

The guarded file kernel:
* `root_for()` — workspace name ⇒ configured absolute root on a configured machine.
* `files.py: file_parts()` — validates a relative path: UTF-8, no `\`, no control
  chars, no `..`/`.`, no `.git`, and no `SECRET_NAMES` (`.ssh`, `.aws`, `.env*`,
  `*.pem`, `*.key`, `credentials`, ...).
* `files.py: file_parent()` — opens the root and each parent directory with
  `O_NOFOLLOW | O_DIRECTORY` dirfd walking: **any symlink in the chain fails**.
* `files.py: file_bytes()` — `O_NOFOLLOW` file open, regular-file + single-link
  check, size cap, read-under-stat race detection (size/mtime/ctime before vs
  after), UTF-8 + NUL check.
* `files.py: text_page()` — 32 KiB UTF-8-boundary pages with **redaction**
  (`foundation/sanitize.py: redact_slice` masks tokens/keys/passwords), returning
  `next_offset`, `has_more`, `content_redacted`, `size_bytes`.
* `files.py: read_text_file()` — combines the above with `git.context()`
  workspace identity + `head_sha` observed before and after the read (the tree
  must not change mid-observation), whole-file SHA-256, line windows, and an
  `AGENTS.md` notice trail. Every response carries `workspace_identity`.
* `write_text_file` / `edit_text_file` / `apply_patch` — the guarded publishers:
  CAS preconditions (`expected_sha256`, `expected_head_sha`,
  `expected_workspace_identity`), atomic temp+rename publication, exact-edit
  uniqueness ("ambiguous matches fail"), `om_patch_v1` plans with per-hunk
  preconditions, `dry_run` previews, and **durable change receipts** keyed by
  caller-supplied `change_id` (`changes.py`, `change_records.py`) where identical
  retained requests *observe* and never replay.

### 2.5 Execution — `openmachine/execution/` (2.6k LOC)

The command kernel (the part most worth studying):
* `api.py: validate_start()` — argv bounds (1..100 args, ≤4096 chars, no NUL),
  timeout 1..86400, resource keys, `expected_head_sha`, cwd must belong to a
  configured workspace.
* `api.py: start()` — caller supplies the 32-hex `execution_id` **before**
  launch. Under a per-ID `flock` lease: if a record already exists with the same
  request ⇒ return the original observation (**observe-never-replay**); a
  different request with the same ID ⇒ `execution_conflict`; a brand-new ID ⇒
  admit + dispatch. Unknown outcomes never permit a new ID for the same work.
* `process.py` — POSIX mechanics: `boot_identity()` (binds cleanup to this boot,
  not a reused PID), `session_members()`, and a `Process` class that owns fds and
  keeps the direct child unreaped (`waitid`/`WNOWAIT` on Linux, kqueue on macOS)
  so exit observation is explicit.
* `worker.py` — the sole post-admission writer: spawn in a **new POSIX session**
  (`posix_session` containment), drain output into a bounded rotating log
  (`logs.py`, redacted), advance stop deadlines (cancel / timeout / interrupt),
  publish terminal receipts atomically (`save_json`), and verify session cleanup.
* `records.py` — the receipt contract (`FORMAT = 5`): request echo, statuses
  (`succeeded/failed/timed_out/cancelled/interrupted/outcome_unknown`),
  `STOP_STATUS`, control protocol (`write_stdin`/`interrupt`/`resize` with
  monotonic `control_seq`, PTY/pipe modes).
* `observation.py` — `read()` returns the original receipt + raw-byte-cursor
  output paging (`offset`, `max_bytes`, `wait_seconds ≤ 5`, max 16 waiting
  readers) from the record owner — never a replay.

### 2.6 Foundation — `openmachine/foundation/` (224 LOC)

The tiny shared kernel (also the seed of om-local):
* `sanitize.py` — `Fault(code, message, evidence, effect)` where
  `effect ∈ {known_no_effect, effect_unknown}` is first-class API: callers learn
  *whether the world changed*, not just that something failed. Plus the secret
  redaction regexes (private keys, `ghp_*`/`sk-*` tokens, `password=...`).
* `files.py` — `SECRET_NAMES`/`excluded()`, bounded file reads, `text_page()`.
* `state.py` — `private_directory()` (0700 + ownership/mode check),
  `save_json()` (temp+rename+fsync), `lease()` (`flock` with shared/exclusive).

### 2.7 Config — `openmachine/config.py`

TOML with `pydantic` models and **explicit grants**:

```toml
[machines.local]
transport = "local"
execution_enabled = false          # native commands off by default
workspace_write_enabled = false    # typed writes off by default

[workspaces.project]
machine = "local"
root = "/absolute/path/to/project"
```

Everything else (SSH machines, GitHub scopes, Skill roots) is opt-in and
validated; `configuration_sha256()` pins the effective config identity.

### 2.8 Everything else

* `openmachine/tasks/` (2.9k LOC) — SQLite task ledger: stages, operations,
  events, evidence, transitions, index rebuilds.
* `openmachine/service/` (2.9k LOC) — macOS launchd user service: install/update/
  rollback of signed payloads, managed state machine, readiness probes.
* `integrations/` (3.5k LOC) — SSH worker bundles (`*.pyz` content-addressed),
  GitHub device-flow auth + scoped operations, Codex browser bridge.

## 3. The 8 invariants that make it "local-first safe"

1. **Explicit grants** — writes/execution disabled until the operator says so.
2. **Root confinement** — relative paths only; `O_NOFOLLOW` dirfd walk; secrets
   and `.git` structurally unreachable.
3. **Optimistic concurrency (SHA-256 CAS)** — read yields `sha256`; write must
   echo it; git `head_sha` + `workspace_identity` pin the tree further.
4. **Idempotent effects** — caller-generated IDs (`execution_id`, `change_id`,
   `request_id`); identical retries *observe*; conflicting reuse *fails*;
   uncertain outcomes are recorded as `outcome_unknown`, never retried blindly.
5. **Evidence over claims** — results are read back from the original receipt
   (byte-cursor output, change records), not from the write call's return.
6. **Bounded I/O** — page sizes, entry caps, timeouts, output budgets everywhere.
7. **Private, durable state** — 0700 dirs, atomic JSON, flock leases, fsync.
8. **Truthful errors** — typed `Fault` codes with `effect` classification and
   redaction of anything echoed back.

## 4. The minimal local-first slice — `om-local/`

For "work with my **local project** from **any chat**", only the local path is
needed. Everything else can be cut:

| Keep (local-first core) | Cut (not needed for local-only chat) | Why cut |
| --- | --- | --- |
| `foundation/state.py` (leases, atomic JSON, 0700) | `service/` (2.9k LOC) | No macOS daemon needed; the chat client spawns the server |
| `foundation/sanitize.py` (Fault, redaction) | `tasks/` (2.9k LOC) | Chat sessions are the task layer; receipts cover continuity |
| `config.py` local machine + workspace | `integrations/ssh` + worker bundles | No remote machines |
| `workspace/files.py` read + guarded write/edit kernel | `integrations/github` | Out of scope |
| `execution/` receipt/session/output kernel | `integrations/codex` | Out of scope |
| MCP tool surface + schemas | `workspace/patch.py`, worktrees | Exact edits suffice at minimal size |
| | Unix-socket HTTP + tunnel docs | stdio MCP is the universal local chat transport |
| | Skills catalog system | Tool docstrings carry the rules |

**Mapping OM Core → `om-local/om_local.py` (990 LOC, stdlib only):**

| om-local section | Distilled from |
| --- | --- |
| `Fault`, `redact()` | `foundation/sanitize.py` |
| `private_directory`, `save_json`, `lease` | `foundation/state.py` |
| `file_parts`, `open_parent`, `file_bytes`, `text_page` | `foundation/files.py` |
| `read_workspace_file`, `inspect_workspace` | `workspace/files.py` + `workspace/inspect.py` |
| `write_workspace_file`, `edit_workspace_file`, `_publish`, `_change_lookup/_record` | `workspace/files.py` publishers + `workspace/changes.py` receipts |
| `run_command`, `_reap`, `read_execution`, `cancel_execution` | `execution/api.py` + `execution/worker.py` + `execution/observation.py` (pipe mode only) |
| `Config` + explicit grants | `config.py` |
| `TOOLS` schemas + `handle_request` (stdio JSON-RPC) | `mcp/*.py` + `server.py` (transport swapped: stdio instead of Unix-socket HTTP) |

What the minimal version keeps verbatim in spirit: 32-hex caller IDs,
observe-never-replay, `""`-means-absent hash preconditions, no parent creation on
publish, no symlink tolerance, `SECRET_NAMES` denial list, redaction, bounded
pages (32 KiB), bounded scans (2000), POSIX session containment
(`start_new_session=True` + `killpg`), and the `Fault`/`effect` error shape.

What it intentionally simplifies: no git `head_sha`/worktree identity (filesystem
device+inode identity instead), no PTY/control protocol (pipes + cancel only),
no SSH/task ledger/patch language/service, no output rotation, and the receipt
store is per-run JSON instead of OM's versioned `FORMAT=5` records.

## 5. Result

`om-local/` = **one file, zero dependencies**, 33/33 end-to-end protocol and
safety tests passing (`python3 om-local/smoke_test.py`), ready to register in
Claude Desktop/Code, Cursor, Windsurf, Zed, Gemini CLI, VS Code Copilot — or
behind `mcp-proxy` for ChatGPT. See [`om-local/README.md`](om-local/README.md)
for the exact client configs.

## 6. Reference: repo facts checked

* Runtime code: `openmachine/` + `integrations/` ≈ 17,803 LOC Python (excl. tests,
  scripts); tests organized as unit / contract / component / resilience /
  repository suites (`scripts/run_tests.py`).
* Dependencies (`pyproject.toml`): `mcp==2.2.0`, `httpx2`, `jsonschema`,
  `pydantic`, `PyYAML`, `uvicorn`; Python ≥ 3.12; MIT license.
* npm package `openmachine` installs a macOS service wrapper; `openmachine core …`
  runs the Python CLI (`openmachine/cli.py`).
* Versions/limits seen in source: `execution.FORMAT = 5`, `MAX_TIMEOUT = 86400`,
  `TEXT_PAGE = 32768`, `MAX_SOURCE = 2 MiB`, `MAX_WAITERS = 16`, `MAX_WAIT = 5 s`,
  change/execution IDs = 32 lowercase hex, config `schema_version = 1`.
