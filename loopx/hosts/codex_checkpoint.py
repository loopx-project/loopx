"""Codex-specific checkpoint inventory and native history readback.

This transport adapter does not decide research validity or adopt authority.
The artifact owner seals its declared native-store members after the Host exits.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shutil
import shlex
import sqlite3
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from contextlib import contextmanager, nullcontext, redirect_stdout, redirect_stderr
from typing import Any

from loopx.control_plane.content_digest import BARE_SHA256_PATTERN
from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport, native_checkpoint_reader


def _retained_history(request, session_id: str) -> list[dict[str, Any]]:
    turns = []
    cursor = None
    seen = set()
    while True:
        page = request("thread/turns/list", {
            "threadId": session_id, "sortDirection": "asc", "itemsView": "full", "limit": 100,
            **({"cursor": cursor} if cursor else {}),
        })
        if not isinstance(page.get("data"), list):
            raise ValueError("native checkpoint history page is unavailable")
        turns.extend(page["data"])
        cursor = page.get("nextCursor")
        if not cursor:
            break
        if cursor in seen:
            raise ValueError("native checkpoint repeated history cursor")
        seen.add(cursor)
    if not turns or any(t.get("status") not in {"completed", "failed", "interrupted"} or not t.get("id") for t in turns):
        raise ValueError("native checkpoint requires complete retained turns")
    ids = [t["id"] for t in turns]
    if len(set(ids)) != len(ids):
        raise ValueError("native checkpoint history contains duplicate turns")
    return turns


def _complete_history(request, session_id: str, *, interrupted_guard: dict | None = None) -> list[dict[str, Any]]:
    turns = _retained_history(request, session_id)
    if interrupted_guard is None:
        if turns[-1].get("status") != "completed":
            raise ValueError("native checkpoint requires a completed final cursor")
    else:
        _verify_interrupted_guard(turns, interrupted_guard)
    return turns


def native_interrupted_turn(env: dict[str, str], session_id: str, *,
                            process_started_at: float, process_stopped_at: float,
                            timeout: float = 30) -> dict[str, Any]:
    """Read a stable interrupted tail; this is not checkpoint admission.

    The caller proves original process termination and current session ownership.
    No resume, fork, interruption, model call, history edit or command replay occurs.
    A completed research-checkpoint cursor remains a separate, stricter contract.
    """
    if (not session_id or any(type(v) not in (int, float) or not math.isfinite(v)
            for v in (process_started_at, process_stopped_at, timeout))
            or process_started_at > process_stopped_at or timeout <= 0):
        raise ValueError("Native interruption requires its original process window")
    deadline = time.monotonic() + timeout
    with native_checkpoint_reader(env, timeout=timeout) as transport:
        def request(method, params):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Native interruption readback window closed")
            transport.response_timeout_sec = remaining
            return transport.request(method, params)
        _native_thread_readback(request, env, session_id)
        turns = _retained_history(request, session_id)
        tail = turns[-1]
        start, end = tail.get("startedAt"), tail.get("completedAt")
        if (tail.get("status") != "interrupted" or tail.get("error") is not None
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in (start, end))
                # Native Turn timestamps have second resolution.
                or not math.floor(process_started_at) <= start <= end <= math.ceil(process_stopped_at)):
            raise ValueError("Native interruption differs from the original process window")
        if any(item.get("status") in {"inProgress", "running", "pending"}
               for item in tail.get("items", [])):
            raise ValueError("Native interruption still has pending effects")
        encoded = json.dumps(turns, sort_keys=True, separators=(",", ":")).encode()
        reread = _retained_history(request, session_id)
        if json.dumps(reread, sort_keys=True, separators=(",", ":")).encode() != encoded:
            raise ValueError("Native interruption advanced during readback")
        return {"session_id": session_id, "turn_id": tail["id"],
                "visible_history_sha256": hashlib.sha256(encoded).hexdigest(),
                "turn_count": len(turns)}


def _native_store_components(home: Path) -> list[dict[str, str]]:
    members = []
    for entry in sorted(home.iterdir()):
        if entry.name in {"sessions", "archived_sessions", "session_index.jsonl"} or re.fullmatch(
            r"(?:state|thread_history)_[0-9]+\.sqlite", entry.name
        ):
            members.append({"id": "native_" + entry.name.replace(".", "_"), "source_path": str(entry)})
    if not any(m["id"].startswith("native_state_") for m in members):
        raise ValueError("native checkpoint session index is unavailable")
    return members


def _native_observation_fingerprint(home: Path, *, deadline: float) -> dict[str, str | None]:
    """Bind an isolated observation to unchanged physical native-store bytes."""
    sources = [home / 'config.toml', *(Path(m['source_path']) for m in _native_store_components(home))]
    for source in tuple(sources):
        if source.suffix == '.sqlite':
            sources.extend(p for suffix in ('-wal', '-journal')
                           if (p := Path(str(source) + suffix)).exists())
    fingerprint = {}
    for source in sources:
        for entry in [source, *sorted(source.rglob('*'))] if source.is_dir() else [source]:
            if entry.is_symlink() or entry.resolve(strict=True) != entry:
                raise ValueError('Native failure observation requires physical source members')
            if time.monotonic() >= deadline:
                raise TimeoutError('Failed native recovery readback window closed')
            if entry.is_dir():
                fingerprint[str(entry.relative_to(home))] = None
            elif entry.is_file():
                with entry.open('rb') as stream:
                    fingerprint[str(entry.relative_to(home))] = hashlib.file_digest(stream, 'sha256').hexdigest()
            else:
                raise ValueError('Native failure observation requires regular source members')
    return fingerprint


@contextmanager
def _idle_native_reader(env: dict[str, str], *, timeout: float, deadline: float,
                        sealed_store_paths: tuple[str, ...] | None = None):
    """Native history reads may update their index; keep those writes off source."""
    home = Path(env['CODEX_HOME'])
    with tempfile.TemporaryDirectory(prefix='loopx-idle-native-read-') as directory:
        observer = Path(directory)
        sources = [home / 'config.toml', *(Path(m['source_path']) for m in _native_store_components(home))]
        if sealed_store_paths is not None and set(sources) != {Path(p) for p in sealed_store_paths}:
            raise ValueError('Native idle observation differs from its sealed native-store participants')
        for source in sources:
            selected = [source, *source.rglob('*')] if source.is_dir() else [source]
            if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in selected):
                raise ValueError('Native idle observation requires physical native-store members')
            target = observer / source.name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
                for suffix in ('-wal', '-journal') if source.suffix == '.sqlite' else ():
                    companion = Path(str(source) + suffix)
                    if companion.exists():
                        if companion.is_symlink() or not companion.is_file():
                            raise ValueError('Native idle SQLite companion must be physical')
                        shutil.copy2(companion, str(target) + suffix)
        with native_checkpoint_reader({**env, 'CODEX_HOME': str(observer)},
                                      timeout=timeout, deadline=deadline) as reader:
            yield reader


def native_idle_turn(env: dict[str, str], session_id: str, *, process_started_at: float,
                     process_stopped_at: float | None = None, timeout: float = 30,
                     sealed_store_paths: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Observe a stable idle session at a stopped Host's process boundary.

    With a verified completed process window, require its last native Turn
    inside that window. Otherwise require completed history before the
    uncertain launch. The caller owns source termination and phase admission.
    """
    if (not session_id or any(type(v) not in (int, float) or not math.isfinite(v)
            for v in (process_started_at, timeout)) or timeout <= 0
            or (process_stopped_at is not None and (type(process_stopped_at) not in (int, float)
                or not math.isfinite(process_stopped_at) or process_stopped_at < process_started_at))):
        raise ValueError("Native idle readback requires its original process boundary")
    deadline = time.monotonic() + timeout
    with _idle_native_reader(env, timeout=timeout, deadline=deadline,
                             sealed_store_paths=sealed_store_paths) as transport:
        def request(method, params):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Native idle readback window closed")
            transport.response_timeout_sec = remaining
            return transport.request(method, params)
        _native_thread_readback(request, env, session_id)
        turns = _complete_history(request, session_id)
        tail = turns[-1]
        for turn in turns:
            start, end = turn.get("startedAt"), turn.get("completedAt")
            if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
                    or start > end or any(item.get("status") in {"inProgress", "running", "pending"}
                                          for item in turn.get("items", []))):
                raise ValueError("Native history is not idle before the original Host")
            if turn is tail and process_stopped_at is not None:
                if not math.floor(process_started_at) <= start <= end <= process_stopped_at:
                    raise ValueError("Native completed Turn is outside the original Host process")
            elif end >= math.floor(process_started_at):
                raise ValueError("Native history is not idle before the original Host")
        if tail.get("error") is not None:
            raise ValueError("Native idle cursor retains an error")
        encoded = json.dumps(turns, sort_keys=True, separators=(",", ":")).encode()
        if json.dumps(_complete_history(request, session_id), sort_keys=True,
                      separators=(",", ":")).encode() != encoded:
            raise ValueError("Native idle history advanced during readback")
        return {"session_id": session_id, "turn_id": tail["id"], "status": "completed",
                "completed_at": tail["completedAt"], "turn_count": len(turns),
                "visible_history_sha256": hashlib.sha256(encoded).hexdigest(),
                **({"started_at": tail["startedAt"]} if process_stopped_at is not None else {})}


