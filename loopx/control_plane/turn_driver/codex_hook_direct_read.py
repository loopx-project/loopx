"""Lock-free in-process JSON pointer reader and fact extractor for Codex compact hooks.

Eliminates subprocess `loopx quota context-read` calls and Node TS IPC
startup lock contention inside `SessionStart(compact)` hooks (leaving
`PostCompact` inert `{}` for Codex CLI hook-output compatibility)
while verifying SHA-256 integrity, internal `eof=true` UTF-8 pagination,
wake prompt deduplication, heavy shell prefetch retirement, and automatic
extraction of `failure_facts` + `open_obligations`.

Opt-in / default-off unless explicitly enabled via hook configuration,
event payload (`compact_direct_read`), or `LOOPX_CODEX_HOOK_DIRECT_READ=1`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import time
from typing import Any

from .decision_capture import read_context_capture_file

COMPACT_DIRECT_READ_SCHEMA_VERSION = "loopx_compact_hook_direct_read_v0"
COMPACT_DIRECT_READ_SELECTION_SCHEMA = "context_capture_selection_v1"
DEFAULT_DIRECT_READ_POINTERS = (
    "/goal_id",
    "/selected_todo",
    "/plan_summary",
    "/work_contract",
    "/workspace_guard",
    "/settlement_plan",
    "/interaction_contract/agent_channel/work_context",
)
DEFAULT_HOOK_PAGE_BYTES = 16_384
MIN_HOOK_PAGE_BYTES = 128
MAX_HOOK_PAGE_BYTES = 65_536
DEFAULT_HOOK_MAX_OUTPUT_BYTES = 24_576
MIN_HOOK_MAX_OUTPUT_BYTES = 512
MAX_HOOK_MAX_OUTPUT_BYTES = 65_536
MAX_EXTERNAL_HISTORY_BYTES = 8 * 1024 * 1024
MAX_POINTERS_PER_HOOK = 24
MAX_FACTS_COUNT = 8
MAX_FACT_CHARS = 180

_ARRAY_INDEX_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)$")
_INVALID_ESCAPE_PATTERN = re.compile(r"~(?:[^01]|$)")
_FAILED_HISTORY_STATUSES = frozenset({
    "failed",
    "error",
    "timed_out",
    "timeout",
    "nonzero_exit",
    "invalid",
    "rejected",
    "cancelled",
    "aborted",
})
_UNSAFE_PATH_FRAGMENT = re.compile(
    r"(?:(?:/Users|/home|/private/var|/root)/[^\s\"'`,;:\]\)]+|[A-Za-z]:\\[^\s\"'`,;:\]\)]+)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+"
)
_READ_ONLY_GIT_FLAGS_BY_SUBCOMMAND: dict[str, frozenset[str]] = {
    "status": frozenset({"--short", "-s", "--branch", "-b", "--porcelain", "-u", "--untracked-files=no"}),
    "rev-parse": frozenset({"HEAD", "--short", "--abbrev-ref", "--is-inside-work-tree", "--verify"}),
    "diff": frozenset({"--stat", "--name-only", "--name-status", "--shortstat", "--cached", "--staged", "HEAD"}),
    "log": frozenset({"--oneline", "-n", "--stat", "--name-only", "--no-decorate"}),
}
_FORBIDDEN_GIT_FLAGS = frozenset({"--output", "--ext-diff", "--open-files-in-pager", "-O", "--exec"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(minimum, min(value, maximum))


def _sanitize_public_text(text: Any, *, limit: int = MAX_FACT_CHARS) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""
    cleaned = _UNSAFE_PATH_FRAGMENT.sub("<redacted-path>", cleaned)
    cleaned = _SECRET_ASSIGNMENT.sub("<redacted-secret>", cleaned)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _dedupe_capped(items: Iterable[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in items:
        item = _sanitize_public_text(raw)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def resolve_compact_direct_read_config(
    state: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return normalized opt-in compact direct-read policy, or None when off."""
    ev_cfg = event.get("compact_direct_read") if isinstance(event, Mapping) else None
    st_cfg = (
        state.get("compact_direct_read_policy")
        if isinstance(state, Mapping)
        else None
    )
    if ev_cfg is False or (isinstance(ev_cfg, Mapping) and ev_cfg.get("enabled") is False):
        return None
    if ev_cfg is True:
        raw_cfg: dict[str, Any] = {"enabled": True}
    elif isinstance(ev_cfg, Mapping) and ev_cfg.get("enabled") is True:
        raw_cfg = dict(ev_cfg)
    elif isinstance(st_cfg, Mapping) and st_cfg.get("enabled") is True:
        raw_cfg = dict(st_cfg)
    elif os.environ.get("LOOPX_CODEX_HOOK_DIRECT_READ") == "1":
        raw_cfg = {"enabled": True}
    else:
        return None

    return {
        "enabled": True,
        "lock_free": bool(raw_cfg.get("lock_free", True)),
        "deduplicate": bool(raw_cfg.get("deduplicate", True)),
        "extract_facts": bool(raw_cfg.get("extract_facts", True)),
        "retire_shell_prefetch": bool(raw_cfg.get("retire_shell_prefetch", True)),
        "pointers": raw_cfg.get("pointers"),
        "page_bytes": _clamp_int(
            raw_cfg.get("page_bytes"),
            default=DEFAULT_HOOK_PAGE_BYTES,
            minimum=MIN_HOOK_PAGE_BYTES,
            maximum=MAX_HOOK_PAGE_BYTES,
        ),
        "max_output_bytes": _clamp_int(
            raw_cfg.get("max_output_bytes"),
            default=DEFAULT_HOOK_MAX_OUTPUT_BYTES,
            minimum=MIN_HOOK_MAX_OUTPUT_BYTES,
            maximum=MAX_HOOK_MAX_OUTPUT_BYTES,
        ),
        "run_history_paths": raw_cfg.get("run_history_paths"),
        "explore_log_paths": raw_cfg.get("explore_log_paths"),
        "decision_file": raw_cfg.get("decision_file"),
        "runtime_root": raw_cfg.get("runtime_root"),
    }


