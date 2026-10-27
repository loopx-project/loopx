"""Original-capture paging never consults current authority or reruns a guard."""
import argparse
import hashlib
import json
import os
import shlex

import pytest

from loopx.cli_commands.quota_capture import read_context_capture
from test_quota_settlement_cli import _run_cli
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal


def _args(path, raw, **changes):
    return argparse.Namespace(capture_file=str(path), capture_sha256=hashlib.sha256(raw).hexdigest(),
        **{"offset": 0, "page_bytes": 256, **changes})


def test_cli_pages_reconstruct_unicode_capture_without_goal_or_runtime(tmp_path):
    path = tmp_path / "capture with ' quote.json"
    original = {"requirements": ("原始要求🙂\n保留权限、停止条件与证据。" * 70), "late": "stop now", "null": None}
    raw = (json.dumps(original, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    runtime, registry = tmp_path / "absent-runtime", tmp_path / "absent-registry.json"
    args = ["quota", "context-read", "--capture-file", str(path), "--capture-sha256",
            hashlib.sha256(raw).hexdigest(), "--page-bytes", "256"]
    chunks, offset = [], 0
    while True:
        code, page = _run_cli(registry, runtime, *args, cwd=tmp_path)
        assert code == 0 and page["ok"], page
        assert page["offset"] == offset and page["total_bytes"] == len(raw)
        chunk = page["content"].encode()
        assert 0 < len(chunk) <= 256
        assert chunk == raw[offset:offset + len(chunk)]
        chunks.append(chunk)
        offset += len(chunk)
        if page["eof"]:
            assert page["next_offset"] is None and "next_command" not in page
            break
        assert page["next_offset"] == offset
        command = shlex.split(page["next_command"])
        assert command[:3] == ["loopx", "--format", "json"]
        args = command[3:]
    assert b"".join(chunks) == raw and json.loads(b"".join(chunks)) == original
    assert path.read_bytes() == raw and not runtime.exists() and not registry.exists()


@pytest.mark.parametrize("kind", ["digest", "digest_uppercase", "digest_envelope", "digest_newline", "digest_short",
    "changed_between_pages", "missing", "symlink", "directory",
    "not_json", "not_object", "not_utf8", "offset", "utf8_offset", "small_page", "large_page"])
def test_invalid_capture_never_returns_partial_context(tmp_path, kind):
    path = tmp_path / "projection.json"
    raw = json.dumps({"source": "原始🙂" * 100}, ensure_ascii=False).encode()
    path.write_bytes(raw)
    args = _args(path, raw)
    if kind == "digest":
        args.capture_sha256 = "0" * 64
    elif kind in {"digest_uppercase", "digest_envelope", "digest_newline", "digest_short"}:
        original_digest = args.capture_sha256
        args.capture_sha256 = {
            "digest_uppercase": original_digest.upper(),
            "digest_envelope": "sha256:" + original_digest,
            "digest_newline": original_digest + "\n",
            "digest_short": original_digest[:-1],
        }[kind]
    elif kind == "changed_between_pages":
        first = read_context_capture(args)
        assert first["ok"] and not first["eof"]
        args.offset = first["next_offset"]
        path.write_bytes(raw.replace(b"source", b"target"))
    elif kind == "missing":
        path.unlink()
    elif kind == "symlink":
        target = tmp_path / "original.json"
        path.rename(target)
        path.symlink_to(target)
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    elif kind in {"not_json", "not_object", "not_utf8"}:
        raw = {"not_json": b'{"missing":', "not_object": b'[]', "not_utf8": b'{"x":"\xff"}'}[kind]
        path.write_bytes(raw)
        args.capture_sha256 = hashlib.sha256(raw).hexdigest()
    elif kind == "offset":
        args.offset = len(raw) + 1
    elif kind == "utf8_offset":
        args.offset = raw.index("原".encode()) + 1
    else:
        args.page_bytes = 255 if kind == "small_page" else 8193
    result = read_context_capture(args)
    assert result["ok"] is False and "content" not in result and "next_command" not in result


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipe")
def test_capture_reader_rejects_fifo_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "projection.json"
    os.mkfifo(path)
    assert read_context_capture(_args(path, b"{}"))["ok"] is False


def test_capture_reader_accepts_parent_directory_alias(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(original, target_is_directory=True)
    raw = b'{"requirement":"preserve the complete original capture"}'
    (original / "projection.json").write_bytes(raw)
    result = read_context_capture(_args(alias / "projection.json", raw))
    assert result["ok"] and result["eof"] and result["content"].encode() == raw


def test_recovery_page_keeps_invocation_executable_and_resets_afterwards(tmp_path):
    from loopx.command_invocation import loopx_cli_route
    path = tmp_path / "projection.json"
    raw = json.dumps({"requirements": "preserve " * 100}).encode()
    path.write_bytes(raw)
    executable = str(tmp_path / "pinned release" / "loopx")
    with loopx_cli_route(executable):
        page = read_context_capture(_args(path, raw))
    assert shlex.split(page["next_command"])[:3] == [executable, "--cli-bin", executable]
    plain = read_context_capture(_args(path, raw))
    assert shlex.split(plain["next_command"])[:3] == ["loopx", "--format", "json"]


def _outcome_capture(tmp_path):
    outcome = {"id": "own", "owner_agent_id": "worker", "objective": "Keep every original requirement.",
        "revision": 2, "requirements": [{"id": "r1", "description": "Exact 原文🙂 " * 70,
            "planning_satisfied": False, "status": "unverified",
            "agent_review": {"conclusion": "met", "basis_current": False,
                "independently_verified": False, "note": "Historical only", "evidence_refs": ["evidence:original"]}}],
        "todo_ids": ["work"], "work": [{"todo_id": "work", "status": "blocked"}],
        "sources": [{"ref": "user:original", "summary": "Keep the complete declaration."}],
        "future_field": {"permission": False, "unknown": None}}
    acceptance = {"outcome_requirements": {"current_lifetime": True, "outcomes": [outcome]}}
    source = {"ok": True, "goal_id": "goal", "agent_identity": {"agent_id": "worker"},
        "interaction_contract": {"agent_channel": {"work_context": {"sources": [
            {"source": "goal_acceptance", "content": {"status": "loaded", "goal_acceptance_contract": acceptance}}]}}},
        "planning_horizon": {"core_goal": {"acceptance_contract_ref":
            "$.interaction_contract.agent_channel.work_context.sources[0].content.goal_acceptance_contract"}}}
    raw = (json.dumps(source, ensure_ascii=False, indent=2) + "\n").encode()
    path = tmp_path / "captured ' ordinary.json"
    path.write_bytes(raw)
    ref = "sha256:" + hashlib.sha256(json.dumps(outcome, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return path, raw, outcome, ref


def test_outcome_index_and_detail_share_original_capture_without_authority(tmp_path):
    path, raw, outcome, ref = _outcome_capture(tmp_path)
    digest = hashlib.sha256(raw).hexdigest()
    registry, runtime = tmp_path / "absent-registry", tmp_path / "absent-runtime"
    base = ["quota", "context-read", "--capture-file", str(path), "--capture-sha256", digest,
        "--max-page-output-bytes", "5000", "--page-bytes", "4096", "--outcome-ref"]
    code, page = _run_cli(registry, runtime, *base, cwd=tmp_path)
    assert code == 0 and page["ok"], page
    assert page["outcome_ref"] is None and page["eof"]
    index = json.loads(page["content"])
    assert len(index["outcomes"]) == 1
    row = index["outcomes"][0]
    assert row["outcome_ref"] == ref and row["owner_agent_id"] == "worker"
    assert row["work"] == outcome["work"]
    assert row["requirement_states"] == [{"requirement_ids": ["r1"], "planning_satisfied": False, "status": "unverified",
        "agent_review": {"conclusion": "met", "basis_current": False, "independently_verified": False}}]
    assert "description" not in row["requirement_states"][0]
    assert "full-read obligations" in index["instruction"]
    args, chunks, offset = [*base, ref, "--page-bytes", "256"], [], 0
    while True:
        code, page = _run_cli(registry, runtime, *args, cwd=tmp_path)
        assert code == 0 and page["ok"], page
        assert page["sha256"] == digest and page["outcome_ref"] == ref and page["offset"] == offset
        chunks.append(page["content"].encode())
        offset += len(chunks[-1])
        if page["eof"]:
            assert page["total_bytes"] == offset and page["next_offset"] is None
            break
        command = shlex.split(page["next_command"])
        assert command[command.index("--outcome-ref") + 1] == ref
        assert command[command.index("--selection-sha256") + 1] == page["selection_sha256"]
        args = command[3:]
    detail = json.loads(b"".join(chunks))
    assert detail["outcome"] == outcome and detail["outcome_ref"] == ref
    assert path.read_bytes() == raw and not registry.exists() and not runtime.exists()


def test_outcome_pages_cannot_count_as_full_capture_retention(tmp_path):
    from loopx.control_plane.turn_driver.decision_capture import capture_page_range
    path, raw, _, ref = _outcome_capture(tmp_path)
    page = read_context_capture(_args(path, raw, outcome_ref=ref))
    assert page["ok"]
    assert capture_page_range(page, raw=raw, digest=hashlib.sha256(raw).hexdigest()) is None
    # Even a coincidentally identical byte range is a scoped read, never a
    # full-context observation a native Host may use to establish continuity.
    ordinary = read_context_capture(_args(path, raw))
    assert capture_page_range(ordinary, raw=raw, digest=hashlib.sha256(raw).hexdigest()) is not None
    assert capture_page_range({**ordinary, "outcome_ref": None}, raw=raw,
        digest=hashlib.sha256(raw).hexdigest()) is None


@pytest.mark.parametrize("ref", ["missing", "sha256:" + "0" * 64])
def test_outcome_unknown_reference_is_not_a_full_capture_fallback(tmp_path, ref):
    path, raw, _, _ = _outcome_capture(tmp_path)
    result = read_context_capture(_args(path, raw, outcome_ref=ref))
    assert result["ok"] is False and "content" not in result and "next_command" not in result


def test_outcome_detail_rechecks_source_before_next_page(tmp_path):
    path, raw, _, ref = _outcome_capture(tmp_path)
    args = _args(path, raw, outcome_ref=ref)
    page = read_context_capture(args)
    assert page["ok"] and not page["eof"]
    args.offset = page["next_offset"]
    args.selection_sha256 = page["selection_sha256"]
    path.write_bytes(raw + b" ")
    rejected = read_context_capture(args)
    assert not rejected["ok"] and "digest mismatch" in rejected["reason"]


def test_outcome_transport_failure_keeps_effect_free_exact_full_recovery(tmp_path, monkeypatch):
    from loopx.control_plane.effect_runtime import EffectRuntimeStartupError
    from loopx.control_plane.quota import turn_envelope
    path, raw, _, ref = _outcome_capture(tmp_path)

    def unavailable(*args, **kwargs):
        raise EffectRuntimeStartupError("synthetic unavailable transport", diagnostic_code="runtime_unavailable")

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", unavailable)
    result = read_context_capture(_args(path, raw, outcome_ref=ref))
    assert not result["ok"] and "unavailable" in result["reason"] and "content" not in result
    recovery = shlex.split(result["recovery_command"])
    assert recovery[recovery.index("--capture-file") + 1] == str(path)
    assert recovery[recovery.index("--capture-sha256") + 1] == hashlib.sha256(raw).hexdigest()
    assert "--outcome-ref" not in recovery
    assert read_context_capture(_args(path, raw))["ok"], "full recovery does not require the failed typed transport"
    assert path.read_bytes() == raw


def test_outcome_index_pagination_retains_selector_budget_and_pinned_cli(tmp_path):
    from loopx.command_invocation import loopx_cli_route

    path, raw, _, _ = _outcome_capture(tmp_path)
    args = _args(path, raw, outcome_ref="", format="json", max_page_output_bytes=1600)
    with loopx_cli_route(str(tmp_path / "pinned release" / "loopx")):
        page = read_context_capture(args)
    assert page["ok"] and not page["eof"] and page["outcome_ref"] is None
    command = shlex.split(page["next_command"])
    assert command[:3] == [str(tmp_path / "pinned release" / "loopx"), "--cli-bin",
        str(tmp_path / "pinned release" / "loopx")]
    assert command[-1] == "--outcome-ref"
    assert command[command.index("--selection-sha256") + 1] == page["selection_sha256"]
    assert command[command.index("--max-page-output-bytes") + 1] == "1600"
    chunks = [page["content"].encode()]
    while not page["eof"]:
        args.offset = page["next_offset"]
        args.selection_sha256 = page["selection_sha256"]
        page = read_context_capture(args)
        assert page["ok"] and page["outcome_ref"] is None
        chunks.append(page["content"].encode())
    assert len(json.loads(b"".join(chunks))["outcomes"]) == 1


@pytest.mark.parametrize("change", ["missing_digest", "wrong_digest", "changed_renderer"])
def test_outcome_cursor_cannot_mix_derived_layouts_under_one_capture_hash(tmp_path, monkeypatch, change):
    from loopx.control_plane.quota import turn_envelope

    path, raw, _, ref = _outcome_capture(tmp_path)
    args = _args(path, raw, outcome_ref=ref)
    page = read_context_capture(args)
    assert page["ok"] and not page["eof"]
    args.offset = page["next_offset"]
    if change != "missing_digest":
        args.selection_sha256 = "0" * 64 if change == "wrong_digest" else page["selection_sha256"]
    if change == "changed_renderer":
        original = turn_envelope._turn_envelope_result

        def next_renderer(*arguments, **kwargs):
            return {**original(*arguments, **kwargs), "future_reader_metadata": "layout changed"}

        monkeypatch.setattr(turn_envelope, "_turn_envelope_result", next_renderer)
    rejected = read_context_capture(args)
    assert not rejected["ok"] and "content" not in rejected and "next_command" not in rejected
    assert ("selection-sha256" if change == "missing_digest" else "digest mismatch") in rejected["reason"]
    assert path.read_bytes() == raw


def test_real_guard_outcomes_remain_bound_after_file_sqlite_authority_changes(acceptance_goal):
    from test_goal_outcome_refinement import record, refinement

    project, _, cli, run = acceptance_goal
    assert record(project, cli, refinement([{"id": "original", "description": "Keep the original demand"}],
        "message:first"), "captured-outcome-first")[0][0] == 0
    directory = project / "capture"
    code, decision = run("quota", "should-run", "--goal-id", "goal-acceptance", "--agent-id", "agent-a",
        "--turn-instance-id", "outcome-reader-turn", "--decision-output-dir", str(directory))
    assert code == 0 and decision["ok"], decision
    path = directory / "decision.json"
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    inspect_before = cli("inspect")[1]
    original = inspect_before["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]

    def read(ref):
        code, page = run("quota", "context-read", "--capture-file", str(path), "--capture-sha256", digest,
            "--page-bytes", "8192", "--max-page-output-bytes", "10000", "--outcome-ref", ref)
        assert code == 0 and page["ok"] and page["eof"], page
        return json.loads(page["content"])

    index = read("")
    ref = index["outcomes"][0]["outcome_ref"]
    assert index["outcomes"][0]["owner_agent_id"] == "agent-a"
    assert read(ref)["outcome"] == original
    assert cli("inspect")[1] == inspect_before, "reader cannot revise authority or append receipts"
    assert record(project, cli, {"outcome_id": "tour", "requirements": [
        {"id": "new", "description": "An actual later demand must not enter the old capture"}],
        "source": {"ref": "message:later", "summary": "Later owner refinement"}},
        "captured-outcome-later")[0][0] == 0
    inspect_after = cli("inspect")[1]
    assert len(inspect_after["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["requirements"]) == 2
    assert read(ref)["outcome"] == original and read("") == index
    assert cli("inspect")[1] == inspect_after and path.read_bytes() == raw


@pytest.mark.parametrize("command", ["should-run", "spend-slot", "status"])
def test_outcome_selector_cannot_be_silently_ignored_by_live_commands(tmp_path, command):
    registry, runtime = tmp_path / "absent-registry", tmp_path / "absent-runtime"
    code, result = _run_cli(registry, runtime, "quota", command, "--outcome-ref", cwd=tmp_path)
    assert code == 1 and result["ok"] is False and "only supported" in result["reason"]
    assert not registry.exists() and not runtime.exists()