def native_failure_error_digest(error: Any, *, cli_projection: bool = False) -> str:
    """Hash the Codex error object without interpreting or retaining its prose.

    The CLI can omit app-server's optional null fields and catch-all `other`
    discriminator. Its comparison projection drops only those protocol
    defaults; the raw native object is separately sealed without that projection.
    """
    if not isinstance(error, dict) or not isinstance(error.get('message'), str) or not error['message'].strip():
        raise ValueError('Returned native failure requires its error object')
    value = dict(error)
    if cli_projection:
        for key in ('codexErrorInfo', 'additionalDetails', 'misalignment'):
            if value.get(key) is None or (key == 'codexErrorInfo' and value[key] == 'other'):
                value.pop(key, None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def native_failed_turn(env: dict[str, str], session_id: str, *, failure_kind: str,
                       process_started_at: float, process_stopped_at: float,
                       returned_error_sha256: str | None = None,
                       timeout: float = 30) -> dict[str, Any]:
    """Read a retained failed cursor without rewriting it as completed.

    The caller independently proves the exact Host returned and explicitly
    requests recovery after an operator repair. Non-quota failures also require
    the exact returned error digest and a precise provider rollout cursor.
    This observation starts no Turn and supplies no checkpoint.
    """
    from loopx.control_plane.turn_driver.codex_cli import _codex_error_info_category
    from loopx.control_plane.turn_driver.host_failure import require_host_failure_kind
    from .codex_rollout_file import read_failed_turn_cursor

    require_host_failure_kind(failure_kind)
    if ((returned_error_sha256 is None and failure_kind != "quota_exhausted") or not session_id
            or (returned_error_sha256 is not None and (not isinstance(returned_error_sha256, str)
                or BARE_SHA256_PATTERN.fullmatch(returned_error_sha256) is None))
            or any(type(x) not in (int, float) or not math.isfinite(x)
                   for x in (process_started_at, process_stopped_at, timeout))
            or process_started_at > process_stopped_at or timeout <= 0):
        raise ValueError("Failed native recovery requires its returned failure and process window")
    deadline = time.monotonic() + timeout
    home = Path(env['CODEX_HOME']) if returned_error_sha256 is not None else None
    original = _native_observation_fingerprint(home, deadline=deadline) if home is not None else {}
    observer = (_idle_native_reader(env, timeout=timeout, deadline=deadline) if home is not None
                else native_checkpoint_reader(env, timeout=timeout, deadline=deadline))
    with observer as transport:
        def request(method, params):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Failed native recovery readback window closed")
            transport.response_timeout_sec = remaining
            return transport.request(method, params)
        thread = _native_thread_readback(request, env, session_id)
        cursor = None
        if home is not None:
            source = thread.get('path')
            rollout = Path(source) if isinstance(source, str) else None
            if rollout is None or not rollout.is_relative_to(home):
                raise ValueError('Failed native rollout must be in the original store')
            relative = rollout.relative_to(home)
            if (not relative.parts or relative.parts[0] not in ('sessions', 'archived_sessions')
                    or original.get(str(relative)) is None):
                raise ValueError('Failed native rollout must be a retained source file')
            cursor = read_failed_turn_cursor(rollout, session_id=session_id, workspace=env['LOOPX_PROJECT'],
                process_started_at=process_started_at, process_stopped_at=process_stopped_at, deadline=deadline)
        turns = _retained_history(request, session_id)
        tail = turns[-1]
        error = tail.get("error")
        error_digest = native_failure_error_digest(error) if returned_error_sha256 is not None else None
        error_projection = (native_failure_error_digest(error, cli_projection=True)
                            if returned_error_sha256 is not None else None)
        category, structured = _codex_error_info_category(error.get('codexErrorInfo') if isinstance(error, dict) else None)
        start, end = tail.get("startedAt"), tail.get("completedAt")
        end_bound = process_stopped_at if cursor is not None else math.ceil(process_stopped_at)
        if (tail.get("status") != "failed"
                or (cursor is not None and tail.get('id') != cursor['turn_id'])
                or (returned_error_sha256 is not None and error_projection != returned_error_sha256)
                or (returned_error_sha256 is None and (not structured or category != failure_kind))
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in (start, end))
                or not math.floor(process_started_at) <= start <= end <= end_bound):
            raise ValueError("Native failure differs from the original returned Host")
        effect_turns = turns if returned_error_sha256 is not None else [tail]
        if any(item.get("status") in {"inProgress", "running", "pending"}
               for turn in effect_turns for item in turn.get("items", [])):
            raise ValueError("Failed native cursor still has pending effects")
        encoded = json.dumps(turns, sort_keys=True, separators=(",", ":")).encode()
        if json.dumps(_retained_history(request, session_id), sort_keys=True,
                      separators=(",", ":")).encode() != encoded:
            raise ValueError("Failed native history advanced during readback")
        result = {"session_id": session_id, "turn_id": tail["id"], "status": "failed",
                "failure_kind": failure_kind, "turn_count": len(turns),
                **({'error_sha256': error_digest, 'returned_error_sha256': error_projection,
                    'rollout_cursor': cursor} if cursor is not None else {}),
                "visible_history_sha256": hashlib.sha256(encoded).hexdigest()}
    if home is not None and _native_observation_fingerprint(home, deadline=deadline) != original:
        raise ValueError('Failed native source changed during isolated readback')
    return result