def is_compact_direct_read_enabled(
    state: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
) -> bool:
    """Report whether Phase-2 lock-free compact hook direct-read is opted in."""
    return resolve_compact_direct_read_config(state, event) is not None


def validate_rfc6901_pointer(pointer: str) -> list[str]:
    """Validate an RFC 6901 pointer and return decoded reference tokens."""
    if not isinstance(pointer, str):
        raise ValueError("JSON pointer must be a string")
    if pointer == "":
        return []
    if not pointer.startswith("/") or _INVALID_ESCAPE_PATTERN.search(pointer):
        raise ValueError(f"invalid RFC 6901 JSON pointer: {pointer!r}")
    return [
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer[1:].split("/")
    ]


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    """Resolve one RFC 6901 pointer against a parsed JSON object."""
    tokens = validate_rfc6901_pointer(pointer)
    current: Any = document
    for token in tokens:
        if isinstance(current, list):
            if not _ARRAY_INDEX_PATTERN.fullmatch(token):
                raise KeyError(f"invalid array index token {token!r} in {pointer!r}")
            index = int(token)
            if index >= len(current):
                raise KeyError(f"array index out of range in {pointer!r}")
            current = current[index]
        elif isinstance(current, dict):
            if token not in current:
                raise KeyError(f"missing pointer {pointer!r}")
            current = current[token]
        else:
            raise KeyError(f"non-container traversed by pointer {pointer!r}")
    return current


def _paginate_utf8_bytes_to_eof(
    encoded: bytes,
    *,
    page_bytes: int,
    max_output_bytes: int,
) -> dict[str, Any]:
    """Step contiguous UTF-8 slices until eof=true within bounded byte ceiling."""
    total_bytes = len(encoded)
    if total_bytes > max_output_bytes:
        return {
            "ok": False,
            "eof": False,
            "truncated": True,
            "total_bytes": total_bytes,
            "max_output_bytes": max_output_bytes,
            "pages_read": 0,
            "reason": "selection_exceeds_max_output_bytes",
        }
    if total_bytes == 0:
        return {
            "ok": True,
            "eof": True,
            "truncated": False,
            "total_bytes": 0,
            "pages_read": 1,
            "text": "",
        }

    offset = 0
    pages_read = 0
    chunks: list[str] = []
    step = max(4, page_bytes)
    while offset < total_bytes:
        end = min(offset + step, total_bytes)
        while end < total_bytes and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        if end <= offset:
            raise ValueError("UTF-8 character exceeds page_bytes budget")
        piece = encoded[offset:end].decode("utf-8")
        pages_read += 1
        eof = end == total_bytes
        next_offset = None if eof else end
        if next_offset != (None if eof else offset + len(piece.encode("utf-8"))):
            raise ValueError("non-contiguous internal pagination cursor")
        chunks.append(piece)
        offset = end
        if eof:
            break
    reassembled = "".join(chunks)
    if len(reassembled.encode("utf-8")) != total_bytes:
        raise ValueError("reassembled byte count mismatch before eof")
    return {
        "ok": True,
        "eof": True,
        "truncated": False,
        "total_bytes": total_bytes,
        "pages_read": pages_read,
        "text": reassembled,
    }


