#!/usr/bin/env python3
"""nano-airbridge — minimal local-first MCP runtime distilled from Open Machine (OM Core).

Gives any MCP-capable chat client (Claude Desktop/Code, Cursor, Windsurf, Zed,
Gemini CLI, VS Code Copilot, ...) guarded access to ONE local project directory
over stdio. Python 3.11+ standard library only. No network, no daemon.

Distilled from https://github.com/OpenMachineLabs/openmachine (MIT © 2026
OpenMachineLabs). The core invariants stay faithful to OM Core:

  * Explicit grants      — workspace writes and command execution are OFF unless
                           the operator enables them (like config.toml's
                           execution_enabled / workspace_write_enabled).
  * Path confinement     — relative paths only, no traversal, no symlinks in the
                           chain (O_NOFOLLOW walk), no .git / credential files.
  * SHA-256 preconditions— optimistic concurrency: reads return the whole-file
                           hash; writes must echo it ("" means "must not exist").
  * Execution receipts   — caller-supplied 32-hex execution_id; the same ID is
                           always observe-never-replay; conflicting reuse fails.
  * POSIX session        — each command gets its own session (start_new_session)
                           so cancel/timeout can signal the whole process group.
  * Private state        — 0700 state dir, atomic JSON receipts, flock leases.
  * Bounded I/O          — 32 KiB UTF-8 pages, bounded scans/output, timeouts.
  * Secret redaction     — tokens/keys/passwords masked in returned text.

Tools: get_capabilities, inspect_workspace (list|search), read_file, write_file,
edit_file, run_command, read_execution, cancel_execution, list_executions.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from typing import Any

VERSION = "0.1.0"
SERVER_NAME = "nano-airbridge"
PROTOCOL_FALLBACK = "2025-06-18"
PROTOCOL_SUPPORTED = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

# ---- Limits (from OM Core's bounded-I/O design) ------------------------------
TEXT_PAGE = 32 * 1024          # one read_file page (foundation/files.py:TEXT_PAGE)
MAX_SOURCE = 2 * 1024 * 1024   # whole-file cap for read/edit
MAX_LINES = 500                # line-window cap per read
MAX_SCAN_ENTRIES = 2000        # list/search entry bound
MAX_MATCHES = 200
MAX_EDIT_BYTES = 131072        # per replacement string
MAX_TIMEOUT = 86400
DEFAULT_TIMEOUT = 600
MAX_WAIT_SECONDS = 5
MAX_READ = 65536
HEX32 = re.compile(r"^[0-9a-f]{32}$")
SHA256 = re.compile(r"^(?:[0-9a-f]{64})?$")

# Credential-ish names never readable/writable/scannable (OM Core SECRET_NAMES).
SECRET_NAMES = {".git", ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".netrc", ".pypirc",
                "credentials", "credentials.json", "secrets.toml", "id_rsa", "id_ed25519"}


# ---- Fault: bounded public errors with effect classification -----------------
class Fault(Exception):
    """OM-style error: safe message + optional evidence + effect class."""

    def __init__(self, code: str, message: str, evidence: dict | None = None,
                 *, effect: str | None = None):
        super().__init__(message)
        self.code, self.evidence, self.effect = code, evidence, effect


# ---- Secret redaction (patterns adapted from foundation/sanitize.py) ---------
_REDACTIONS = (
    (re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)", re.S),
     0, "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})"), 0, "[REDACTED]"),
    (re.compile(r"(?i)\b((?:password|secret|token|api[_-]?key)\s*[:=]\s*)([^\s,;]+)"), 2, "[REDACTED]"),
)


def redact(text: str) -> str:
    for pattern, group, replacement in _REDACTIONS:
        text = pattern.sub(lambda m: (m.group(1) if group else "") + replacement, text)
    return text


# ---- Private state: 0700 dir, atomic JSON, flock leases ----------------------
def private_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise Fault("invalid_state", "The state directory must be an absolute path.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Fault("unsafe_state", "State directories must be owned by this user with mode 0700.")
    return path.resolve()


def save_json(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load_json(path: Path) -> dict:
    try:
        with path.open() as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise Fault("corrupt_state", "A state record could not be read; preserve the original store.") from error


@contextmanager
def lease(path: Path, *, shared: bool = False):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "rb") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH if shared else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Fault("resource_busy", "An owned operation already holds this record.") from error
        yield handle


# ---- Configuration: explicit grants, never implicit --------------------------
class Config:
    def __init__(self, root: str, state: Path, writes: bool, execution: bool,
                 max_file_bytes: int, command_timeout: int):
        self.root = Path(root)
        self.state = state
        self.writes = writes
        self.execution = execution
        self.max_file_bytes = max_file_bytes
        self.command_timeout = command_timeout
        if not self.root.is_absolute() or not self.root.is_dir():
            raise Fault("invalid_workspace", "The workspace root must be an existing absolute directory.")
        self.root = self.root.resolve()
        info = self.root.stat()
        self.identity = hashlib.sha256(f"{info.st_dev}:{info.st_ino}".encode()).hexdigest()


def load_config(args: argparse.Namespace) -> Config:
    values: dict[str, Any] = {}
    if args.config:
        try:
            with args.config.open("rb") as source:
                values = tomllib.load(source)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise Fault("invalid_config", "Check the TOML config: root, grants and limits.") from error
    if args.root is not None:
        values["root"] = str(args.root)
    if args.enable_writes:
        values["workspace_write_enabled"] = True
    if args.enable_execution:
        values["execution_enabled"] = True
    if args.state_dir is not None:
        values["state_dir"] = str(args.state_dir)
    if "root" not in values:
        raise Fault("invalid_config", "Provide --root (or root= in --config) for the project workspace.")
    state = private_directory(Path(values.get("state_dir", Path.home() / ".nano-airbridge")).expanduser())
    return Config(
        root=str(values["root"]),
        state=state,
        writes=bool(values.get("workspace_write_enabled", False)),
        execution=bool(values.get("execution_enabled", False)),
        max_file_bytes=min(int(values.get("max_file_bytes", MAX_SOURCE)), MAX_SOURCE),
        command_timeout=min(int(values.get("command_timeout_seconds", DEFAULT_TIMEOUT)), MAX_TIMEOUT),
    )


# ---- Workspace kernel: confinement + bounded reads ---------------------------
def file_parts(path: str) -> tuple[str, ...]:
    """Validate a relative resource path (OM Core file_parts): no traversal, no
    backslashes, no control chars, no .git, no credential names."""
    try:
        if not isinstance(path, str):
            raise ValueError
        path.encode("utf-8")
    except (UnicodeError, ValueError) as error:
        raise Fault("path_denied", "Use a valid UTF-8 relative file path.") from error
    parts = path.split("/")
    lowered = [p.casefold() for p in parts]
    if (not path or "\\" in path or any(ord(c) < 32 for c in path)
            or any(p in {"", ".", ".."} for p in parts)
            or any(p == ".git" for p in lowered)
            or any(p in SECRET_NAMES or p == ".env" or p.startswith(".env.")
                   or p.endswith((".pem", ".key", ".p12", ".pfx")) for p in lowered)):
        raise Fault("path_denied", "Use a relative non-credential file path without traversal or Git control files.")
    return tuple(parts)


@contextmanager
def open_parent(root: Path, parts: tuple[str, ...], *, allow_missing: bool = False):
    """Walk parent directories with O_NOFOLLOW: any symlink in the chain fails.
    (Distilled from foundation/files.py:file_parent.)"""
    fds: list[int] = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(fd)
        missing: list[str] = []
        for part in parts[:-1]:
            try:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                fds.append(fd)
            except FileNotFoundError:
                if not allow_missing:
                    raise
                missing.append(part)
                break
        yield fds[-1], missing
    finally:
        for fd in reversed(fds):
            os.close(fd)


def file_bytes(parent: int, name: str, *, max_bytes: int) -> tuple[bytes | None, os.stat_result | None]:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise Fault("invalid_file", "Expected a bounded regular text file without hardlinks.")
        if before.st_size > max_bytes:
            raise Fault("source_limit", "The selected source exceeds its byte limit.")
        raw = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise Fault("source_changed", "The file changed while reading; inspect its current version.")
    if len(raw) > max_bytes:
        raise Fault("source_limit", "The selected source exceeds its byte limit.")
    if b"\0" in raw:
        raise Fault("invalid_file", "Expected UTF-8 text without NUL bytes.")
    try:
        raw.decode("utf-8")
    except UnicodeError as error:
        raise Fault("invalid_file", "The selected file is not UTF-8 text.") from error
    return raw, after


def read_at(cfg: Config, parts: tuple[str, ...]) -> tuple[bytes | None, os.stat_result | None]:
    try:
        with open_parent(cfg.root, parts) as (parent, missing):
            if missing:
                return None, None
            return file_bytes(parent, parts[-1], max_bytes=cfg.max_file_bytes)
    except (FileNotFoundError, NotADirectoryError):
        return None, None
    except OSError as error:
        raise Fault("source_unavailable", "The selected path is unavailable (symlink or permission denied).") from error


def digest(raw: bytes | None) -> str:
    return hashlib.sha256(raw).hexdigest() if raw is not None else ""


def text_page(raw: bytes, offset: int = 0, *, end: int | None = None) -> dict:
    """One bounded UTF-8 page with redaction (OM Core text_page semantics)."""
    if (type(offset) is not int or not 0 <= offset <= len(raw)
            or (offset < len(raw) and raw[offset] & 0xC0 == 0x80)):
        raise Fault("invalid_range", "Use the returned next_offset at a UTF-8 character boundary.")
    stop = len(raw) if end is None else end
    if not offset <= stop <= len(raw):
        raise Fault("invalid_range", "The requested byte range is outside this source.")
    page_end = min(offset + TEXT_PAGE, stop)
    while True:
        while page_end > offset and page_end < len(raw) and raw[page_end] & 0xC0 == 0x80:
            page_end -= 1
        if page_end == offset and stop > offset:
            raise Fault("invalid_file", "No bounded UTF-8 page can be presented from this source.")
        text = raw[offset:page_end].decode("utf-8")
        clean = redact(text)
        if len(clean.encode("utf-8")) <= TEXT_PAGE:
            break
        page_end = offset + (page_end - offset) // 2
    return {"text": clean, "content_redacted": clean != text, "offset": offset,
            "next_offset": page_end, "has_more": page_end < len(raw), "size_bytes": len(raw)}


def glob_ok(pattern: str, relative: str) -> bool:
    """* and ? never cross '/' (OM Core path_glob rule)."""
    if not pattern:
        return True
    regex = "".join("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in pattern)
    return re.fullmatch(regex, relative) is not None


def scan_paths(root: Path, under: Path, path_glob: str) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(under):
        current = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SECRET_NAMES and d != ".git"
                             and not (d == ".env" or d.startswith(".env.")))
        for name in sorted(filenames):
            if len(found) >= MAX_SCAN_ENTRIES:
                return found
            item = current / name
            rel = item.relative_to(root).as_posix()
            try:
                file_parts(rel)
            except Fault:
                continue
            if path_glob and not glob_ok(path_glob, rel):
                continue
            if item.is_symlink():
                continue
            found.append(item)
    return found


def read_workspace_file(cfg: Config, path: str, offset: int = 0, expected_sha256: str = "",
                        start_line: int | None = None, line_count: int = 200) -> dict:
    if (type(offset) is not int or offset < 0 or type(line_count) is not int or not 1 <= line_count <= MAX_LINES
            or (start_line is not None and (type(start_line) is not int or start_line < 1 or offset != 0))):
        raise Fault("invalid_range", "Use 1-based lines, 1..500 lines per request, and offset zero in line mode.")
    if not SHA256.fullmatch(expected_sha256 or ""):
        raise Fault("invalid_hash", "Use the complete raw-byte SHA256 returned by the original read.")
    parts = file_parts(path)
    raw, metadata = read_at(cfg, parts)
    sha = digest(raw)
    if expected_sha256 and sha != expected_sha256:
        raise Fault("source_changed", "The file changed between pages; inspect its current version.")
    source = raw or b""
    stop = len(source)
    if start_line is not None:
        offset = 0
        for _ in range(start_line - 1):
            newline = source.find(b"\n", offset)
            if newline < 0:
                offset = len(source)
                break
            offset = newline + 1
        stop = offset
        for _ in range(line_count):
            newline = source.find(b"\n", stop)
            if newline < 0:
                stop = len(source)
                break
            stop = newline + 1
    page = text_page(source, offset, end=stop)
    end = page["next_offset"]
    page.update(start_line=source.count(b"\n", 0, offset) + 1 if end > offset else None,
                end_line=source.count(b"\n", 0, end - 1) + 1 if end > offset else None,
                range_complete=end == stop)
    if end < len(source) and (end == 0 or source[end - 1] == 10):
        page["next_line"] = source.count(b"\n", 0, end) + 1
    return {"path": path, "exists": raw is not None, "sha256": sha,
            "mtime_ns": metadata.st_mtime_ns if metadata else None,
            "workspace_identity": cfg.identity, **page}


def inspect_workspace(cfg: Config, action: str, path: str = ".", query: str = "",
                      path_glob: str = "", offset: int = 0, limit: int = 100) -> dict:
    if action not in ("list", "search"):
        raise Fault("invalid_action", "Use action 'list' or 'search'.")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise Fault("invalid_range", "Use offset >= 0 and limit 1..200.")
    rel = path.strip("/")
    under = cfg.root if rel in ("", ".") else cfg.root.joinpath(*file_parts(rel))
    resolved = under.resolve()
    if resolved != cfg.root and not resolved.is_relative_to(cfg.root):
        raise Fault("path_denied", "The selected directory is outside the workspace root.")
    if not resolved.is_dir():
        raise Fault("source_unavailable", "The selected directory is unavailable.")
    entries = scan_paths(cfg.root, resolved, path_glob)
    if action == "list":
        page = [{"path": p.relative_to(cfg.root).as_posix(), "kind": "file", "size_bytes": p.stat().st_size}
                for p in entries[offset:offset + limit]]
        return {"action": action, "entries": page, "total_scanned": len(entries),
                "offset": offset, "has_more": offset + limit < len(entries),
                "workspace_identity": cfg.identity}
    if not query or len(query) > 200:
        raise Fault("invalid_query", "Use a search query of 1..200 characters.")
    needle = query.casefold()
    matches: list[dict] = []
    scanned = 0
    for file in entries:
        scanned += 1
        if len(matches) >= MAX_MATCHES:
            break
        raw, _ = read_at(cfg, tuple(file.relative_to(cfg.root).parts))
        if raw is None:
            continue
        for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if needle in line.casefold():
                matches.append({"path": file.relative_to(cfg.root).as_posix(), "line": number,
                                "text": redact(line.strip())[:200]})
                if len(matches) >= MAX_MATCHES:
                    break
    return {"action": action, "matches": matches[offset:offset + limit], "total_matches": len(matches),
            "total_scanned": scanned, "has_more": offset + limit < len(matches),
            "workspace_identity": cfg.identity}


# ---- Guarded writes: SHA-256 CAS + atomic publish + change receipts ----------
def _publish(cfg: Config, parts: tuple[str, ...], content: bytes) -> str:
    """Atomic, no-follow publication into an existing parent directory."""
    try:
        with open_parent(cfg.root, parts) as (parent, _):
            token = os.urandom(8).hex()
            tmp = f".om-{token}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(tmp, dir_fd=parent)
                except OSError:
                    pass
            os.fsync(parent)
    except OSError as error:
        raise Fault("publish_failed", "The parent directory is missing or not writable; "
                                      "create parents first (e.g. run mkdir -p).") from error
    return digest(content)


def _change_fingerprint(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


def _change_lookup(cfg: Config, change_id: str, request: dict) -> dict | None:
    """Idempotency key: if this change_id was already recorded, identical requests
    OBSERVE the original result (never replay); different requests conflict."""
    if not change_id:
        return None
    path = cfg.state / "changes" / f"{change_id}.json"
    if not path.exists():
        return None
    original = load_json(path)
    if original.get("request_sha256") != _change_fingerprint(request):
        raise Fault("change_conflict", "This change ID already belongs to a different request.",
                    {"change_id": change_id}, effect="known_no_effect")
    return dict(original.get("result") or {}, observed=True)


def _change_record(cfg: Config, change_id: str, request: dict, result: dict) -> None:
    if not change_id:
        return
    directory = private_directory(cfg.state / "changes")
    private_directory(cfg.state / "locks")
    path = directory / f"{change_id}.json"
    with lease(cfg.state / "locks" / f"{change_id}.lock"):
        if path.exists():
            return
        save_json(path, {"change_id": change_id, "request_sha256": _change_fingerprint(request),
                         "result": result, "recorded_at": time.time()})


def write_workspace_file(cfg: Config, path: str, content: str, expected_sha256: str,
                         change_id: str = "", expected_workspace_identity: str = "") -> dict:
    if not cfg.writes:
        raise Fault("workspace_write_disabled", "The operator must enable workspace writes (--enable-writes).")
    if type(content) is not str or "\0" in content or len(content.encode("utf-8")) > MAX_EDIT_BYTES * 2:
        raise Fault("invalid_content", "Provide UTF-8 text without NUL bytes, at most 256 KiB.")
    if not SHA256.fullmatch(expected_sha256 or ""):
        raise Fault("invalid_hash", "Use the whole-file SHA256 from the original read; '' means the file must not exist.")
    if change_id and not HEX32.fullmatch(change_id):
        raise Fault("invalid_change_id", "Use a 32-character lowercase hexadecimal change ID.")
    if change_id and expected_workspace_identity and expected_workspace_identity != cfg.identity:
        raise Fault("context_mismatch", "The workspace identity changed; re-inspect before writing.")
    parts = file_parts(path)
    request = {"op": "write", "path": path, "content_sha256": digest(content.encode("utf-8")),
               "expected_sha256": expected_sha256}
    prior = _change_lookup(cfg, change_id, request)
    if prior is not None:
        return prior
    raw, _ = read_at(cfg, parts)
    observed = digest(raw)
    if observed != (expected_sha256 or ""):
        raise Fault("source_changed", "The current file hash does not match expected_sha256.",
                    {"expected_sha256": expected_sha256, "observed_sha256": observed},
                    effect="known_no_effect")
    result_sha = _publish(cfg, parts, content.encode("utf-8"))
    result = {"path": path, "status": "published", "sha256": result_sha,
              "previous_sha256": observed, "workspace_identity": cfg.identity,
              "change_id": change_id or None,
              "recovery": "durable" if change_id else "none: no change_id was supplied"}
    _change_record(cfg, change_id, request, result)
    return result


def edit_workspace_file(cfg: Config, path: str, edits: list[dict], expected_sha256: str,
                        change_id: str = "", dry_run: bool = False,
                        expected_workspace_identity: str = "") -> dict:
    if not cfg.writes:
        raise Fault("workspace_write_disabled", "The operator must enable workspace writes (--enable-writes).")
    if type(dry_run) is not bool or not isinstance(edits, list) or not 1 <= len(edits) <= 64:
        raise Fault("invalid_edits", "Provide 1..64 exact {old_text, new_text} edits and a boolean dry_run.")
    if not SHA256.fullmatch(expected_sha256 or "") or not expected_sha256:
        raise Fault("invalid_hash", "Echo the whole-file SHA256 from the original read.")
    if change_id and not HEX32.fullmatch(change_id):
        raise Fault("invalid_change_id", "Use a 32-character lowercase hexadecimal change ID.")
    if dry_run and change_id:
        raise Fault("invalid_change_id", "Preview requires an empty change_id; apply with a fresh ID.")
    if change_id and expected_workspace_identity and expected_workspace_identity != cfg.identity:
        raise Fault("context_mismatch", "The workspace identity changed; re-inspect before editing.")
    normalized = []
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) != {"old_text", "new_text"}
                or not isinstance(edit["old_text"], str) or not isinstance(edit["new_text"], str)
                or not edit["old_text"] or len(edit["old_text"].encode()) > MAX_EDIT_BYTES
                or len(edit["new_text"].encode()) > MAX_EDIT_BYTES):
            raise Fault("invalid_edits", "Each edit needs non-empty old_text and new_text, each at most 128 KiB.")
        normalized.append((edit["old_text"], edit["new_text"]))
    parts = file_parts(path)
    request = {"op": "edit", "path": path,
               "edits": [[o, n] for o, n in normalized],
               "expected_sha256": expected_sha256}
    prior = _change_lookup(cfg, change_id, request)
    if prior is not None:
        return prior
    raw, _ = read_at(cfg, parts)
    if raw is None:
        raise Fault("source_unavailable", "The target file does not exist.")
    observed = digest(raw)
    if observed != expected_sha256:
        raise Fault("source_changed", "The file changed since the original read.",
                    {"expected_sha256": expected_sha256, "observed_sha256": observed},
                    effect="known_no_effect")
    text = raw.decode("utf-8")
    plan, working = [], text
    for old, new in normalized:
        count = working.count(old)
        plan.append({"old_text": redact(old)[:120], "matches": count})
        if count != 1:
            raise Fault("ambiguous_edit", "Each old_text must match exactly once in sequence.",
                        {"matches": count, "plan": plan}, effect="known_no_effect")
        working = working.replace(old, new, 1)
    if dry_run:
        return {"path": path, "status": "preview", "plan": plan, "sha256": observed,
                "resulting_sha256": digest(working.encode("utf-8")), "workspace_identity": cfg.identity}
    result_sha = _publish(cfg, parts, working.encode("utf-8"))
    result = {"path": path, "status": "published", "plan": plan, "sha256": result_sha,
              "previous_sha256": observed, "workspace_identity": cfg.identity,
              "change_id": change_id or None,
              "recovery": "durable" if change_id else "none: no change_id was supplied"}
    _change_record(cfg, change_id, request, result)
    return result


# ---- Execution kernel: receipts, POSIX sessions, observe-never-replay --------
_LIVE: dict[str, subprocess.Popen] = {}
_STOPS: dict[str, str] = {}
_LIVE_LOCK = threading.Lock()


def execution_dir(cfg: Config, execution_id: str) -> Path:
    if not isinstance(execution_id, str) or not HEX32.fullmatch(execution_id):
        raise Fault("invalid_execution_id", "Use a 32-character lowercase hexadecimal execution ID.")
    return cfg.state / "executions" / execution_id


def _terminal_status(exit_code: int | None, stop_reason: str | None, timed_out: bool) -> str:
    if timed_out or stop_reason == "timeout":
        return "timed_out"
    if stop_reason == "cancel":
        return "cancelled"
    return "succeeded" if exit_code == 0 else "failed"


def _reap(cfg: Config, execution_id: str, proc: subprocess.Popen, timeout: int) -> None:
    directory = cfg.state / "executions" / execution_id
    timed_out = False
    try:
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _STOPS.setdefault(execution_id, "timeout")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except OSError:
                pass
            try:
                exit_code = proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    pass
                exit_code = proc.wait()
        stop_reason = _STOPS.get(execution_id)
        with lease(directory / "record.lock"):
            record = load_json(directory / "record.json")
            record["command"].update(exited=True, exit_code=exit_code, exited_at=time.time())
            record["status"] = _terminal_status(exit_code, stop_reason, timed_out)
            record["stop"] = {"reason": stop_reason}
            size = (directory / "output.log").stat().st_size if (directory / "output.log").exists() else 0
            record["output_bytes"] = size
            save_json(directory / "record.json", record)
    except Exception:  # never crash the server from a reaper thread
        try:
            with lease(directory / "record.lock"):
                record = load_json(directory / "record.json")
                record["status"] = "outcome_unknown"
                save_json(directory / "record.json", record)
        except Exception:
            pass
    finally:
        with _LIVE_LOCK:
            _LIVE.pop(execution_id, None)
            _STOPS.pop(execution_id, None)


def run_command(cfg: Config, execution_id: str, cwd: str, argv: list[str],
                timeout_seconds: int | None = None) -> dict:
    if not cfg.execution:
        raise Fault("execution_disabled", "The operator must explicitly enable command execution (--enable-execution).")
    directory = execution_dir(cfg, execution_id)
    timeout = cfg.command_timeout if timeout_seconds is None else timeout_seconds
    if (type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT
            or not isinstance(argv, list) or not argv or len(argv) > 100
            or any(not isinstance(a, str) or not a or "\0" in a or len(a) > 4096 for a in argv)):
        raise Fault("invalid_command", "Supply a program with 1..100 arguments (no NULs), timeout 1..86400 s.")
    working = (cfg.root if cwd in ("", ".") else Path(cwd) if Path(cwd).is_absolute()
               else cfg.root / cwd).resolve()
    if not working.is_dir() or not working.is_relative_to(cfg.root):
        raise Fault("invalid_cwd", "cwd must be a directory inside the workspace root.")
    request = {"machine": "local", "cwd": str(working), "argv": argv, "timeout_seconds": timeout}
    locks = private_directory(cfg.state / "locks")
    private_directory(cfg.state / "executions")
    directory.mkdir(mode=0o700, exist_ok=True)
    with lease(locks / f"{execution_id}.lock"):
        if (directory / "record.json").exists():
            record = load_json(directory / "record.json")
            if record["request"] != request:
                raise Fault("execution_conflict",
                            "This execution ID already belongs to a different request; read that original ID.",
                            {"execution_id": execution_id}, effect="known_no_effect")
            return read_execution(cfg, execution_id)  # observe, never replay
        record = {"format_version": 1, "execution_id": execution_id, "request": request,
                  "status": "running", "effect": "unknown_until_exit",
                  "command": {"pid": None, "started_at": time.time(), "exited": False,
                              "exit_code": None},
                  "stop": {"reason": None}, "output_bytes": 0}
        save_json(directory / "record.json", record)
        log = open(directory / "output.log", "ab")
        try:
            proc = subprocess.Popen(argv, cwd=working, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)  # OM Core posix_session containment
        except FileNotFoundError as error:
            log.close()
            record.update(status="failed", effect="known_no_effect",
                          command={**record["command"], "exited": True, "exit_code": 127})
            record["error"] = "spawn_failed: program not found"
            save_json(directory / "record.json", record)
            raise Fault("spawn_failed", "The program was not found; nothing was executed.",
                        {"execution_id": execution_id}, effect="known_no_effect") from error
        except OSError as error:
            log.close()
            record.update(status="failed", effect="known_no_effect",
                          command={**record["command"], "exited": True, "exit_code": 127})
            record["error"] = f"spawn_failed: {type(error).__name__}"
            save_json(directory / "record.json", record)
            raise Fault("spawn_failed", "The command could not be started; nothing was executed.",
                        {"execution_id": execution_id}, effect="known_no_effect") from error
        finally:
            try:
                log.close()
            except Exception:
                pass
        record["command"]["pid"] = proc.pid
        save_json(directory / "record.json", record)
        with _LIVE_LOCK:
            _LIVE[execution_id] = proc
        threading.Thread(target=_reap, args=(cfg, execution_id, proc, timeout), daemon=True).start()
    return read_execution(cfg, execution_id)


def read_execution(cfg: Config, execution_id: str, offset: int = 0, max_bytes: int = MAX_READ,
                   wait_seconds: float = 0) -> dict:
    directory = execution_dir(cfg, execution_id)
    if not directory.is_dir():
        raise Fault("execution_not_found", "No receipt exists for this execution ID; do not assume it ran.")
    if (type(offset) is not int or offset < 0 or type(max_bytes) is not int
            or not 1 <= max_bytes <= MAX_READ or type(wait_seconds) not in (int, float)
            or not 0 <= wait_seconds <= MAX_WAIT_SECONDS):
        raise Fault("invalid_range", "Use offset >= 0, max_bytes 1..65536, wait_seconds 0..5.")
    deadline = time.monotonic() + float(wait_seconds)
    while True:
        record = load_json(directory / "record.json")
        if record["status"] != "running" or time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    log = directory / "output.log"
    raw = b""
    if log.exists():
        with log.open("rb") as stream:
            stream.seek(offset)
            raw = stream.read(max_bytes)
    text = redact(raw.decode("utf-8", errors="replace"))
    return {"execution_id": execution_id, "status": record["status"], "request": record["request"],
            "command": record.get("command"), "stop": record.get("stop"),
            "error": record.get("error"), "output": {"offset": offset, "text": text,
            "next_offset": offset + len(raw), "has_more": log.exists()
            and offset + len(raw) < log.stat().st_size}}


def cancel_execution(cfg: Config, execution_id: str) -> dict:
    directory = execution_dir(cfg, execution_id)
    if not directory.is_dir():
        raise Fault("execution_not_found", "No receipt exists for this execution ID.")
    with _LIVE_LOCK:
        proc = _LIVE.get(execution_id)
    record = load_json(directory / "record.json")
    if proc is not None and proc.poll() is None:
        _STOPS[execution_id] = "cancel"
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)  # OM rule: cancel undoes nothing already done
        except OSError:
            pass
        return {"execution_id": execution_id, "status": "stopping",
                "message": "Termination requested for the owned session; read the receipt for the final status."}
    return {"execution_id": execution_id, "status": record["status"],
            "message": "No live worker in this server process; read the original receipt."}


def list_executions(cfg: Config, cursor: str = "", limit: int = 20) -> dict:
    if (cursor and not HEX32.fullmatch(cursor)) or type(limit) is not int or not 1 <= limit <= 100:
        raise Fault("invalid_range", "Use a 32-hex cursor (or empty) and limit 1..100.")
    root = cfg.state / "executions"
    ids = sorted(p.name for p in root.iterdir() if p.is_dir() and HEX32.fullmatch(p.name)) if root.is_dir() else []
    if cursor:
        ids = [i for i in ids if i > cursor]
    page, unreadable = [], []
    for name in ids[:limit]:
        try:
            record = load_json(root / name / "record.json")
            page.append({"execution_id": name, "status": record.get("status"),
                         "argv": record.get("request", {}).get("argv")})
        except Fault:
            unreadable.append(name)
    return {"executions": page, "next_cursor": page[-1]["execution_id"] if len(ids) > limit and page else "",
            "unreadable": unreadable}


# ---- MCP tool surface -------------------------------------------------------
TOOLS: list[dict] = [
    {"name": "get_capabilities",
     "description": "Report grants, workspace root identity and limits. Not live health.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "inspect_workspace",
     "description": ("List files or search text in the workspace. Reads are bounded; "
                     "* in path_glob never crosses '/'. Listing is not proof the tree is idle."),
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "search"]},
         "path": {"type": "string", "description": "Relative directory; default '.'"},
         "query": {"type": "string", "description": "Case-insensitive substring for action=search"},
         "path_glob": {"type": "string", "description": "Filter like 'src/*.py'"},
         "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
         "required": ["action"], "additionalProperties": False}},
    {"name": "read_file",
     "description": ("Read bounded UTF-8 source with whole-file SHA256 and 32 KiB pages. "
                     "Continue with next_offset and the same expected_sha256. "
                     "Heed has_more and content_redacted; never reconstruct full files from redacted pages."),
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string"},
         "offset": {"type": "integer", "minimum": 0},
         "expected_sha256": {"type": "string", "description": "Hash from the previous page; omit on first read"},
         "start_line": {"type": "integer", "minimum": 1, "description": "1-based; requires offset=0"},
         "line_count": {"type": "integer", "minimum": 1, "maximum": 500}},
         "required": ["path"], "additionalProperties": False}},
    {"name": "write_file",
     "description": ("Publish one whole file under SHA-256 precondition. expected_sha256='' "
                     "means the file must not exist. Optional 32-hex change_id gives a durable "
                     "receipt (same ID observes, never replays). No parent creation. "
                     "Requires the workspace-write grant."),
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"},
         "expected_sha256": {"type": "string", "description": "Current whole-file hash, or '' for a new file"},
         "change_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
         "expected_workspace_identity": {"type": "string"}},
         "required": ["path", "content", "expected_sha256"], "additionalProperties": False}},
    {"name": "edit_file",
     "description": ("Replace exact non-overlapping fragments in one file; each old_text must "
                     "match exactly once in sequence. dry_run=true previews and requires an empty "
                     "change_id; apply requires a caller-generated 32-hex change_id. "
                     "File changes are not test results."),
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string"},
         "edits": {"type": "array", "minItems": 1, "maxItems": 64, "items": {
             "type": "object", "properties": {"old_text": {"type": "string"}, "new_text": {"type": "string"}},
             "required": ["old_text", "new_text"], "additionalProperties": False}},
         "expected_sha256": {"type": "string"},
         "change_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
         "dry_run": {"type": "boolean"},
         "expected_workspace_identity": {"type": "string"}},
         "required": ["path", "edits", "expected_sha256"], "additionalProperties": False}},
    {"name": "run_command",
     "description": ("Start one authorized native command inside the workspace root. You choose "
                     "the 32-hex execution_id before launch; save it. The same ID + request returns "
                     "the original observation (never a replay); a different request with the same "
                     "ID fails. Requires the execution grant."),
     "inputSchema": {"type": "object", "properties": {
         "execution_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
         "cwd": {"type": "string", "description": "Absolute or root-relative directory inside the workspace"},
         "argv": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "string"}},
         "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 86400}},
         "required": ["execution_id", "cwd", "argv"], "additionalProperties": False}},
    {"name": "read_execution",
     "description": ("Read the original receipt and raw-output cursor. wait_seconds (0..5) only "
                     "affects observation. Unknown evidence never permits a new ID for the same work."),
     "inputSchema": {"type": "object", "properties": {
         "execution_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
         "offset": {"type": "integer", "minimum": 0},
         "max_bytes": {"type": "integer", "minimum": 1, "maximum": 65536},
         "wait_seconds": {"type": "number", "minimum": 0, "maximum": 5}},
         "required": ["execution_id"], "additionalProperties": False}},
    {"name": "cancel_execution",
     "description": "Request cancellation of this owned execution only; read its receipt after. Does not undo previous effects.",
     "inputSchema": {"type": "object", "properties": {
         "execution_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}},
         "required": ["execution_id"], "additionalProperties": False}},
    {"name": "list_executions",
     "description": "Find original execution IDs in stable ID order; follow next_cursor. Not task recovery.",
     "inputSchema": {"type": "object", "properties": {
         "cursor": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
         "additionalProperties": False}},
]


def get_capabilities(cfg: Config) -> dict:
    return {
        "server": SERVER_NAME, "version": VERSION,
        "derived_from": "OpenMachine (OM Core) MIT — local-first slice",
        "workspace_root": str(cfg.root), "workspace_identity": cfg.identity,
        "grants": {"workspace_write_enabled": cfg.writes, "execution_enabled": cfg.execution},
        "limits": {"text_page_bytes": TEXT_PAGE, "max_file_bytes": cfg.max_file_bytes,
                   "max_lines_per_read": MAX_LINES, "max_scan_entries": MAX_SCAN_ENTRIES,
                   "default_timeout_seconds": cfg.command_timeout, "max_timeout_seconds": MAX_TIMEOUT,
                   "max_read_bytes": MAX_READ},
        "execution": {"receipts": "original-records", "id_policy": "caller-supplied 32-hex, observe-never-replay",
                      "containment": "posix_session", "controls": ["cancel"]},
        "workspace": {"sha256_preconditions": True, "exact_edits": True, "change_receipts": True},
        "boundary": "Native account access, not a sandbox. Files and logs returned to a client may leave this machine.",
    }


def dispatch(cfg: Config, name: str, arguments: dict) -> dict:
    if name == "get_capabilities":
        return get_capabilities(cfg)
    if name == "inspect_workspace":
        return inspect_workspace(cfg, arguments["action"], arguments.get("path", "."),
                                 arguments.get("query", ""), arguments.get("path_glob", ""),
                                 arguments.get("offset", 0), arguments.get("limit", 100))
    if name == "read_file":
        return read_workspace_file(cfg, arguments["path"], arguments.get("offset", 0),
                                   arguments.get("expected_sha256", ""),
                                   arguments.get("start_line"), arguments.get("line_count", 200))
    if name == "write_file":
        return write_workspace_file(cfg, arguments["path"], arguments["content"],
                                    arguments["expected_sha256"], arguments.get("change_id", ""),
                                    arguments.get("expected_workspace_identity", ""))
    if name == "edit_file":
        return edit_workspace_file(cfg, arguments["path"], arguments["edits"],
                                   arguments["expected_sha256"], arguments.get("change_id", ""),
                                   arguments.get("dry_run", False),
                                   arguments.get("expected_workspace_identity", ""))
    if name == "run_command":
        return run_command(cfg, arguments["execution_id"], arguments.get("cwd", "."),
                           arguments["argv"], arguments.get("timeout_seconds"))
    if name == "read_execution":
        return read_execution(cfg, arguments["execution_id"], arguments.get("offset", 0),
                              arguments.get("max_bytes", MAX_READ), arguments.get("wait_seconds", 0))
    if name == "cancel_execution":
        return cancel_execution(cfg, arguments["execution_id"])
    if name == "list_executions":
        return list_executions(cfg, arguments.get("cursor", ""), arguments.get("limit", 20))
    raise Fault("unknown_tool", f"Unknown tool: {name}")


# ---- MCP stdio transport: newline-delimited JSON-RPC -------------------------
def _reply_payload(payload: dict, failed: bool) -> dict:
    text = json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "structuredContent": payload, "isError": failed}


def handle_request(cfg: Config, message: dict) -> dict | None:
    method, params, msg_id = message.get("method"), message.get("params") or {}, message.get("id")
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_SUPPORTED else PROTOCOL_FALLBACK
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
            "instructions": ("Local-first project runtime distilled from Open Machine. "
                             "Read get_capabilities first. Writes and commands are disabled "
                             "unless the operator's grants allow them. Preserve execution_id / "
                             "change_id values; identical retries observe, never replay.")}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return {"jsonrpc": "2.0", "id": msg_id, "result": _reply_payload(
                {"error": {"code": "invalid_tool_call", "message": "Provide a tool name and an arguments object."}}, True)}
        try:
            payload = dispatch(cfg, name, arguments)
            failed = isinstance(payload.get("error"), dict)
            return {"jsonrpc": "2.0", "id": msg_id, "result": _reply_payload(payload, failed)}
        except Fault as error:
            payload = {**(error.evidence or {}), "error": {"code": error.code, "message": str(error)},
                       "effect": error.effect}
            return {"jsonrpc": "2.0", "id": msg_id, "result": _reply_payload(payload, True)}
        except (KeyError, TypeError) as error:
            return {"jsonrpc": "2.0", "id": msg_id, "result": _reply_payload(
                {"error": {"code": "invalid_arguments",
                           "message": f"Missing or invalid tool arguments: {error}"}}, True)}
        except Exception as error:  # bounded public error; never leak internals
            return {"jsonrpc": "2.0", "id": msg_id, "result": _reply_payload(
                {"error": {"code": "internal_error", "message": type(error).__name__}}, True)}
    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve_stdio(cfg: Config) -> None:
    print(f"{SERVER_NAME} {VERSION} ready on stdio; root={cfg.root} "
          f"writes={cfg.writes} execution={cfg.execution}", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}
            print(json.dumps(response), flush=True)
            continue
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            continue
        try:
            response = handle_request(cfg, message)
        except Exception as error:
            response = {"jsonrpc": "2.0", "id": message.get("id"),
                        "error": {"code": -32603, "message": type(error).__name__}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog=SERVER_NAME, description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=str, help="Absolute project workspace root")
    parser.add_argument("--config", type=Path, help="Optional TOML config (see config.toml)")
    parser.add_argument("--state-dir", type=Path, help="Private state directory (default ~/.nano-airbridge)")
    parser.add_argument("--enable-writes", action="store_true", help="Grant workspace writes")
    parser.add_argument("--enable-execution", action="store_true", help="Grant command execution")
    args = parser.parse_args()
    try:
        cfg = load_config(args)
    except Fault as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    try:
        serve_stdio(cfg)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