def _guard_cli_arguments(command: str, guard: dict) -> list[str]:
    """Parse the provider shell wrapper plus the renderer's optional Turn assignment."""
    outer = shlex.split(command)
    if len(outer) != 3 or Path(outer[0]).name not in {"bash", "zsh", "sh"} or outer[1] != "-lc":
        raise ValueError("native pending guard is not a supported single shell command")
    lines = outer[2].strip().splitlines()
    if len(lines) == 2 and shlex.split(lines[0]) == ["LOOPX_TURN=" + guard["turn_instance_id"]]:
        script = lines[1].replace('"${LOOPX_TURN:?}"', shlex.quote(guard["turn_instance_id"]))
    elif len(lines) == 1:
        script = lines[0]
    else:
        raise ValueError("native pending guard contains an unsupported shell program")
    # Conservative provider syntax profile, not Goal/state classification.
    # Only the exact renderer assignment above is expanded; never execute shell.
    if not re.fullmatch(r"[A-Za-z0-9_./:=+@ '\"-]+", script):
        raise ValueError("native pending guard contains unsupported shell syntax")
    argv = shlex.split(script)
    if not argv or argv[0] != guard["cli"]:
        raise ValueError("native pending guard executable differs")
    return argv[1:]


def _verify_interrupted_guard(turns: list[dict], guard: dict) -> None:
    """Provider-specific stopped-tool cursor, never a relaxed completed cursor.

    Only the single simple command emitted by the heartbeat renderer is eligible.
    Other than the exact renderer Turn assignment, shell programs and expansions
    are unsupported, even if they eventually call quota. CLI parsing owns option
    semantics; this adapter binds the native command item to the Host's held packet and rejects any later native item.
    """
    from loopx.cli import build_parser

    tail = turns[-1]
    items = tail.get("items") or []
    if tail.get("status") != "interrupted" or not items:
        raise ValueError("native checkpoint requires an interrupted pending guard")
    item = items[-1]
    if (item.get("type") != "commandExecution" or item.get("status") != "failed" or
            item.get("aggregatedOutput") is not None or not item.get("id") or
            item.get("cwd") != guard["workspace"]):
        raise ValueError("native checkpoint has output or work beyond its pending guard")
    parser = build_parser()
    def arguments(command):
        argv = _guard_cli_arguments(command, guard)
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                parsed = vars(parser.parse_args(argv))
            # A sealed command is independent of today's machine preference.
            # Legacy omitted transport flags mean off; explicit on stays distinct.
            if parsed.get("context_delivery") is None:
                parsed["context_delivery"] = "off"
            return parsed
        except (SystemExit, ValueError) as error:
            raise ValueError("native pending guard arguments are invalid") from error
    args = arguments(item.get("command", ""))
    # Older sealed guards could omit the then-mandatory envelope flag.
    # Missing complete mode is historical off, never inferred from the command.
    turn_envelope = guard.get("turn_envelope", True)
    complete_turn = guard.get("complete_turn", False)
    if not isinstance(turn_envelope, bool):
        raise ValueError("native pending guard envelope mode must be boolean")
    if not isinstance(complete_turn, bool):
        raise ValueError("native pending guard complete mode must be boolean")
    if turn_envelope and complete_turn:
        raise ValueError("native pending guard has incompatible envelope and complete modes")
    expected = {"command": "quota", "quota_command": "should-run", "format": "json",
                "registry": guard["registry"], "runtime_root": guard["runtime_root"],
                "goal_id": guard["goal_id"], "agent_id": guard["agent_id"],
                "turn_instance_id": guard["turn_instance_id"], "turn_envelope": turn_envelope,
                "complete_turn": complete_turn, "decision_output_root": guard["capture_root"],
                "decision_output_dir": None, "decision_handoff_socket": guard["socket_path"],
                "codex_context_dir": guard.get("codex_context_dir"),
                "codex_context_root": guard.get("codex_context_root"),
                "context_delivery": guard.get("context_delivery", "off")}
    if any(args.get(key) != value for key, value in expected.items()):
        raise ValueError("native pending guard differs from the held packet routes or identity")
    # A second matching guard after a tool yield cannot stand in for the first
    # held call. Compare parsed routes, not only command spelling or item count.
    for prior_turn in turns:
        for previous in prior_turn.get("items", []):
            if previous is item or previous.get("type") != "commandExecution":
                continue
            try:
                prior_args = arguments(previous.get("command", ""))
            except ValueError:
                continue
            if all(prior_args.get(key) == value for key, value in expected.items()):
                raise ValueError("native pending guard is ambiguous after an earlier matching call")
    if guard.get("native_call_id") is not None and item["id"] != guard["native_call_id"]:
        raise ValueError("native pending guard call identity differs")