def read_json_pointers_paginated(
    capture_path: str | Path,
    capture_sha256: str,
    pointers: Sequence[str] | None = None,
    *,
    private: bool = True,
    page_bytes: int = DEFAULT_HOOK_PAGE_BYTES,
    max_output_bytes: int = DEFAULT_HOOK_MAX_OUTPUT_BYTES,
    require_all_pointers: bool = False,
) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    """Read verified capture JSON pointers in-process without locks until eof=true."""
    started = time.perf_counter()
    explicit_pointers = pointers is not None
    requested_raw = list(pointers) if pointers is not None else list(DEFAULT_DIRECT_READ_POINTERS)
    requested = list(dict.fromkeys(requested_raw))
    if len(requested) > MAX_POINTERS_PER_HOOK:
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return (
            False,
            {
                "ok": False,
                "sha256_verified": False,
                "eof": False,
                "lock_free": True,
                "read_latency_ms": elapsed_ms,
                "reason": "too_many_json_pointers",
                "requested_count": len(requested),
                "max_pointers": MAX_POINTERS_PER_HOOK,
            },
            None,
        )
    try:
        resolved_path, raw, payload = read_context_capture_file(
            str(capture_path), capture_sha256, private=private,
        )
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return (
            False,
            {
                "ok": False,
                "sha256_verified": False,
                "eof": False,
                "lock_free": True,
                "read_latency_ms": elapsed_ms,
                "reason": "capture_verification_failed",
                "error": _sanitize_public_text(str(exc)),
            },
            None,
        )

    selections: list[dict[str, Any]] = []
    missing_pointers: list[str] = []
    for pointer in requested:
        try:
            validate_rfc6901_pointer(pointer)
        except ValueError as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            return (
                False,
                {
                    "ok": False,
                    "sha256_verified": True,
                    "eof": False,
                    "lock_free": True,
                    "read_latency_ms": elapsed_ms,
                    "reason": "invalid_json_pointer",
                    "error": str(exc),
                },
                payload,
            )
        try:
            value = resolve_json_pointer(payload, pointer)
        except KeyError:
            missing_pointers.append(pointer)
            if require_all_pointers:
                elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
                return (
                    False,
                    {
                        "ok": False,
                        "sha256_verified": True,
                        "eof": False,
                        "lock_free": True,
                        "read_latency_ms": elapsed_ms,
                        "reason": "missing_required_pointer",
                        "missing_pointers": missing_pointers,
                    },
                    payload,
                )
            continue
        selections.append({"pointer": pointer, "value": value})

    if explicit_pointers and requested and not selections:
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return (
            False,
            {
                "ok": False,
                "sha256_verified": True,
                "eof": False,
                "lock_free": True,
                "read_latency_ms": elapsed_ms,
                "reason": "no_requested_pointers_found",
                "missing_pointers": missing_pointers,
            },
            payload,
        )

    selection_doc = {
        "schema_version": COMPACT_DIRECT_READ_SELECTION_SCHEMA,
        "selections": selections,
    }
    encoded = json.dumps(
        selection_doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    selection_sha256 = hashlib.sha256(encoded).hexdigest()
    paged = _paginate_utf8_bytes_to_eof(
        encoded,
        page_bytes=page_bytes,
        max_output_bytes=max_output_bytes,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    if paged.get("ok") is not True:
        return (
            False,
            {
                "ok": False,
                "sha256_verified": True,
                "eof": False,
                "truncated": True,
                "lock_free": True,
                "source_path": str(resolved_path),
                "source_sha256": capture_sha256,
                "selection_sha256": selection_sha256,
                "total_bytes": paged["total_bytes"],
                "max_output_bytes": max_output_bytes,
                "pages_read": 0,
                "read_latency_ms": elapsed_ms,
                "reason": paged["reason"],
            },
            payload,
        )

    return (
        True,
        {
            "ok": True,
            "schema_version": COMPACT_DIRECT_READ_SCHEMA_VERSION,
            "sha256_verified": True,
            "eof": True,
            "lock_free": True,
            "source_path": str(resolved_path),
            "source_bytes": len(raw),
            "source_sha256": capture_sha256,
            "selection_sha256": selection_sha256,
            "selections": selections,
            "rendered_text": paged["text"],
            "rendered_bytes": paged["total_bytes"],
            "pages_read": paged["pages_read"],
            "missing_pointers": missing_pointers,
            "read_latency_ms": elapsed_ms,
        },
        payload,
    )


def _extract_select_flags_from_argv(tokens: Sequence[str]) -> list[str]:
    extracted: list[str] = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token == "--select" and idx + 1 < len(tokens):
            extracted.append(tokens[idx + 1])
            idx += 2
            continue
        if token.startswith("--select="):
            extracted.append(token.split("=", 1)[1])
        idx += 1
    return extracted


def _is_lightweight_git_probe(tokens: Sequence[str]) -> bool:
    if len(tokens) < 2 or tokens[0] != "git":
        return False
    sub = tokens[1]
    allowed_flags = _READ_ONLY_GIT_FLAGS_BY_SUBCOMMAND.get(sub)
    if allowed_flags is None:
        return False
    for token in tokens[2:]:
        if token in _FORBIDDEN_GIT_FLAGS or any(
            token.startswith(f + "=") for f in _FORBIDDEN_GIT_FLAGS
        ):
            return False
        if token.startswith("-"):
            if sub == "log" and re.fullmatch(r"-[0-9]{1,3}", token):
                continue
            if token in allowed_flags:
                continue
            return False
        elif sub == "log" and token.isdigit():
            continue
        elif token not in allowed_flags:
            return False
    return True


def retire_shell_prefetch_command(command: Any) -> dict[str, Any]:
    """Retire subprocess `loopx quota context-read` & heavy shell prefetch commands."""
    if command is None:
        return {"present": False, "retired": False, "retired_commands": [], "extracted_pointers": [], "allowed_git_probe": None}
    is_list_of_subcommands = (
        isinstance(command, list)
        and bool(command)
        and (
            any(isinstance(item, list) for item in command)
            or (
                all(isinstance(item, str) for item in command)
                and isinstance(command[0], str)
                and " " in command[0].strip()
                and not Path(command[0].strip()).is_file()
            )
        )
    )
    commands = command if is_list_of_subcommands else [command]

    extracted_pointers: list[str] = []
    retired_labels: list[str] = []
    allowed_git_probe: list[str] | None = None
    for item in commands:
        if isinstance(item, str):
            stripped = item.strip()
            if not stripped:
                continue
            try:
                tokens = shlex.split(stripped)
            except ValueError:
                retired_labels.append("malformed_shell_prefetch")
                continue
        elif isinstance(item, Sequence):
            tokens = [str(part) for part in item if str(part)]
        else:
            retired_labels.append("unsupported_prefetch_command")
            continue
        if not tokens:
            continue
        joined = " ".join(tokens)
        if any(meta in joined for meta in ("&&", "||", ";", "|", "$(", "`", ">", "<")):
            retired_labels.append(f"retired_shell_chain:{tokens[0]}")
            continue
        base = Path(tokens[0]).name
        is_context_read = "quota" in tokens and "context-read" in tokens and (
            base.startswith("loopx") or "loopx.entrypoint" in joined or base.startswith("python")
        )
        if is_context_read:
            extracted_pointers.extend(_extract_select_flags_from_argv(tokens))
            retired_labels.append("retired_subprocess:loopx_quota_context_read")
            continue
        if _is_lightweight_git_probe(tokens) and allowed_git_probe is None:
            allowed_git_probe = tokens
            continue
        retired_labels.append(f"retired_heavy_external_shell:{base}")

    return {
        "present": True,
        "retired": bool(retired_labels),
        "retired_commands": retired_labels,
        "extracted_pointers": list(dict.fromkeys(extracted_pointers)),
        "allowed_git_probe": allowed_git_probe,
    }


def _read_bounded_regular_bytes(path: Path) -> bytes | None:
    try:
        resolved = path.expanduser().absolute()
        resolved = resolved.parent.resolve(strict=True) / resolved.name
        if resolved.is_symlink():
            return None
        open_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(resolved, open_flags)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_EXTERNAL_HISTORY_BYTES:
                return None
            raw = stream.read(info.st_size + 1)
        return raw if len(raw) == info.st_size else None
    except OSError:
        return None


def _read_bounded_regular_json(path: Path) -> Any | None:
    raw = _read_bounded_regular_bytes(path)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None


def _read_bounded_regular_jsonl(path: Path) -> list[dict[str, Any]]:
    raw = _read_bounded_regular_bytes(path)
    if raw is None:
        return []
    items: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(record, dict):
            items.append(record)
    return items


def _candidate_run_history_paths(
    *,
    cwd: str | Path | None,
    explicit_paths: Sequence[str | Path] | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        if p not in seen:
            seen.add(p)
            candidates.append(p)

    for raw in explicit_paths or ():
        add(Path(raw))
    if cwd is not None:
        base = Path(cwd)
        for root in (base, base.parent, base.parent.parent):
            for name in ("run_history.json", "game_history.json"):
                add(root / name)
            if root.parent == root:
                break
    for env_name in ("LOOPX_TRIAL_DIR",):
        env_val = os.environ.get(env_name)
        if env_val:
            for name in ("run_history.json", "game_history.json"):
                add(Path(env_val) / name)
    return candidates


def _candidate_explore_log_paths(
    *,
    goal_id: str | None,
    runtime_root: str | Path | None,
    explicit_paths: Sequence[str | Path] | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        if p not in seen:
            seen.add(p)
            candidates.append(p)

    for raw in explicit_paths or ():
        add(Path(raw))
    if (
        isinstance(goal_id, str)
        and goal_id
        and goal_id == Path(goal_id).name
        and goal_id not in (".", "..")
        and runtime_root is not None
    ):
        add(Path(runtime_root).expanduser() / "goals" / goal_id / "explore-result-log.jsonl")
    return candidates


def _is_failed_run_history_entry(entry: Mapping[str, Any]) -> bool:
    status = str(entry.get("status") or "").strip().lower()
    if status in _FAILED_HISTORY_STATUSES:
        return True
    if entry.get("valid") is False:
        return True
    for err_key in ("error", "error_kind", "failure_reason", "diagnostic_code"):
        val = entry.get(err_key)
        if isinstance(val, str) and val.strip():
            return True
    pass_rate = entry.get("pass_rate")
    if (
        isinstance(pass_rate, (int, float))
        and not isinstance(pass_rate, bool)
        and math.isfinite(float(pass_rate))
        and float(pass_rate) < 1.0
        and status != "pending"
    ):
        return True
    return False


def _format_run_history_failure(entry: Mapping[str, Any]) -> str:
    round_label = str(entry.get("round") or entry.get("submission_id") or entry.get("attempt") or "unknown")
    status = str(entry.get("status") or "unknown")
    parts = [f"run_history[{round_label}]: status={status}"]
    if entry.get("valid") is False:
        parts.append("valid=false")
    if isinstance(entry.get("score"), (int, float)) and not isinstance(entry.get("score"), bool):
        parts.append(f"score={entry['score']}")
    if isinstance(entry.get("pass_rate"), (int, float)) and not isinstance(entry.get("pass_rate"), bool):
        parts.append(f"pass_rate={entry['pass_rate']}")
    for err_key in ("error_kind", "failure_reason", "diagnostic_code", "error"):
        val = entry.get(err_key)
        if isinstance(val, str) and val.strip():
            parts.append(f"err={_sanitize_public_text(val, limit=80)}")
            break
    return " ".join(parts)


def extract_failure_facts_from_sources(
    *,
    cwd: str | Path | None = None,
    goal_id: str | None = None,
    runtime_root: str | Path | None = None,
    run_history_paths: Sequence[str | Path] | None = None,
    explore_log_paths: Sequence[str | Path] | None = None,
    limit: int = MAX_FACTS_COUNT,
) -> dict[str, Any]:
    """Auto-extract bounded public-safe failure facts from run_history and explore logs."""
    facts: list[str] = []
    sources: list[str] = []
    total_failed_entries = 0

    for candidate in _candidate_run_history_paths(cwd=cwd, explicit_paths=run_history_paths):
        doc = _read_bounded_regular_json(candidate)
        if not isinstance(doc, dict):
            continue
        entries = doc.get("entries")
        if not isinstance(entries, list):
            continue
        sources.append(candidate.name)
        for entry in reversed(entries):
            if not isinstance(entry, dict) or not _is_failed_run_history_entry(entry):
                continue
            total_failed_entries += 1
            if len(facts) < limit:
                facts.append(_format_run_history_failure(entry))

    for candidate in _candidate_explore_log_paths(
        goal_id=goal_id,
        runtime_root=runtime_root,
        explicit_paths=explore_log_paths,
    ):
        records = _read_bounded_regular_jsonl(candidate)
        if not records:
            continue
        sources.append(candidate.name)
        superseded_ids = {
            str(item.get("supersedes"))
            for item in records
            if isinstance(item.get("supersedes"), str) and item.get("supersedes")
        }
        latest_node_status: dict[str, str] = {}
        for item in records:
            if item.get("event_kind") == "node" and isinstance(item.get("result_id"), str):
                latest_node_status[item["result_id"]] = str(item.get("status") or "")
        for item in reversed(records):
            if len(facts) >= limit:
                break
            rid = str(item.get("result_id") or "")
            if rid and rid in superseded_ids:
                continue
            kind = item.get("event_kind")
            status = str(item.get("status") or "").lower()
            if kind == "node" and status in ("blocked", "dead_end"):
                if rid and latest_node_status.get(rid) == "resolved":
                    continue
                total_failed_entries += 1
                title = _sanitize_public_text(item.get("title") or rid, limit=70)
                detail = _sanitize_public_text(
                    item.get("blocked_reason") or item.get("summary") or "", limit=80,
                )
                msg = f"explore[{rid or 'node'}]({status}): {title}"
                if detail:
                    msg += f" - {detail}"
                facts.append(msg)
            elif kind == "finding" and status == "refuted":
                total_failed_entries += 1
                title = _sanitize_public_text(item.get("title") or rid, limit=70)
                detail = _sanitize_public_text(item.get("summary") or "", limit=80)
                msg = f"explore[{rid or 'finding'}](refuted): {title}"
                if detail:
                    msg += f" - {detail}"
                facts.append(msg)

    return {
        "failure_facts": _dedupe_capped(facts, limit=limit),
        "total_failures_observed": total_failed_entries,
        "sources_inspected": list(dict.fromkeys(sources)),
    }


def extract_open_obligations_from_decision(
    payload: Mapping[str, Any] | None,
    *,
    limit: int = MAX_FACTS_COUNT,
) -> list[str]:
    """Auto-extract bounded active obligations from verified decision/capture JSON."""
    if not isinstance(payload, Mapping):
        return []
    obligations: list[str] = []

    raw_open = payload.get("open_obligations")
    if isinstance(raw_open, list):
        for item in raw_open:
            if isinstance(item, str) and item.strip():
                obligations.append(item)
            elif isinstance(item, Mapping):
                label = item.get("title") or item.get("requirement") or item.get("id")
                if label:
                    obligations.append(str(label))

    selected = payload.get("selected_todo")
    if isinstance(selected, Mapping):
        todo_id = str(selected.get("todo_id") or "selected_todo")
        title = selected.get("title") or selected.get("text")
        if title:
            obligations.append(f"todo[{todo_id}]: {title}")
        for key in ("requirements", "acceptance_criteria", "stops"):
            val = selected.get(key)
            if isinstance(val, str) and val.strip():
                obligations.append(f"todo[{todo_id}].{key}: {val}")
            elif isinstance(val, list):
                for entry in val[:3]:
                    if entry:
                        obligations.append(f"todo[{todo_id}].{key}: {entry}")

    summary = payload.get("plan_summary")
    if isinstance(summary, Mapping):
        for key in ("requirements", "stops", "remaining_work", "next_action"):
            val = summary.get(key)
            if isinstance(val, str) and val.strip():
                obligations.append(f"plan.{key}: {val}")
            elif isinstance(val, list):
                for entry in val[:3]:
                    if entry:
                        obligations.append(f"plan.{key}: {entry}")

    work_contract = payload.get("work_contract")
    if isinstance(work_contract, str) and work_contract.strip():
        obligations.append(f"work_contract: {work_contract}")
    elif isinstance(work_contract, Mapping):
        summary_val = (
            work_contract.get("summary")
            or work_contract.get("instruction")
            or work_contract.get("requirement")
            or work_contract.get("non_negotiable")
        )
        if summary_val:
            obligations.append(f"work_contract: {summary_val}")

    interaction = payload.get("interaction_contract")
    cli_channel = interaction.get("cli_channel") if isinstance(interaction, Mapping) else None
    settlement_plan = cli_channel.get("settlement_plan") if isinstance(cli_channel, Mapping) else None
    if isinstance(settlement_plan, Mapping):
        identity = settlement_plan.get("identity")
        turn_id = identity.get("turn_instance_id") if isinstance(identity, Mapping) else None
        if turn_id:
            obligations.append(
                f"settle_turn[{turn_id}]: settle via original Turn receipt without rerunning admission"
            )

    return _dedupe_capped(obligations, limit=limit)


def _format_direct_read_context(
    *,
    read_result: Mapping[str, Any],
    context_epoch: str,
    reclaimed_turns: int,
    dropped_failures: int,
    failure_facts: Sequence[str],
    open_obligations: Sequence[str],
    retired_commands: Sequence[str],
    continuation_text: str,
) -> str:
    header = (
        "[LoopX Hook Direct-Read Receipt | operator=EpochReset | sha256_verified=true "
        f"| eof=true | lock_free=true | read_latency_ms={read_result['read_latency_ms']} "
        f"| pages={read_result['pages_read']}]"
    )
    meta_line = (
        f"context_epoch={context_epoch} source_sha256={read_result['source_sha256']} "
        f"selection_sha256={read_result['selection_sha256']} reclaimed_turns={reclaimed_turns} "
        f"dropped_failures={dropped_failures} failure_facts_count={len(failure_facts)} "
        f"open_obligations_count={len(open_obligations)}"
    )
    sections = [header, meta_line]
    if failure_facts:
        sections.append(
            "Preserved failure_facts:\n"
            + "\n".join(f"- {item}" for item in failure_facts)
        )
    else:
        sections.append("Preserved failure_facts: none")
    if open_obligations:
        sections.append(
            "Preserved open_obligations:\n"
            + "\n".join(f"- {item}" for item in open_obligations)
        )
    if retired_commands:
        sections.append(
            "Retired shell prefetch commands (replaced in-process): "
            + ", ".join(retired_commands)
        )
    sections.append("Verified captured pointer payload (eof=true):\n" + str(read_result["rendered_text"]))
    if continuation_text:
        sections.append(continuation_text)
    return "\n".join(sections)


def _format_keep_raw_fallback(
    *,
    reason: str,
    context_epoch: str,
    failure_facts: Sequence[str],
    open_obligations: Sequence[str],
    continuation_text: str,
    raw_guidance_text: str,
) -> str:
    facts_json = json.dumps(list(failure_facts), ensure_ascii=False)
    obligations_json = json.dumps(list(open_obligations), ensure_ascii=False)
    return (
        f"[LoopX Hook Direct-Read Fallback | operator=KeepRaw | reason={reason} | context_epoch={context_epoch}]\n"
        f"LoopX model-context baseline invalidated. Saved files remain available. "
        f"{continuation_text}{raw_guidance_text}\n"
        f"Preserved failure facts: {facts_json}\n"
        f"Preserved open obligations: {obligations_json}"
    )


def apply_fail_closed_compact_invalidation(
    state: dict[str, Any],
    *,
    session: str,
    cwd: str,
    new_epoch: str,
    hook_name: str | None = None,
) -> dict[str, Any]:
    """Fail-closed baseline invalidation keeping recovery lookup intact under startup contention."""
    same = state.get("session_id") == session and state.get("cwd") == cwd
    updated = dict(state) if same else {"session_id": session, "cwd": cwd}
    recovery = updated.get("recovery")
    file_epoch_current = (
        isinstance(recovery, dict)
        and recovery.get("continuity_basis") == "captured_file"
        and isinstance(recovery.get("target"), dict)
        and recovery["target"].get("context_epoch") == updated.get("context_epoch")
    )
    if not updated.get("context_epoch") or updated.get("candidate") or updated.get("pending") or file_epoch_current:
        previous_epoch = updated.get("context_epoch")
        updated["context_epoch"] = new_epoch
        if updated["context_epoch"] != previous_epoch:
            updated.pop("compact_hook_wake", None)
            updated.pop("epoch_prefetch", None)
            updated.pop("quiet_turn", None)
    if hook_name in ("PreCompact", "PostCompact"):
        updated.pop("compact_hook_wake", None)
    for key in (
        "candidate",
        "pending",
        "active_call",
        "consumed_pointers",
        "confirmed_read_sections",
        "section_reads",
        "selected_resume",
    ):
        updated.pop(key, None)
    updated["baseline_invalidated_fail_closed"] = True
    return updated


def _load_decision_context_payload(
    state: Mapping[str, Any],
    event: Mapping[str, Any],
    recovery: Mapping[str, Any],
    payload: Mapping[str, Any] | None,
    cfg: Mapping[str, Any],
) -> tuple[Mapping[str, Any] | None, str | None, Any]:
    """Merge optional decision_file/source_recovery captures and resolve goal/runtime_root."""
    decision_payload: Mapping[str, Any] | None = payload
    decision_file = cfg.get("decision_file") or event.get("decision_file")
    if isinstance(decision_file, (str, Path)) and decision_file:
        extra_decision = _read_bounded_regular_json(Path(decision_file))
        if isinstance(extra_decision, Mapping):
            decision_payload = (
                {**dict(payload), **dict(extra_decision)}
                if isinstance(payload, Mapping)
                else extra_decision
            )
    if isinstance(state.get("source_recovery"), Mapping):
        src_ref = state["source_recovery"]
        if (
            isinstance(src_ref.get("path"), str)
            and isinstance(src_ref.get("sha256"), str)
            and src_ref.get("sha256") != recovery.get("sha256")
        ):
            try:
                _, _, src_payload = read_context_capture_file(
                    src_ref["path"], src_ref["sha256"], private=True,
                )
                decision_payload = (
                    {**src_payload, **dict(decision_payload)}
                    if isinstance(decision_payload, Mapping)
                    else src_payload
                )
            except (OSError, ValueError, TypeError, RecursionError):
                pass
    prep = recovery.get("execution_preparation")
    authority_route = prep.get("authority_route") if isinstance(prep, Mapping) else None
    goal_id = str(
        (decision_payload or {}).get("goal_id")
        or recovery.get("goal_id")
        or state.get("goal_id")
        or event.get("goal_id")
        or ""
    ) or None
    runtime_root = (
        cfg.get("runtime_root")
        or event.get("runtime_root")
        or (decision_payload or {}).get("runtime_root")
        or (authority_route.get("runtime_root") if isinstance(authority_route, Mapping) else None)
        or os.environ.get("LOOPX_RUNTIME_ROOT")
    )
    return decision_payload, goal_id, runtime_root


def _collect_compact_facts_and_obligations(
    state: Mapping[str, Any],
    event: Mapping[str, Any],
    decision_payload: Mapping[str, Any] | None,
    cfg: Mapping[str, Any],
    *,
    goal_id: str | None,
    runtime_root: Any,
) -> tuple[list[str], list[str], int, list[str]]:
    """Combine explicit and extracted failure facts and open obligations cleanly."""
    explicit_failures = [
        str(x) for x in (event.get("failure_facts") or ()) if str(x).strip()
    ]
    explicit_obligations = [
        str(x) for x in (event.get("open_obligations") or ()) if str(x).strip()
    ]
    extracted_failures_info = (
        extract_failure_facts_from_sources(
            cwd=event.get("cwd") or state.get("cwd"),
            goal_id=goal_id,
            runtime_root=runtime_root,
            run_history_paths=cfg.get("run_history_paths") or event.get("run_history_paths"),
            explore_log_paths=cfg.get("explore_log_paths") or event.get("explore_log_paths"),
        )
        if cfg["extract_facts"]
        else {"failure_facts": [], "total_failures_observed": 0, "sources_inspected": []}
    )
    extracted_obligations = (
        extract_open_obligations_from_decision(decision_payload)
        if cfg["extract_facts"]
        else []
    )
    failure_facts = _dedupe_capped(
        [*explicit_failures, *extracted_failures_info["failure_facts"]],
        limit=MAX_FACTS_COUNT,
    )
    open_obligations = _dedupe_capped(
        [*explicit_obligations, *extracted_obligations],
        limit=MAX_FACTS_COUNT,
    )
    dropped_failures = int(
        event.get("dropped_failures")
        or extracted_failures_info["total_failures_observed"]
        or len(failure_facts)
    )
    return (
        failure_facts,
        open_obligations,
        dropped_failures,
        list(extracted_failures_info["sources_inspected"]),
    )


def _record_compact_wake_metrics(
    state: dict[str, Any],
    *,
    ok: bool,
    operator: str,
    read_result: Mapping[str, Any],
    context_epoch: str,
    source_sha256: str,
    selection_sha256: str,
    wake_digest: str,
    failure_facts: list[str],
    open_obligations: list[str],
    sources_inspected: list[str],
    retired: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Check compact wake dedup, update hook telemetry counters, and build telemetry meta."""
    previous_wake = state.get("compact_hook_wake")
    deduplicated = bool(
        ok
        and cfg["deduplicate"]
        and isinstance(previous_wake, Mapping)
        and previous_wake.get("emitted_hook") == "SessionStart"
        and previous_wake.get("operator", "EpochReset") == "EpochReset"
        and previous_wake.get("context_epoch") == context_epoch
        and previous_wake.get("source_sha256") == source_sha256
        and previous_wake.get("wake_digest") == wake_digest
    )
    meta = {
        "schema_version": COMPACT_DIRECT_READ_SCHEMA_VERSION,
        "operator": operator,
        "lock_free": True,
        "sha256_verified": bool(read_result.get("sha256_verified")),
        "eof": bool(read_result.get("eof")),
        "pages_read": int(read_result.get("pages_read") or 0),
        "rendered_bytes": int(read_result.get("rendered_bytes") or 0),
        "read_latency_ms": float(read_result.get("read_latency_ms") or 0.0),
        "context_epoch": context_epoch,
        "source_sha256": source_sha256,
        "selection_sha256": selection_sha256,
        "wake_digest": wake_digest,
        "deduplicated": deduplicated,
        "suppressed_duplicate_prompt": deduplicated,
        "failure_facts_count": len(failure_facts),
        "open_obligations_count": len(open_obligations),
        "sources_inspected": sources_inspected,
        "retired_shell_prefetch": bool(retired["retired"]),
        "retired_shell_commands": retired["retired_commands"],
        "fallback_reason": None if ok else read_result.get("reason"),
    }
    metrics = dict(state.get("compact_hook_metrics") or {})
    metrics["attempts"] = int(metrics.get("attempts") or 0) + 1
    if deduplicated:
        metrics["deduplicated_wakes"] = int(metrics.get("deduplicated_wakes") or 0) + 1
    elif ok:
        metrics["direct_reads"] = int(metrics.get("direct_reads") or 0) + 1
    else:
        metrics["keep_raw_fallbacks"] = int(metrics.get("keep_raw_fallbacks") or 0) + 1
    metrics["last_read_latency_ms"] = meta["read_latency_ms"]
    metrics["last_operator"] = operator
    state["compact_hook_metrics"] = metrics
    return meta, deduplicated


def resolve_compact_hook_direct_read(
    *,
    hook_name: Any,
    state: dict[str, Any],
    event: Mapping[str, Any],
    recovery: Mapping[str, Any] | None,
    recovery_needed: bool,
    continuation_text: str = "",
    raw_guidance_text: str = "",
) -> dict[str, Any] | None:
    """Resolve opt-in lock-free SessionStart(compact) hook direct-read, fact extraction, and wake dedup.

    Codex CLI (0.171.0+) only accepts `hookSpecificOutput.additionalContext`
    on `SessionStart`; `PostCompact` stays non-consuming (`{}`) so invalidation
    still runs without dropping or prematurely deduplicating the first wake.
    """
    cfg = resolve_compact_direct_read_config(state, event)
    if cfg is None or hook_name != "SessionStart":
        return None
    if event.get("source") != "compact" and not recovery_needed:
        return None
    if not isinstance(recovery, Mapping) or not recovery.get("path") or not recovery.get("sha256"):
        return None

    retired = (
        retire_shell_prefetch_command(event.get("prefetch_command"))
        if cfg["retire_shell_prefetch"]
        else {"present": False, "retired": False, "retired_commands": [], "extracted_pointers": [], "allowed_git_probe": None}
    )
    explicit_pointers = cfg["pointers"] if isinstance(cfg.get("pointers"), (list, tuple)) else None
    if explicit_pointers is None and retired["extracted_pointers"]:
        explicit_pointers = retired["extracted_pointers"]

    ok, read_result, payload = read_json_pointers_paginated(
        str(recovery["path"]),
        str(recovery["sha256"]),
        explicit_pointers,
        private=bool(cfg.get("private", True)),
        page_bytes=int(cfg["page_bytes"]),
        max_output_bytes=int(cfg["max_output_bytes"]),
        require_all_pointers=explicit_pointers is not None,
    )
    decision_payload, goal_id, runtime_root = _load_decision_context_payload(
        state, event, recovery, payload, cfg,
    )
    failure_facts, open_obligations, dropped_failures, sources_inspected = (
        _collect_compact_facts_and_obligations(
            state, event, decision_payload, cfg, goal_id=goal_id, runtime_root=runtime_root,
        )
    )
    context_epoch = str(state.get("context_epoch") or "epoch-0")
    source_sha256 = str(recovery.get("sha256") or "")
    operator = "EpochReset" if ok else "KeepRaw"
    selection_sha256 = str(read_result.get("selection_sha256") or "")
    wake_digest = _sha256_json({
        "context_epoch": context_epoch,
        "source_sha256": source_sha256,
        "selection_sha256": selection_sha256,
        "operator": operator,
        "failure_facts": failure_facts,
        "open_obligations": open_obligations,
        "fallback_reason": read_result.get("reason") if not ok else None,
    })
    meta, deduplicated = _record_compact_wake_metrics(
        state,
        ok=ok,
        operator=operator,
        read_result=read_result,
        context_epoch=context_epoch,
        source_sha256=source_sha256,
        selection_sha256=selection_sha256,
        wake_digest=wake_digest,
        failure_facts=failure_facts,
        open_obligations=open_obligations,
        sources_inspected=sources_inspected,
        retired=retired,
        cfg=cfg,
    )
    if deduplicated:
        return {"hookSpecificOutput": {"hookEventName": hook_name, "compactDirectRead": meta}}

    if ok:
        state["compact_hook_wake"] = {
            "context_epoch": context_epoch,
            "source_sha256": source_sha256,
            "wake_digest": wake_digest,
            "emitted_hook": hook_name,
            "operator": operator,
            "recorded_at": _now_iso(),
        }
    additional_context = (
        _format_direct_read_context(
            read_result=read_result,
            context_epoch=context_epoch,
            reclaimed_turns=int(event.get("reclaimed_turns") or 0),
            dropped_failures=dropped_failures,
            failure_facts=failure_facts,
            open_obligations=open_obligations,
            retired_commands=retired["retired_commands"],
            continuation_text=continuation_text,
        )
        if ok
        else _format_keep_raw_fallback(
            reason=str(read_result.get("reason") or "direct_read_failed"),
            context_epoch=context_epoch,
            failure_facts=failure_facts,
            open_obligations=open_obligations,
            continuation_text=continuation_text,
            raw_guidance_text=raw_guidance_text,
        )
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_name,
            "additionalContext": additional_context,
            "compactDirectRead": meta,
        }
    }
