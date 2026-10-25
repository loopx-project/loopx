"""Native delivery assumptions, invalidation, recovery and scope isolation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.control_plane.turn_driver import codex_context as context


def test_shared_root_keeps_interleaved_native_sessions_independent(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.chdir(tmp_path)
    original = {}
    for session in ("one", "../../two"):
        directory = Path(context.codex_context_directory(root=str(tmp_path), session=session, create=True))
        assert directory.parent == tmp_path and directory.stat().st_mode & 0o077 == 0
        monkeypatch.setenv("CODEX_THREAD_ID", session)
        event = dict(session_id=session, cwd=str(tmp_path), hook_event_name="SessionStart", source="startup")
        context.codex_context_hook(str(directory), event)
        _, wire = project(directory)
        original[session] = (directory, wire["recovery"], (directory / "session.json").read_bytes())
    assert original["one"][0] != original["../../two"][0]
    for session, (directory, recovery, raw) in original.items():
        monkeypatch.setenv("CODEX_THREAD_ID", session)
        selected = context.codex_context_directory(root=str(tmp_path))
        assert selected == str(directory)
        assert context.read_codex_context_reference(selected, goal_id="fixture", agent_id="agent") == (
            recovery["path"], recovery["sha256"])
        assert (directory / "session.json").read_bytes() == raw


def test_shared_root_read_does_not_create_unknown_session_or_follow_alias(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "unknown")
    with pytest.raises(FileNotFoundError):
        context.codex_context_directory(root=str(tmp_path))
    assert list(tmp_path.iterdir()) == []
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    with pytest.raises(ValueError, match="native session"):
        context.codex_context_directory(root=str(tmp_path), create=True)
    import hashlib

    alias = tmp_path / hashlib.sha256(b"session").hexdigest()
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="private"):
        context.codex_context_directory(root=str(tmp_path), session="session", create=True)
    assert not (target / "session.json").exists()


def test_native_configuration_cli_is_read_only_and_usable_without_registry(tmp_path):
    tmp_path.chmod(0o700)
    repo = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "loopx.entrypoint", "--format", "json", "quota", "context-config",
        "--context-delivery", "on", "--codex-app", "--codex-context-root", str(tmp_path), "--goal-id", "fixture", "--agent-id", "agent"]
    result = subprocess.run(command, cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(repo)},
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    native = payload["native_config"]
    assert set(native["hooks"]) == set(context.CONTEXT_LIFECYCLE_HOOKS)
    assert "model_provider" not in native and "model_providers" not in native
    assert "ROLLOUT_TRACE" not in json.dumps(payload)
    assert "--codex-context-root" in native["developer_instructions"]
    assert "context-read" in native["developer_instructions"]
    assert "hooks.state" not in native and list(tmp_path.iterdir()) == []
    missing_identity = subprocess.run(command[:-2], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(repo)}, capture_output=True, text=True, timeout=15)
    assert missing_identity.returncode == 1 and not json.loads(missing_identity.stdout)["ok"]


def setup(root, monkeypatch):
    root.chmod(0o700)
    monkeypatch.chdir(root)
    monkeypatch.setenv("CODEX_THREAD_ID", "session")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    event = dict(session_id="session", cwd=str(root), hook_event_name="SessionStart", source="startup")
    assert context.codex_context_hook(str(root), event) == {}
    return event


def project(root, capture_id="one", *, payload=None, generation=None):
    directory = root / capture_id
    directory.mkdir(mode=0o700)
    if payload is None:
        payload = {"ok": True, "goal_id": "fixture", "requirements": "Preserve final stop condition."}
    result = context.project_codex_context(payload, directory=str(root), capture_directory=directory,
        goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id=capture_id,
        continuity_generation=generation)
    return payload, result


@pytest.mark.parametrize("event_name,source", [("SessionStart", "compact"),
    ("SessionStart", "startup"), ("PreCompact", None), ("PostCompact", None)])
def test_lifecycle_invalidates_and_recovers_current_capture_without_bodies(tmp_path, monkeypatch, event_name, source):
    event = setup(tmp_path, monkeypatch)
    payload, first = project(tmp_path)
    assert first["mode"] == "full" and first["context"] == payload
    before = context._state(tmp_path)
    result = context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": event_name, "source": source})
    after = context._state(tmp_path)
    assert before["context_epoch"] != after["context_epoch"] and "candidate" not in after
    assert after["recovery"] == before["recovery"]
    if event_name == "SessionStart":
        body = result["hookSpecificOutput"]["additionalContext"]
        assert first["recovery"]["sha256"] in body and "quota context-read" in body
        assert payload["requirements"] not in body and len(body) < 2000
    else:
        assert result == {}
    _, second = project(tmp_path, "two")
    assert second["mode"] == "full" and second["target"]["context_epoch"] == after["context_epoch"]


def read_pages(root, wire, *, size=3072):
    from loopx.cli_commands.quota_capture import native_context_capture_view

    view = native_context_capture_view(wire, Path(wire["recovery"]["path"]).parent,
                                      context_directory=str(root))
    return capture_pages(view["path"], view["sha256"], size=size)


def capture_pages(path, digest, *, size=3072):
    import argparse
    from loopx.cli_commands.quota_capture import read_context_capture

    pages = []
    offset = 0
    while True:
        page = read_context_capture(argparse.Namespace(capture_file=path,
            capture_sha256=digest, offset=offset, page_bytes=size))
        assert page["ok"]
        pages.append(page)
        if page["eof"]:
            break
        offset = page["next_offset"]
    return pages


def deliver(root, event, pages):
    for page in pages:
        assert context.codex_context_hook(str(root), {**event, "hook_event_name": "PostToolUse",
            "tool_use_id": f"page-{page.get('offset')}", "tool_response": json.dumps(page)}) == {}


def test_complete_reader_delivery_allows_delta_without_diagnostics_or_ack(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    payload = {"ok": True, "plan_summary": {"requirements": "Preserve every obligation. " * 1000}}
    _, first = project(tmp_path, payload=payload)
    assert "candidate" not in context._state(tmp_path)
    deliver(tmp_path, event, read_pages(tmp_path, first))
    state = context._state(tmp_path)
    assert state["candidate"]["basis"] == "native_hook_assumption" and "pending" not in state
    assert "retained" not in state and "active_call" not in state
    assert context.codex_context_hook(str(tmp_path), {**event, "source": "resume"}) == {}
    # Ordinary tools and normal turn endings preserve the completed baseline.
    for name in ("PreToolUse", "PostToolUse", "Stop", "SessionEnd"):
        context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": name,
            "tool_response": "ordinary tool output"})
    assert context._state(tmp_path) == state
    _, second = project(tmp_path, "two", payload=payload)
    assert second["mode"] == "delta" and second["continuity_basis"] == "native_hook_assumption"
    assert not (tmp_path / "traces").exists()
    deliver(tmp_path, event, read_pages(tmp_path, second))
    _, third = project(tmp_path, "three", payload=payload)
    assert third["mode"] == "delta"


@pytest.mark.parametrize("failure", ["tail_only", "gap", "truncated", "interrupted", "reader_error", "failed_tool"])
def test_known_incomplete_delivery_cannot_establish_baseline(tmp_path, monkeypatch, failure):
    event = setup(tmp_path, monkeypatch)
    payload = {"ok": True, "plan_summary": {"requirements": "Preserve every obligation. " * 1000}}
    _, first = project(tmp_path, payload=payload)
    pages = read_pages(tmp_path, first)
    if failure == "tail_only":
        deliver(tmp_path, event, pages[-1:])  # EOF alone is insufficient
    elif failure == "gap":
        deliver(tmp_path, event, pages[:1] + pages[2:])
    elif failure == "truncated":
        deliver(tmp_path, event, [{**pages[0], "content": pages[0]["content"][:-1]}])
    elif failure == "interrupted":
        deliver(tmp_path, event, pages[:1])
        context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "Interrupt"})
    elif failure == "failed_tool":
        context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PostToolUse",
            "tool_response": {"exit_code": 1, "output": json.dumps(pages[0])}})
    else:
        deliver(tmp_path, event, [{"ok": False, "schema_version": "context_capture_page_v1"}])
    assert "candidate" not in context._state(tmp_path)
    _, second = project(tmp_path, "two", payload=payload)
    assert second["mode"] == "full"
    assert second["recovery"]["path"] != first["recovery"]["path"]
    assert json.loads(Path(first["recovery"]["path"]).read_bytes()) == payload


@pytest.mark.parametrize("mode", ["full", "delta"])
@pytest.mark.parametrize("damage", ["changed_bytes", "missing_file", "changed_file", "symlink"])
def test_hook_cli_rejects_changed_page_bytes_and_recovers_original(tmp_path, monkeypatch, mode, damage):
    event = setup(tmp_path, monkeypatch)
    payload = {"ok": True, "plan_summary": {"requirements": "Preserve every obligation. " * 1000}}
    _, wire = project(tmp_path, payload=payload)
    if mode == "delta":
        deliver(tmp_path, event, read_pages(tmp_path, wire))
        _, wire = project(tmp_path, "two", payload=payload)
    assert wire["mode"] == mode
    pages = read_pages(tmp_path, wire, size=8192)
    before = context._state(tmp_path)
    recovery = before["recovery"]
    raw = Path(recovery["path"]).read_bytes()
    source = Path(before["pending"]["path"])
    source_raw = source.read_bytes()
    changed = [dict(page) for page in pages]
    # Keep valid JSON, every byte offset and the self-reported digest unchanged.
    # Page coverage and byte length alone cannot distinguish this wrong body.
    first = changed[0]["content"]
    index = next(i for i, char in enumerate(first) if char.isascii() and char.isalpha())
    if damage == "changed_bytes":
        changed[0]["content"] = first[:index] + ("X" if first[index] != "X" else "Y") + first[index + 1:]
        assert len(changed[0]["content"].encode()) == len(first.encode())
        assert json.loads("".join(page["content"] for page in changed))
    elif damage == "changed_file":
        source.write_bytes(source_raw + b" ")  # valid JSON, different pinned digest
    else:
        source.unlink()
        if damage == "symlink":
            alias_target = tmp_path / "same-bytes.json"
            alias_target.write_bytes(source_raw)
            source.symlink_to(alias_target)
    repo = Path(__file__).resolve().parents[2]
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    cmd = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry), "--runtime-root", str(runtime),
        "--format", "json", "quota", "context-hook", "--context-delivery", "on", "--codex-app",
        "--codex-context-dir", str(tmp_path)]

    def hook(values):
        response = {"content": [{"type": "text", "text": json.dumps(page)} for page in values]}
        result = subprocess.run(cmd, input=json.dumps({**event, "hook_event_name": "PostToolUse",
            "tool_use_id": "capture-read", "tool_response": response}), capture_output=True, text=True,
            cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(repo)}, timeout=20)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {}

    hook(changed)
    assert "candidate" not in context._state(tmp_path)
    assert context._state(tmp_path)["recovery"] == recovery
    if damage != "changed_bytes":
        source.unlink(missing_ok=True)
        source.write_bytes(source_raw)
        source.chmod(0o600)
    assert Path(recovery["path"]).read_bytes() == raw
    # Recover the same full capture, including after a damaged delta. Never
    # reuse the incomplete wire or collect/admit another business decision.
    hook(capture_pages(recovery["path"], recovery["sha256"], size=8192))
    assert context._state(tmp_path)["candidate"]["capture_sha256"] == recovery["sha256"]
    _, after = project(tmp_path, "three", payload=payload)
    assert after["mode"] == "delta"
    assert not registry.exists() and not runtime.exists()


def test_legacy_delta_without_pinned_path_recovers_only_from_known_full_capture(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    payload = {"ok": True, "plan_summary": {"requirements": "Preserve every obligation. " * 1000}}
    _, full = project(tmp_path, payload=payload)
    deliver(tmp_path, event, read_pages(tmp_path, full))
    _, delta = project(tmp_path, "two", payload=payload)
    assert delta["mode"] == "delta"
    pages = read_pages(tmp_path, delta)
    state = context._state(tmp_path)
    # An older/incomplete journal has no trusted path for this wire. A path
    # supplied by the tool output cannot repair that missing local reference.
    path = state["pending"].pop("path")
    recovery = state["recovery"]
    context._save(tmp_path, state)
    deliver(tmp_path, event, [{**page, "path": path} for page in pages])
    assert "candidate" not in context._state(tmp_path)
    assert context._state(tmp_path)["recovery"] == recovery
    deliver(tmp_path, event, capture_pages(recovery["path"], recovery["sha256"]))
    assert context._state(tmp_path)["candidate"]["capture_sha256"] == recovery["sha256"]


def test_unverified_page_facts_cannot_promote_through_the_shared_owner(tmp_path, monkeypatch):
    from loopx.control_plane.quota.turn_envelope import update_native_context_lifecycle

    event = setup(tmp_path, monkeypatch)
    _, wire = project(tmp_path)
    pages = read_pages(tmp_path, wire)
    before = context._state(tmp_path)
    updated = update_native_context_lifecycle(before, {**event, "hook_event_name": "PostToolUse"},
        new_epoch="unverified-pages", pages=pages)
    assert "candidate" not in updated and "pending" not in updated
    assert updated["recovery"] == before["recovery"]


@pytest.mark.parametrize("route", ["resume", "projection"])
def test_metadata_only_baselines_require_a_new_verified_read(tmp_path, monkeypatch, route):
    event = setup(tmp_path, monkeypatch)
    payload = {"ok": True, "plan_summary": {"requirements": "Preserve every obligation. " * 1000}}
    _, wire = project(tmp_path, payload=payload)
    pages = read_pages(tmp_path, wire)
    deliver(tmp_path, event, pages)
    before = context._state(tmp_path)
    assert before["candidate"].pop("capture_bytes_verified") is True
    context._save(tmp_path, before)
    if route == "projection":
        _, next_wire = project(tmp_path, "two", payload=payload)
        assert next_wire["mode"] == "full" and next_wire["reason"] == "baseline_unavailable"
    else:
        context.codex_context_hook(str(tmp_path), {**event, "source": "resume"})
        assert "candidate" not in context._state(tmp_path)
        assert context._state(tmp_path)["recovery"] == before["recovery"]
        deliver(tmp_path, event, pages)
        assert context._state(tmp_path)["candidate"]["capture_bytes_verified"] is True


@pytest.mark.parametrize("damage", ["whitespace", "public_mode"])
def test_verified_candidate_requires_unchanged_private_capture(tmp_path, monkeypatch, damage):
    event = setup(tmp_path, monkeypatch)
    payload = {"ok": True, "plan_summary": {"requirements": "Preserve every obligation. " * 1000}}
    _, wire = project(tmp_path, payload=payload)
    deliver(tmp_path, event, read_pages(tmp_path, wire))
    candidate = context._state(tmp_path)["candidate"]
    assert candidate["capture_bytes_verified"] is True
    path = Path(candidate["capture_file"])
    if damage == "whitespace":
        path.write_bytes(path.read_bytes() + b" ")  # identical decoded context, different original bytes
    else:
        path.chmod(0o644)
    _, next_wire = project(tmp_path, "two", payload=payload)
    assert next_wire["mode"] == "full" and next_wire["reason"] == "baseline_unavailable"


def test_parallel_and_duplicate_pages_are_idempotent_and_cannot_borrow_other_scope(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    event = setup(tmp_path, monkeypatch)
    payload = {"ok": True, "plan_summary": {"requirements": "Preserve every obligation. " * 1000}}
    _, first = project(tmp_path, payload=payload)
    pages = read_pages(tmp_path, first)
    # A subagent's parent session id and another native session never promote it.
    for extra in ({"agent_id": "child"}, {"session_id": "other"}, {"cwd": str(tmp_path.parent)}):
        deliver(tmp_path, {**event, **extra}, pages)
    assert "candidate" not in context._state(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda page: deliver(tmp_path, event, [page]), reversed(pages)))
    state = context._state(tmp_path)
    assert state["candidate"]["basis"] == "native_hook_assumption"
    deliver(tmp_path, event, pages)
    assert context._state(tmp_path) == state
    _, second = project(tmp_path, "two", payload=payload)
    assert second["mode"] == "delta"


def test_compaction_reset_recovers_the_same_original_before_new_delta(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    payload = {"ok": True, "plan_summary": {"requirements": "Preserve every obligation. " * 1000}}
    _, first = project(tmp_path, payload=payload)
    pages = read_pages(tmp_path, first)
    deliver(tmp_path, event, pages)
    old_epoch = context._state(tmp_path)["context_epoch"]
    for name in ("PreCompact", "PostCompact", "SessionStart"):
        reply = context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": name, "source": "compact"})
    reset = context._state(tmp_path)
    assert reset["context_epoch"] != old_epoch and "candidate" not in reset
    assert first["recovery"]["sha256"] in reply["hookSpecificOutput"]["additionalContext"]
    # The unchanged original reader, not a fresh guard, restores the baseline.
    deliver(tmp_path, event, pages)
    assert context._state(tmp_path)["candidate"]["target"]["context_epoch"] == reset["context_epoch"]
    _, second = project(tmp_path, "two", payload=payload)
    assert second["mode"] == "delta"


def test_configuration_round_trip_requires_a_new_full_baseline(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    payload = {"ok": True, "plan_summary": {"requirements": "Preserve every obligation. " * 1000}}
    _, first = project(tmp_path, payload=payload, generation="enabled-before-off")
    deliver(tmp_path, event, read_pages(tmp_path, first))
    _, second = project(tmp_path, "two", payload=payload, generation="enabled-after-off")
    assert second["mode"] == "full" and second["reason"] == "baseline_unavailable"
    deliver(tmp_path, event, read_pages(tmp_path, second))
    _, third = project(tmp_path, "three", payload=payload, generation="enabled-after-off")
    assert third["mode"] == "delta"



def test_primary_write_failure_preserves_small_recovery_and_does_not_create_ack(tmp_path, monkeypatch):
    event = setup(tmp_path, monkeypatch)
    _, wire = project(tmp_path)
    def unavailable(*args, **kwargs):
        raise OSError("synthetic journal unavailable")
    monkeypatch.setattr(context, "_save", unavailable)
    result = context.codex_context_hook(str(tmp_path), {**event, "source": "compact"})
    assert wire["recovery"]["sha256"] in result["hookSpecificOutput"]["additionalContext"]
    assert not (tmp_path / "session.json").exists()
    assert Path(wire["recovery"]["path"]).exists()


def _assert_recovery_after_fallback(root, event, source, capture):
    import argparse
    import hashlib
    from loopx.cli_commands.quota_capture import read_context_capture

    hook = context.codex_context_hook(str(root), {**event, "source": source})
    args = argparse.Namespace(codex_context_dir=str(root), goal_id="fixture", agent_id="agent",
                              offset=0, page_bytes=8192)
    page = read_context_capture(args)
    if capture is None:
        assert hook == {} and page["ok"] is False and "content" not in page
        return
    raw = capture.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert page["ok"] is True and page["sha256"] == digest
    assert page["content"].encode() == raw[:len(page["content"].encode())]
    assert digest in hook["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("source", ["compact", "resume"])
def test_large_capture_keeps_current_full_recovery(tmp_path, monkeypatch, source):
    from loopx.control_plane.effect_runtime import MAX_REQUEST_BYTES

    event = setup(tmp_path, monkeypatch)
    before = {"ok": True, "goal_id": "fixture", "stop": "Stop before publication.",
              "plan_summary": {"requirements": "x" * (MAX_REQUEST_BYTES // 2 + 8192)}}
    after = {**before, "stop": "Stop before publication or deployment."}
    _, first = project(tmp_path, payload=before)
    original = Path(first["recovery"]["path"]).read_bytes()
    assert len(original) < MAX_REQUEST_BYTES
    deliver(tmp_path, event, read_pages(tmp_path, first, size=8192))
    _, delivered = project(tmp_path, "two", payload=after)
    assert delivered["mode"] == "delta"
    current_capture = tmp_path / "two/projection.json"
    assert json.loads(current_capture.read_bytes()) == after
    _assert_recovery_after_fallback(tmp_path, event, source, current_capture)
    assert Path(first["recovery"]["path"]).read_bytes() == original


@pytest.mark.parametrize("source", ["compact", "resume"])
@pytest.mark.parametrize("failed_write", ["recovery", "candidate"])
def test_journal_failure_never_recovers_previous_capture(tmp_path, monkeypatch, source, failed_write):
    event = setup(tmp_path, monkeypatch)
    _, first = project(tmp_path)
    original = Path(first["recovery"]["path"]).read_bytes()
    current_capture = tmp_path / "two/projection.json"
    original_save, failures = context._save, []

    def fail_current_write(root, state):
        current_recovery = state.get("recovery", {}).get("sha256") not in (None, first["recovery"]["sha256"])
        current_candidate = isinstance(state.get("recovery", {}).get("target"), dict)
        if current_recovery and (failed_write == "recovery" or current_candidate):
            failures.append(failed_write)
            raise OSError("synthetic current publication unavailable")
        original_save(root, state)

    with monkeypatch.context() as publication:
        publication.setattr(context, "_save", fail_current_write)
        payload, delivered = project(tmp_path, "two", payload={"ok": True, "goal_id": "fixture",
            "requirements": "Preserve the changed source and stop before deployment."})
    assert failures and delivered.get("context", delivered) == payload
    assert json.loads(current_capture.read_bytes()) == payload
    _assert_recovery_after_fallback(tmp_path, event, source,
                                   current_capture if failed_write == "candidate" else None)
    assert Path(first["recovery"]["path"]).read_bytes() == original


@pytest.mark.parametrize("source", ["compact", "resume"])
@pytest.mark.parametrize("caller", ["subdirectory", "missing_session", "foreign_session"])
def test_fallback_identity_restoration_keeps_recovery_scoped(tmp_path, monkeypatch, source, caller):
    event = setup(tmp_path, monkeypatch)
    _, first = project(tmp_path)
    original_capture = Path(first["recovery"]["path"])
    original = original_capture.read_bytes()
    context.codex_context_hook(str(tmp_path), {**event, "hook_event_name": "PreToolUse", "tool_use_id": "guard"})
    with monkeypatch.context() as native_tool:
        if caller == "subdirectory":
            subdirectory = tmp_path / "subdirectory"
            subdirectory.mkdir()
            native_tool.chdir(subdirectory)
        elif caller == "missing_session":
            native_tool.delenv("CODEX_THREAD_ID")
        else:
            native_tool.setenv("CODEX_THREAD_ID", "foreign-session")
        payload, delivered = project(tmp_path, "two", payload={"ok": True, "goal_id": "fixture",
            "requirements": "Changed source; stop before deployment."})
    assert delivered == payload
    _assert_recovery_after_fallback(tmp_path, event, source,
        original_capture if caller == "foreign_session" else None)
    assert original_capture.read_bytes() == original


@pytest.mark.parametrize("failure", ["invalidation", "lock"])
def test_unavailable_recovery_boundary_refuses_current_publication(tmp_path, monkeypatch, failure):
    from loopx.control_plane.quota.error_codes import QuotaDecisionTransportError

    setup(tmp_path, monkeypatch)
    _, first = project(tmp_path)
    journal = tmp_path / "session.json"
    original = journal.read_bytes()
    unlink = Path.unlink

    def unavailable(*args, **kwargs):
        raise OSError("synthetic journal publication unavailable")

    def cannot_invalidate(path, *args, **kwargs):
        if path == journal:
            raise OSError("synthetic journal invalidation unavailable")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as publication:
        publication.setattr(context, "_save", unavailable)
        publication.setattr(Path, "unlink", cannot_invalidate)
        if failure == "lock":
            publication.setattr(context, "_locked", unavailable)
        with pytest.raises(QuotaDecisionTransportError):
            project(tmp_path, "two", payload={"ok": True, "goal_id": "fixture", "requirements": "Current source."})
    assert journal.read_bytes() == original
    assert json.loads(Path(first["recovery"]["path"]).read_bytes())["requirements"] == "Preserve final stop condition."


@pytest.mark.parametrize("damage", ["missing", "corrupt", "symlink", "public", "other_workspace",
    "missing_epoch", "invalid_epoch", "read_failure", "projection_failure"])
def test_invalid_journal_falls_back_to_original_full(tmp_path, monkeypatch, damage):
    event = setup(tmp_path, monkeypatch)
    project(tmp_path)
    path = tmp_path / "session.json"
    before = path.read_bytes()
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_text("{partial")
    elif damage == "symlink":
        other = tmp_path / "other.json"
        path.rename(other)
        path.symlink_to(other)
    elif damage == "public":
        path.chmod(0o644)
    elif damage == "other_workspace":
        state = context._state(tmp_path)
        state["cwd"] = str(tmp_path.parent)
        context._save(tmp_path, state)
    elif damage in {"missing_epoch", "invalid_epoch"}:
        state = context._state(tmp_path)
        state.pop("context_epoch")
        if damage == "invalid_epoch":
            state["context_epoch"] = 3
        context._save(tmp_path, state)
    with monkeypatch.context() as failed:
        if damage in {"read_failure", "projection_failure"}:
            def unavailable(*args, **kwargs):
                raise OSError("synthetic pre-publication IO unavailable")
            failed.setattr(context, "_state" if damage == "read_failure" else "capture_context_projection", unavailable)
        payload, full = project(tmp_path, "two")
    assert full == payload
    if damage == "symlink":
        assert other.read_bytes() == before
    _assert_recovery_after_fallback(tmp_path, event, "compact", None)


def test_real_hook_cli_is_transport_only_without_registry_or_installation(tmp_path):
    directory = tmp_path / "native"
    directory.mkdir(mode=0o700)
    registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
    cmd = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry), "--runtime-root", str(runtime),
        "quota", "context-hook", "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(directory)]
    event = dict(session_id="session", cwd=str(tmp_path), hook_event_name="SessionStart", source="startup")
    result = subprocess.run(cmd, input=json.dumps(event), capture_output=True, text=True, timeout=20,
        cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stderr
    assert context._state(directory)["session_id"] == "session"
    assert not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("damage", [None, "session", "workspace", "goal", "agent", "missing_scope", "missing", "corrupt", "public", "symlink"])
def test_lost_hook_reference_is_scoped_read_only_and_pins_later_pages(tmp_path, monkeypatch, damage):
    import argparse
    from loopx.cli_commands.quota_capture import read_context_capture

    setup(tmp_path, monkeypatch)
    _, first = project(tmp_path, payload={"goal_id": "fixture", "requirements": "保留🙂" * 400})
    journal = tmp_path / "session.json"
    args = argparse.Namespace(codex_context_dir=str(tmp_path), goal_id="fixture", agent_id="agent",
                              offset=0, page_bytes=256)
    if damage == "session":
        monkeypatch.setenv("CODEX_THREAD_ID", "other")
    elif damage == "workspace":
        monkeypatch.chdir(tmp_path.parent)
    elif damage in {"goal", "agent"}:
        setattr(args, damage + "_id", "other")
    elif damage == "missing_scope":
        state = context._state(tmp_path)
        state["recovery"].pop("goal_id")
        context._save(tmp_path, state)
    elif damage == "missing":
        journal.unlink()
    elif damage == "corrupt":
        journal.write_text("{broken")
    elif damage == "public":
        journal.chmod(0o644)
    elif damage == "symlink":
        original = tmp_path / "original.json"
        journal.rename(original)
        journal.symlink_to(original)
    before = journal.read_bytes() if journal.exists() else None
    page = read_context_capture(args)
    assert (journal.read_bytes() if journal.exists() else None) == before
    assert page["ok"] is (damage is None)
    if damage:
        assert "content" not in page
        return
    assert page["sha256"] == first["recovery"]["sha256"] and page["offset"] == 0
    assert "--codex-context-dir" not in page["next_command"]
    assert first["recovery"]["sha256"] in page["next_command"]
    # Publication can move the selector; the first page's explicit next command
    # must continue the original capture, never silently switch to the new one.
    _, second = project(tmp_path, "two", payload={"goal_id": "fixture", "requirements": "changed " * 400})
    assert second["recovery"]["sha256"] != page["sha256"]
    args.capture_file = first["recovery"]["path"]
    args.capture_sha256 = first["recovery"]["sha256"]
    args.codex_context_dir = None
    args.offset = page["next_offset"]
    rest = read_context_capture(args)
    assert rest["ok"] and rest["sha256"] == page["sha256"]
    assert rest["content"] == Path(args.capture_file).read_bytes()[args.offset:args.offset + len(rest["content"].encode())].decode()
    args.codex_context_dir = str(tmp_path)
    assert not read_context_capture(args)["ok"]  # no mixed moving and pinned selectors



@pytest.mark.parametrize("setting", [None, "off", "on"])
@pytest.mark.parametrize("host_args,expected", [
    ([], None),
    (["--codex-app"], "codex_app_heartbeat"),
    (["--runtime-profile", "codex_app_heartbeat"], "codex_app_heartbeat"),
    (["--host-surface", "codex_app", "--scheduler-owner", "host_automation", "--execution-mode", "hosted_automation"], "codex_app_heartbeat"),
    (["--host-surface", "codex_cli", "--scheduler-owner", "outer_controller", "--execution-mode", "isolated_headless"], None),
    (["--runtime-profile", "generic_cli"], None),
    (["--runtime-profile", "claude_code"], None),
    (["--runtime-profile", "codex_cli"], None),
    (["--host-surface", "codex_app"], None),
    (["--codex-app", "--host-surface", "codex_cli"], None),
    (["--codex-app", "--runtime-profile", "generic_cli"], None),
    (["--codex-app", "--trae_app"], None),
])
def test_activation_reuses_declared_host_mode_and_off_is_inert(tmp_path, monkeypatch, setting, host_args, expected):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import handle_native_context_command

    # Ambient native environment is not an independent activation or mode proof.
    monkeypatch.setenv("CODEX_THREAD_ID", "ambient-native-session")
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    tmp_path.chmod(0o700)
    args = ["--format", "json", "quota", "context-config", "--codex-context-dir", str(tmp_path),
            "--goal-id", "fixture", "--agent-id", "agent", *host_args]
    if setting is not None:
        args += ["--context-delivery", setting]
    results = []
    parsed = build_parser().parse_args(args)
    code = handle_native_context_command(parsed, print_payload=lambda value, *_: results.append(value))
    result = results[0]
    if setting == "on" and expected is None:
        assert code == 1 and result["ok"] is False
        assert "supported declared run mode" in result["reason"]
        assert "--context-delivery off" in result["reason"]
        assert "native_config" not in result and list(tmp_path.iterdir()) == []
        return
    assert code == 0 and result["ok"] is True
    activation = result["context_delivery"]
    expected = expected if setting == "on" else None
    assert activation["requested"] == (setting or "off")
    assert activation["enabled"] is (expected is not None) and activation["scenario"] == expected
    assert activation["supported_scenarios"] == ["codex_benchmark_heartbeat", "codex_app_heartbeat"]
    if expected is None:
        assert "native_config" not in result
    else:
        for entries in result["native_config"]["hooks"].values():
            assert "--context-delivery on" in entries[0]["hooks"][0]["command"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("setting", ["true", "generic_cli", "claude_code", "all", "codex_app_heartbeat"])
def test_context_activation_rejects_non_binary_settings(setting):
    from loopx.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["quota", "context-config", "--context-delivery", setting])


@pytest.mark.parametrize("mode,setting,expected", [("heartbeat", "on", True), ("plain", "on", False),
    (None, "on", False), ("heartbeat", "off", False)])
def test_benchmark_activation_uses_existing_explicit_worker_declaration(monkeypatch, mode, setting, expected):
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import native_context_activation

    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "on")
    if mode is not None:
        monkeypatch.setenv("LOOPX_EXECUTION_MODE", mode)
    else:
        monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    args = build_parser().parse_args(["quota", "context-config", "--runtime-profile", "generic_cli",
        "--context-delivery", setting])
    if setting == "on" and not expected:
        with pytest.raises(ValueError, match="supported declared run mode"):
            native_context_activation(args)
        return
    result = native_context_activation(args)
    assert result["enabled"] is expected
    assert result["scenario"] == ("codex_benchmark_heartbeat" if expected else None)