def _native_thread_readback(request, env: dict[str, str], session_id: str) -> dict[str, Any]:
    readback = request("thread/read", {"threadId": session_id, "includeTurns": False})
    if readback.get("thread", {}).get("id") != session_id:
        raise ValueError("native checkpoint session identity mismatch")
    workspace = Path(env["LOOPX_PROJECT"])
    if (not workspace.is_absolute() or workspace.resolve(strict=True) != workspace or
            readback["thread"].get("cwd") != str(workspace)):
        raise ValueError("native checkpoint session workspace differs from the declared code basis")
    return readback['thread']


def native_completed_cursor(env: dict[str, str], session_id: str, *,
                            retained_turn_ids: list[str], timeout: float) -> str:
    """Read a provisional completed continuation, not a history certificate.

    Actual capture must independently verify the retained full prefix and this
    cursor. An explicit context reset has an empty prefix; its caller must still
    bind that reset's checkpoint/review/session provenance. An empty prefix does
    not accept an empty or unfinished current conversation. Ordinary preflight
    does not need every historical tool output.
    """
    if (not session_id or not math.isfinite(timeout) or timeout <= 0 or
            not isinstance(retained_turn_ids, list) or any(not isinstance(x, str) or not x for x in retained_turn_ids)):
        raise ValueError("native continuation cursor requires its retained source and an open window")
    deadline = time.monotonic() + timeout
    with native_checkpoint_reader(env, timeout=timeout) as transport:
        def request(method, params):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("native continuation cursor window closed")
            transport.response_timeout_sec = remaining
            return transport.request(method, params)
        _native_thread_readback(request, env, session_id)
        page = request("thread/turns/list", {"threadId": session_id,
            "sortDirection": "desc", "itemsView": "notLoaded", "limit": 1})
        turns = page.get("data")
        if not isinstance(turns, list) or len(turns) != 1:
            raise ValueError("native continuation cursor is unavailable")
        cursor = turns[0].get("id")
        if (not isinstance(cursor, str) or not cursor or cursor in retained_turn_ids or
                turns[0].get("status") != "completed"):
            raise ValueError("native continuation requires a new completed final cursor")
        return cursor


def native_session_checkpoint(env: dict[str, str], session_id: str, *, timeout: float,
                              interrupted_guard: dict | None = None,
                              retained_history: dict | None = None,
                              required_completed_turn_id: str | None = None,
                              transport: StdioNativeGoalTransport | None = None) -> dict[str, Any]:
    home = Path(env["CODEX_HOME"])
    if not session_id or timeout <= 0:
        raise ValueError("native checkpoint requires a session and an open capture window")
    if required_completed_turn_id is not None and (
            not isinstance(required_completed_turn_id, str) or not required_completed_turn_id or
            retained_history is None):
        raise ValueError("native capture cursor requires its retained history basis")
    deadline = time.monotonic() + timeout
    version = subprocess.check_output([env["CODEX_BIN"], "--version"], env=env, text=True, encoding="utf-8",
                                      timeout=timeout).strip()
    # Native storage is opaque. Retain both rollout and paginated/index stores;
    # auth, logs, memories and the symlinked skill installation are not members.
    reader = nullcontext(transport) if transport is not None else native_checkpoint_reader(env, timeout=timeout)
    with reader as transport:
        def request(method, params):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("native checkpoint window closed")
            transport.response_timeout_sec = remaining
            return transport.request(method, params)
        _native_thread_readback(request, env, session_id)
        turns = _complete_history(request, session_id, interrupted_guard=interrupted_guard)
        ids = [t["id"] for t in turns]
        if retained_history is not None:
            original = retained_history["turn_ids"]
            prefix = json.dumps(turns[:len(original)], sort_keys=True, separators=(",", ":")).encode()
            if (len(ids) <= len(original) or ids[:len(original)] != original or
                    hashlib.sha256(prefix).hexdigest() != retained_history["visible_history_sha256"]):
                raise ValueError("native continuation lost or changed retained checkpoint history")
            if required_completed_turn_id is not None and not any(
                    turn["id"] == required_completed_turn_id and turn["status"] == "completed"
                    for turn in turns[len(original):]):
                raise ValueError("native capture lost its completed continuation cursor")
    members = _native_store_components(home)
    encoded = json.dumps(turns, sort_keys=True, separators=(",", ":")).encode()
    return {"components": members, "metadata": {
        "session_id": session_id, "turn_ids": ids,
        **({"completed_turn_id": ids[-1]} if interrupted_guard is None else {
            "interrupted_turn_id": ids[-1], "interrupted_guard": {
                **interrupted_guard, "native_call_id": turns[-1]["items"][-1]["id"]}}),
        "visible_history_sha256": hashlib.sha256(encoded).hexdigest(), "host_version": version,
        "guest_home": str(home), "guest_workspace": env["LOOPX_PROJECT"],
        "path_profile": "preserve_guest_paths_in_isolated_environment",
        "config_sha256": hashlib.sha256((home / "config.toml").read_bytes()).hexdigest(),
        "store_members": [{"id": c["id"], "name": Path(c["source_path"]).name} for c in members],
        "model_calls": 0, "execution_authority_granted": False,
    }}


def _native_thread_ids(home: Path) -> set[str]:
    """Read opaque identities omitted by the native interactive thread list.

    At the qualified Host version, thread/list hides a fresh fork until its
    first user event. The native index locates candidates only: thread/read
    and complete native history still prove provenance and the captured cursor.
    Never edit this provider-owned store or infer authority from an index row.
    """
    indexes = [path for path in home.iterdir() if re.fullmatch(r"state_[0-9]+\.sqlite", path.name)]
    if len(indexes) != 1 or indexes[0].resolve(strict=True) != indexes[0] or not indexes[0].is_file():
        raise ValueError("native fork recovery requires one qualified physical session index")
    with sqlite3.connect(indexes[0].as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT id FROM threads").fetchall()
    ids = [row[0] for row in rows]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("native fork recovery index has invalid or duplicate identities")
    return set(ids)


def checkpoint_destination_config(config_path: Path, source_sha256: str,
                                  destination_config: Path | None = None, *,
                                  provider_base_url: str | None = None) -> tuple[bytes, dict[str, str] | None]:
    """Verify sealed settings and a new receiver's explicit configuration.

    Model/effort may change as declared. A Host may separately bind its current
    credential-gateway route; that changes only the existing provider's base URL,
    never its protocol, credential contract, tools or permissions. The complete
    source and destination config digests remain in the transition provenance.
    This does not authorize changing an active session or selecting a provider.
    """
    source = config_path.read_bytes()
    if hashlib.sha256(source).hexdigest() != source_sha256:
        raise ValueError("native checkpoint effective configuration differs")
    if destination_config is None:
        if provider_base_url is not None:
            raise ValueError("provider transport rebinding requires explicit destination settings")
        return source, None
    if (not destination_config.is_absolute() or
            destination_config.resolve(strict=True) != destination_config):
        raise ValueError("checkpoint model configuration requires a physical absolute path")
    target = destination_config.read_bytes()
    original, changed = tomllib.loads(source.decode("utf-8")), tomllib.loads(target.decode("utf-8"))
    keys = ("model", "model_reasoning_effort")
    if any(not isinstance(config.get(key), str) or not config[key].strip()
           for config in (original, changed) for key in keys):
        raise ValueError("checkpoint model transition requires explicit model and reasoning effort")
    original_other = {k: v for k, v in original.items() if k not in keys}
    changed_other = {k: v for k, v in changed.items() if k not in keys}
    if provider_base_url is not None:
        provider = original.get("model_provider")
        before_profiles, after_profiles = original.get("model_providers"), changed.get("model_providers")
        if (not isinstance(provider, str) or not isinstance(before_profiles, dict)
                or not isinstance(after_profiles, dict)):
            raise ValueError("checkpoint provider transport requires an existing custom provider")
        before = before_profiles.get(provider, {})
        after = after_profiles.get(provider, {})
        if (not isinstance(provider_base_url, str) or not provider_base_url.strip()
                or not isinstance(provider, str) or provider == "openai"
                or changed.get("model_provider") != provider
                or not isinstance(before, dict) or not isinstance(after, dict)
                or not isinstance(before.get("base_url"), str) or not before["base_url"]
                or after.get("base_url") != provider_base_url):
            raise ValueError("checkpoint provider transport differs from the current Host route")
        # Compare typed TOML values, not text substitutions or hostname guesses.
        # All other provider entries and every non-route setting stay fixed.
        for config, profile in ((original_other, before), (changed_other, after)):
            config["model_providers"] = {**config["model_providers"], provider: {
                key: value for key, value in profile.items() if key != "base_url"}}
    if original_other != changed_other:
        raise ValueError("checkpoint configuration transition may change only model and reasoning effort "
                         "or an explicitly bound provider transport route")
    return target, {
        "source_config_sha256": source_sha256,
        "destination_config_sha256": hashlib.sha256(target).hexdigest(),
        "source_model": original["model"], "source_effort": original["model_reasoning_effort"],
        "destination_model": changed["model"], "destination_effort": changed["model_reasoning_effort"],
    }


def fork_native_checkpoint(env: dict[str, str], unit: Path, expected_sha256: str,
                           config_path: Path, *, timeout: float,
                           destination_config: Path | None = None,
                           provider_base_url: str | None = None) -> dict[str, Any]:
    # The existing process lock serializes reconstruction in this isolated home.
    # The private attempt below describes one opaque native RPC, not authority
    # to admit a Goal, a work Turn, or a second fork after an uncertain response.
    from loopx.file_lock import exclusive_file_lock

    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("native checkpoint reconstruction timeout must be finite and positive")
    if provider_base_url is not None and provider_base_url != env.get("OPENAI_BASE_URL"):
        raise ValueError("checkpoint provider transport differs from the current launch")
    deadline = time.monotonic() + timeout
    home = Path(env["CODEX_HOME"])
    if not home.is_absolute() or home != home.resolve():
        raise ValueError("native checkpoint destination must be a physical absolute home")
    with exclusive_file_lock(home, timeout_seconds=timeout, operation="checkpoint_native_fork"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native checkpoint reconstruction window closed")
        return _fork_native_checkpoint(env, unit, expected_sha256, config_path, timeout=remaining,
            **({"destination_config": destination_config} if destination_config is not None else {}),
            **({"provider_base_url": provider_base_url} if provider_base_url is not None else {}))


def _fork_native_checkpoint(env: dict[str, str], unit: Path, expected_sha256: str,
                            config_path: Path, *, timeout: float,
                            destination_config: Path | None = None,
                            provider_base_url: str | None = None) -> dict[str, Any]:
    """Reconstruct one native branch in a new, fixed-path isolated environment.

    No model starts and no LoopX binding is registered. Existing session/Goal
    owners must separately admit this new session before useful continuation.
    """
    from loopx.control_plane.effect_runtime import effect_runtime_result

    deadline = time.monotonic() + timeout
    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(unit), "expected_sha256": expected_sha256,
    }, timeout=timeout, large_local_snapshot=True)
    manifest = json.loads((unit / "checkpoint.json").read_text(encoding="utf-8"))
    native = manifest["point_metadata"]["native_session"]
    if native.get("interrupted_guard") is not None:
        from loopx.hosts.research_checkpoint import select_checkpoint_replan

        component_id = manifest["point_metadata"].get("held_decision_component_id")
        if component_id not in {entry["id"] for entry in manifest["components"]}:
            raise ValueError("interrupted checkpoint lacks its held decision participant")
        raw = (unit / "components" / component_id).read_bytes()
        if hashlib.sha256(raw).hexdigest() != native["interrupted_guard"]["decision_sha256"]:
            raise ValueError("interrupted checkpoint held decision digest differs")
        guard = native["interrupted_guard"]
        intent = select_checkpoint_replan(json.loads(raw), goal_id=guard["goal_id"],
            agent_id=guard["agent_id"], turn_instance_id=guard["turn_instance_id"],
            timeout=max(.1, deadline - time.monotonic()), allow_legacy_source=True)
        if not intent["capture"] or intent != manifest["point_metadata"].get("replan"):
            raise ValueError("interrupted checkpoint original replan binding differs")
    home = Path(env["CODEX_HOME"])
    if str(home) != native["guest_home"] or env["LOOPX_PROJECT"] != native["guest_workspace"]:
        raise ValueError("native checkpoint requires its qualified fixed guest paths")
    attempt_path = home / ".loopx-checkpoint-fork.json"
    recovering = home.exists()
    if recovering and (not attempt_path.is_file() or attempt_path.resolve() != attempt_path):
        raise ValueError("native checkpoint destination home must be new or retain its original fork attempt")
    config, transition = checkpoint_destination_config(
        config_path, native["config_sha256"], destination_config,
        **({"provider_base_url": provider_base_url} if provider_base_url is not None else {}))
    version = subprocess.check_output([env["CODEX_BIN"], "--version"], env=env, text=True, encoding="utf-8",
                                      timeout=max(.1, deadline - time.monotonic())).strip()
    if version != native["host_version"]:
        raise ValueError("native checkpoint Host version differs")
    names, component_ids = set(), set()
    declared_ids = {entry["id"] for entry in manifest["components"]}
    for member in native["store_members"]:
        if not isinstance(member, dict) or set(member) != {"id", "name"}:
            raise ValueError("native checkpoint member inventory is invalid")
        name, component_id = member["name"], member["id"]
        # The sealed manifest binds allocated IDs to bytes; provider names
        # still select only the existing allowlisted native-store destinations.
        # Historical fixed IDs remain valid without reserving caller names.
        if (not isinstance(name, str) or not isinstance(component_id, str) or
                name in names or component_id in component_ids or component_id not in declared_ids or
                not (name in {"sessions", "archived_sessions", "session_index.jsonl"} or
                     re.fullmatch(r"(?:state|thread_history)_[0-9]+\.sqlite", name))):
            raise ValueError("native checkpoint member inventory is invalid")
        names.add(name)
        component_ids.add(component_id)
    interrupted_guard = native.get("interrupted_guard")
    if interrupted_guard is not None:
        if ("completed_turn_id" in native or not native.get("interrupted_turn_id") or
                not native.get("turn_ids") or native["turn_ids"][-1] != native["interrupted_turn_id"]):
            raise ValueError("native checkpoint mixes completed and interrupted cursors")
        cursor_basis = {"interrupted_turn_id": native["interrupted_turn_id"],
                        "interrupted_guard": interrupted_guard}
    else:
        cursor_basis = {"completed_turn_id": native["completed_turn_id"]}
    basis = {"checkpoint_sha256": expected_sha256, "source_session_id": native["session_id"],
        **cursor_basis, "config_sha256": native["config_sha256"],
        "destination_goal_id": env.get("LOOPX_GOAL_ID"), "destination_agent_id": env.get("LOOPX_AGENT_ID"),
        "guest_workspace": env["LOOPX_PROJECT"], "guest_home": str(home)}
    if transition is not None:
        basis["model_transition"] = transition
    attempt = None
    if recovering:
        attempt = json.loads(attempt_path.read_bytes())
        if (not isinstance(attempt, dict) or set(attempt) != {"basis", "source_thread_ids"} or
                attempt["basis"] != basis or not isinstance(attempt["source_thread_ids"], list) or
                any(not isinstance(v, str) or not v for v in attempt["source_thread_ids"]) or
                len(set(attempt["source_thread_ids"])) != len(attempt["source_thread_ids"]) or
                native["session_id"] not in attempt["source_thread_ids"]):
            raise ValueError("native fork attempt differs from the requested checkpoint or destination")
        if (home / "config.toml").read_bytes() != config:
            raise ValueError("native fork recovery effective configuration differs")
    else:
        home.mkdir(parents=True, mode=0o700)
        for member in native["store_members"]:
            source, destination = unit / "components" / member["id"], home / member["name"]
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        (home / "config.toml").write_bytes(config)
    with StdioNativeGoalTransport.spawn(
        [env["CODEX_BIN"], "-c", "features.goals=false", "app-server", "--listen", "stdio://"],
        cwd=env["LOOPX_PROJECT"], env=env, stderr=subprocess.DEVNULL,
        response_timeout_sec=max(.1, deadline - time.monotonic()),
    ) as transport:
        def request(method, params):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("native checkpoint reconstruction window closed")
            transport.response_timeout_sec = remaining
            return transport.request(method, params)
        request("initialize", {"clientInfo": {"name": "loopx_checkpoint", "version": "0.1.0"},
                               "capabilities": {"experimentalApi": True}})
        transport.notify("initialized", {})
        inventory = _native_thread_ids(home)
        if recovering:
            added = set(inventory) - set(attempt["source_thread_ids"])
            if len(added) != 1 or not set(attempt["source_thread_ids"]).issubset(inventory):
                raise ValueError("native fork outcome remains absent or ambiguous; a second fork is prohibited")
            session = added.pop()
            fork = request("thread/read", {"threadId": session, "includeTurns": False})
        else:
            if native["session_id"] not in inventory:
                raise ValueError("native checkpoint source is missing from the reconstructed store")
            attempt = {"basis": basis, "source_thread_ids": sorted(inventory)}
            # Persist before dispatch. Partial preparation is not a license to
            # fork again; a missing outcome stays explicitly unresolved.
            with os.fdopen(os.open(attempt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w",
                           encoding="utf-8") as file:
                json.dump(attempt, file, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            directory = os.open(home, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            fork = request("thread/fork", {"threadId": native["session_id"],
                **({"lastTurnId": native["completed_turn_id"]} if interrupted_guard is None else {}),
                "cwd": env["LOOPX_PROJECT"],
                "sandbox": "read-only", "approvalPolicy": "never", "deferGoalContinuation": True,
                "excludeTurns": True})
            session = fork.get("thread", {}).get("id")
        if not session or session == native["session_id"]:
            raise ValueError("native checkpoint did not create a distinct session")
        if (fork.get("thread", {}).get("forkedFromId") != native["session_id"] or
                fork["thread"].get("cwd") != env["LOOPX_PROJECT"]):
            raise ValueError("native fork provenance differs from the original attempt")
        turns = _complete_history(request, session, interrupted_guard=interrupted_guard)
        encoded = json.dumps(turns, sort_keys=True, separators=(",", ":")).encode()
        if ([t["id"] for t in turns] != native["turn_ids"] or
                hashlib.sha256(encoded).hexdigest() != native["visible_history_sha256"]):
            raise ValueError("native checkpoint reconstructed history differs")
    return {"session_id": session, **cursor_basis,
            "visible_history_sha256": native["visible_history_sha256"], "history_verified": True,
            **({"model_transition": transition} if transition is not None else {}),
            "model_calls": 0, "execution_authority_granted": False, "continuation_qualified": False}


def admit_native_checkpoint_session(env: dict[str, str], unit: Path, expected_sha256: str,
                                    config_path: Path, *, sandbox: str, timeout: float,
                                    destination_config: Path | None = None,
                                    provider_base_url: str | None = None,
                                    restore_loopx_state: bool = False) -> dict[str, Any]:
    """Explicit controller admission of a verified fork into a destination Goal.

    Existing native fork, Goal lifetime and session owners retain their rules.
    Historical execution effects and domain-provider budget grants are not adopted
    by this participant. Admission alone never launches work or a model.
    An interrupted cursor is retained as history, not as an executable source
    guard. The destination must obtain its own current work/replan admission.
    """
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from loopx.agent_registry import load_goal_from_registry, require_registered_agent_id
    from loopx.control_plane.projects.registry_codec import (
        project_registry_transaction,
    )
    from loopx.control_plane.turn_driver.codex_sessions import (
        codex_session_profile_digest, select_codex_cli_session,
    )
    from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession

    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("checkpoint session admission timeout must be finite and positive")
    if type(restore_loopx_state) is not bool:
        raise ValueError("full LoopX state restoration requires an explicit boolean")
    if provider_base_url is not None and provider_base_url != env.get("OPENAI_BASE_URL"):
        raise ValueError("checkpoint provider transport differs from the current launch")
    deadline = time.monotonic() + timeout
    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(unit), "expected_sha256": expected_sha256,
    }, timeout=timeout, large_local_snapshot=True)
    metadata = json.loads((unit / "checkpoint.json").read_bytes())["point_metadata"]
    source_profile = (metadata.get("session") or {}).get("session_profile_digest")
    from loopx.control_plane.content_digest import BARE_SHA256_PATTERN

    if not isinstance(source_profile, str) or not BARE_SHA256_PATTERN.fullmatch(source_profile):
        raise ValueError("checkpoint session admission requires its captured source session profile")
    if tomllib.loads(config_path.read_text(encoding="utf-8")).get("features", {}).get("goals") is not False:
        raise ValueError("checkpoint session admission requires native automatic Goals explicitly disabled")
    config, transition = checkpoint_destination_config(
        config_path, metadata["native_session"]["config_sha256"], destination_config,
        **({"provider_base_url": provider_base_url} if provider_base_url is not None else {}))
    target_profile = source_profile
    if transition is not None:
        if (env["MODEL_NAME"] != transition["destination_model"] or
                env["REASONING_EFFORT"] != transition["destination_effort"]):
            raise ValueError("checkpoint destination model differs from the current launch")
        binary = shutil.which(env["CODEX_BIN"])
        if binary is None:
            raise ValueError("Codex CLI executable is unavailable")
        observed_source = codex_session_profile_digest(
            project=Path(env["LOOPX_PROJECT"]), codex_bin=binary,
            home=Path(env["CODEX_HOME"]), sandbox=sandbox,
            model=transition["source_model"], reasoning_effort=transition["source_effort"],
            config_bytes=config_path.read_bytes())
        if observed_source != source_profile:
            raise ValueError("checkpoint source session profile changed")
        target_profile = codex_session_profile_digest(
            project=Path(env["LOOPX_PROJECT"]), codex_bin=binary,
            home=Path(env["CODEX_HOME"]), sandbox=sandbox,
            model=env["MODEL_NAME"], reasoning_effort=env["REASONING_EFFORT"], config_bytes=config)
        transition = {**transition, "source_profile_digest": source_profile,
                      "destination_profile_digest": target_profile}
    source = metadata.get("loopx_state", {})
    lineage = {"goal_id": env["LOOPX_GOAL_ID"], "agent_id": env["LOOPX_AGENT_ID"]}
    if (not source.get("goal_id") or not source.get("agent_id") or
            (not all(source[key] == lineage[key] for key in lineage) if restore_loopx_state else
             any(source[key] == lineage[key] for key in lineage))):
        raise ValueError("full LoopX restoration requires the captured Goal and Agent identities" if restore_loopx_state else
                         "checkpoint session admission requires distinct destination Goal and Agent identities")
    registry = Path(env["LOOPX_REGISTRY"])
    # Use the current governed Goal path. The lifecycle-only source-session
    # profile cannot run governed Turns and is rejected by the registry owner.
    goal = load_goal_from_registry(registry, lineage["goal_id"])
    require_registered_agent_id(registry_path=registry, goal_id=lineage["goal_id"], agent_id=lineage["agent_id"])
    existing = select_codex_cli_session(Path(env["LOOPX_RUNTIME_ROOT"]), lineage=lineage, session_scope="agent")
    expected_binding = None
    if restore_loopx_state and (existing is None or existing.get("checkpoint_artifact_sha256") != expected_sha256):
        from loopx.hosts.research_checkpoint import verify_restored_loopx_state
        from loopx.control_plane.turn_driver.codex_sessions import _session_path

        runtime_source = verify_restored_loopx_state(env, unit, metadata,
            deadline=time.time() + max(.1, deadline - time.monotonic()))
        path = _session_path(Path(env["LOOPX_RUNTIME_ROOT"]), lineage, "agent")
        sealed = runtime_source / path.relative_to(Path(env["LOOPX_RUNTIME_ROOT"]))
        if sealed.resolve(strict=True) != sealed:
            raise ValueError("captured LoopX session binding is redirected")
        expected_binding = json.loads(sealed.read_bytes())
        if (existing != expected_binding or existing.get("session_id") != metadata["native_session"]["session_id"]
                or existing.get("session_profile_digest") != source_profile):
            raise ValueError("restored LoopX session differs from its sealed source binding")
    if existing is not None and expected_binding is None:
        # The original session binding is also the admission receipt. Only a
        # source-bound committed admission can recover a lost controller reply;
        # an unrelated binding or a partial native fork is never replaced.
        # Session construction captures launch authority through the registry
        # owner too. Construct before taking the non-reentrant binding fence;
        # recheck current Goal and Agent authority inside it before readback.
        wake = CodexAgentSession(env, context="resume", sandbox=sandbox, receipt={})
        with project_registry_transaction(registry, operation="checkpoint_session_binding", agent_id=lineage["agent_id"]):
            current = load_goal_from_registry(registry, lineage["goal_id"])
            require_registered_agent_id(registry_path=registry, goal_id=lineage["goal_id"], agent_id=lineage["agent_id"])
            if current != goal:
                raise ValueError("destination Goal configuration changed during native checkpoint readback")
            binding = select_codex_cli_session(wake.root, lineage=lineage, session_scope="agent",
                                              goal_admission=wake.admission)
            if not binding or binding.get("checkpoint_artifact_sha256") != expected_sha256:
                raise ValueError("destination session binding already exists without this checkpoint admission")
            if binding.get("checkpoint_artifact_root") not in (None, str(unit)):
                raise ValueError("destination checkpoint source root differs from the original admission")
            if wake.digest != target_profile:
                raise ValueError("checkpoint source session profile changed")
            if transition is not None and binding.get("checkpoint_model_transition") != transition:
                raise ValueError("checkpoint model transition differs from its original admission")
            native = metadata["native_session"]
            observed = native_session_checkpoint(env, binding["session_id"],
                timeout=max(.1, deadline - time.monotonic()),
                interrupted_guard=native.get("interrupted_guard"))["metadata"]
            cursor_key = "interrupted_turn_id" if native.get("interrupted_guard") is not None else "completed_turn_id"
            keys = (cursor_key, "turn_ids", "visible_history_sha256", "host_version",
                    "guest_home", "guest_workspace", "config_sha256")
            expected = {**native, "config_sha256": hashlib.sha256(config).hexdigest()}
            if binding["session_id"] == native["session_id"] or any(observed[key] != expected[key] for key in keys):
                raise ValueError("checkpoint admitted native session advanced or differs; use its existing continuation")
            return {"session_id": binding["session_id"], cursor_key: observed[cursor_key],
                "visible_history_sha256": observed["visible_history_sha256"], "history_verified": True,
                "model_calls": 0, "execution_authority_granted": False, "continuation_qualified": False,
                "destination_goal_ref": wake.goal_ref, "destination_agent_id": lineage["agent_id"],
                "session_binding_admitted": True, "session_profile_digest": wake.digest,
                **({"model_transition": transition} if transition is not None else {})}
    result = fork_native_checkpoint(env, unit, expected_sha256, config_path,
        timeout=max(.1, deadline - time.monotonic()),
        **({"destination_config": destination_config} if destination_config is not None else {}),
        **({"provider_base_url": provider_base_url} if provider_base_url is not None else {}))
    receipt: dict[str, Any] = {}
    wake = CodexAgentSession(env, context="fresh" if expected_binding is not None else "resume",
                             sandbox=sandbox, receipt=receipt)
    if expected_binding is not None and expected_binding.get("goal_ref") != wake.goal_ref:
        raise ValueError("restored Goal lifetime differs from its captured session")
    # Hold the existing registry owner's fence through the session write. This
    # does not mint a lease, task or work admission from historical state.
    with project_registry_transaction(registry, operation="checkpoint_session_binding", agent_id=lineage["agent_id"]):
        current = load_goal_from_registry(registry, lineage["goal_id"])
        require_registered_agent_id(registry_path=registry, goal_id=lineage["goal_id"], agent_id=lineage["agent_id"])
        if current != goal:
            raise ValueError("destination Goal configuration changed during native checkpoint reconstruction")
        # The source profile was independently checked against the sealed
        # settings above. Bind the explicitly verified destination profile;
        # ordinary session resumes still require an exact profile match.
        wake._bind_verified_fork(result["session_id"], target_profile, expected_sha256, str(unit),
            **({"expected_binding": expected_binding} if expected_binding is not None else {}),
            **({"model_transition": transition} if transition is not None else {}))
    return {**result, **({"model_transition": transition} if transition is not None else {}),
            "destination_goal_ref": wake.goal_ref, "destination_agent_id": lineage["agent_id"],
            "session_binding_admitted": True, "session_profile_digest": wake.digest}


def main(argv=None) -> None:
    """Controller entrypoint inside an explicitly prepared isolated guest."""
    import os
    from loopx.control_plane.turn_driver.codex_cli import CODEX_CLI_SANDBOXES

    parser = argparse.ArgumentParser(description="Admit a checkpoint's native session participant; no model starts.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--destination-model-config", type=Path,
                        help="Explicit new-receiver config changing only model and reasoning effort")
    parser.add_argument("--sandbox", choices=CODEX_CLI_SANDBOXES, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    required = ("CODEX_BIN", "CODEX_HOME", "LOOPX_PROJECT", "LOOPX_REGISTRY", "LOOPX_RUNTIME_ROOT",
                "LOOPX_GOAL_ID", "LOOPX_AGENT_ID", "MODEL_NAME", "REASONING_EFFORT")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        parser.error("Explicit destination controller environment is missing: " + ", ".join(missing))
    result = admit_native_checkpoint_session(dict(os.environ), args.root, args.expected_sha256,
        args.config_path, sandbox=args.sandbox, timeout=args.timeout_seconds,
        **({"destination_config": args.destination_model_config}
           if args.destination_model_config is not None else {}))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
