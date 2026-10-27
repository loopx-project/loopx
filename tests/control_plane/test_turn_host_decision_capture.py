"""Real CLI/provider/Host transport qualification; the model provider is synthetic."""
from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.turn_driver.decision_capture import prepare_turn_decision_capture, write_decision_capture
from loopx.control_plane.turn_driver.journal_store import load_loopx_turn_plan_from_journal, turn_journal_path
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime


def _fixture_module():
    path = Path(__file__).resolve().parents[2] / "examples/loopx-turn-codex-cli-e2e-smoke.py"
    spec = importlib.util.spec_from_file_location("turn_capture_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("first_attempt_fails", [False, True])
@pytest.mark.parametrize("legacy_pretty", [False, True])
def test_real_host_reads_original_capture_after_source_change(
    tmp_path, monkeypatch, provider, first_attempt_fails, legacy_pretty,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    f = _fixture_module()
    project, runtime, workspace, registry = f._write_fixture(tmp_path, turn_count=2)
    state = project / ".codex/goals" / f.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_bytes = state.read_bytes()
    code, listed = f._run_cli(["--registry", str(registry), "--runtime-root", str(runtime),
                             "--format", "json", "todo", "list", "--goal-id", f.GOAL_ID])
    assert code == 1 and listed["error_code"] == "canonical_authority_required", listed
    assert listed["legacy_fallback_used"] is False and state.read_bytes() == state_bytes
    # Convert the disposable fixture explicitly; ordinary reads must not
    # recover unmigrated work from its Markdown projection.
    summary = parse_active_state_todos(state_bytes.decode(), state_path=state, item_limit=None)
    initialize_canonical_authority(runtime, f.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=f.GOAL_ID, handoff_mode="soft_claim",
                                             todos=summary["agent_todos"]["items"]),
        state_path=state, provider=provider)
    executable = f._write_fake_codex(tmp_path)
    source = executable.read_text().replace("#!/usr/bin/env python3", "#!" + sys.executable)
    source = source.replace("prompt = sys.stdin.read()", 'prompt = sys.stdin.read()\npathlib.Path("received.txt").write_text(prompt)')
    if first_attempt_fails:
        source = source.replace("marker = pathlib.Path(",
            'failed = pathlib.Path("failed-once")\n'
            'if not failed.exists():\n'
            '    failed.touch()\n'
            '    print(json.dumps({"type":"thread.started","thread_id":"session-fixture-0001"}), flush=True)\n'
            '    print("Selected model is at capacity. Please try a different model.", file=sys.stderr)\n'
            '    raise SystemExit(9)\n'
            'marker = pathlib.Path(')
    executable.write_text(source)
    base = f._base_argv(registry=registry, runtime=runtime, workspace=workspace,
        codex_bin=executable, model=None, timeout_seconds=20,
        expected_marker=f._marker_value(1), turn_instance_id="original-observation")
    # Preview must not allocate a capture or invoke the provider.
    code, preview = f._run_cli(base)
    assert code == 0, preview
    assert not list(runtime.rglob("decisions/*.json"))
    assert not (workspace / "received.txt").exists()
    code, result = f._run_cli([*base, "--execute"])
    if first_attempt_fails:
        assert code == 1 and result["status"] == "failed", result
        assert f._quota_spend_count(runtime) == 0
    else:
        assert code == 0 and result["status"] == "committed", result
    request = json.loads((workspace / "received.txt").read_text().split("Turn request:\n", 1)[1])
    detail = request["turn_envelope"]["detail_ref"]
    capture = detail["captured_decision"]
    assert capture["turn_instance_id"] == "original-observation"
    assert capture["turn_key"] == request["turn_key"]
    path = Path(capture["path"])
    original = path.read_bytes()
    assert path.stat().st_mode & 0o077 == 0
    if legacy_pretty:
        # Model an observation produced by the older writer. Recovery must
        # preserve those original bytes rather than rewriting its layout.
        original = (json.dumps(json.loads(original), ensure_ascii=False, indent=2) + "\n").encode()
        path.write_bytes(original)
    else:
        assert original.endswith(b"\n") and b"\n" not in original[:-1]
    source_a = json.loads(original)
    assert source_a["goal_id"] == f.GOAL_ID
    assert source_a["agent_identity"]["agent_id"] == f.AGENT_ID
    # Direct `turn` bypasses quota CLI emission. Check its actual subprocess
    # prompt against the same original contracts, including recovery below.
    for field in ("work_lane_contract", "execution_obligation", "automation_liveness"):
        assert source_a[field]
        assert request["turn_envelope"]["contract_capsule"][field] == source_a[field]
    # Change the real provider through its native CAS CLI, not the projection.
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    code, current = f._run_cli([*prefix, "todo", "list", "--goal-id", f.GOAL_ID, "--todo-id", f.TODO_ID])
    assert code == 0
    code, update = f._run_cli([*prefix, "todo", "update", "--goal-id", f.GOAL_ID,
        "--todo-id", f.TODO_ID, "--agent-id", f.AGENT_ID, "--text", current["todo"]["text"] + " SOURCE_B",
        "--update-operation-id", "source-b", "--update-expected-provider-revision",
        current["authority_read"]["provider_revision"], "--execute"])
    assert code == 0, update
    # Execute precisely what the actual subprocess received.
    detail_bytes = subprocess.run(shlex.split(detail["full_decision"]), check=True,
                                  capture_output=True).stdout
    assert detail_bytes == original and b"SOURCE_B" not in detail_bytes
    code, current = f._run_cli([*prefix, "todo", "list", "--goal-id", f.GOAL_ID, "--todo-id", f.TODO_ID])
    assert code == 0 and "SOURCE_B" in current["todo"]["text"]
    plan = load_loopx_turn_plan_from_journal(runtime, goal_id=f.GOAL_ID, turn_key=request["turn_key"])
    journal_path = turn_journal_path(runtime, goal_id=f.GOAL_ID, turn_key=request["turn_key"])
    rebound = prepare_turn_decision_capture(plan, journal_path=journal_path,
                                          decision=None, existing_journal=True)
    assert rebound["turn_envelope"]["detail_ref"] == detail
    replay = list(base)
    index = replay.index("--turn-instance-id")
    del replay[index:index + 2]
    if first_attempt_fails:
        retry = [*replay, "--resume-turn-key", request["turn_key"], "--retry-failed-turn", "--execute"]
        received_before = (workspace / "received.txt").read_bytes()
        for corrupt in (True, False):
            if corrupt:
                path.write_text(json.dumps({**source_a, "reason": "replaced evidence"}))
            else:
                path.unlink()
            code, rejected = f._run_cli(retry)
            assert code == 1, rejected
            assert (workspace / "received.txt").read_bytes() == received_before
            assert f._quota_spend_count(runtime) == 0
            path.write_bytes(original)
        code, recovered = f._run_cli([*replay, "--resume-turn-key", request["turn_key"],
                                      "--retry-failed-turn", "--execute"])
        assert code == 0 and recovered["status"] == "committed", json.dumps(recovered)
        retried = json.loads((workspace / "received.txt").read_text().split("Turn request:\n", 1)[1])
        assert retried["turn_envelope"] == request["turn_envelope"]
        assert path.read_bytes() == original
    code, repeated = f._run_cli([*replay, "--resume-turn-key", request["turn_key"], "--execute"])
    assert code == 0, repeated
    assert not any(repeated["effects"].values())
    assert f._quota_spend_count(runtime) == 1
    assert path.read_bytes() == original
    # Corrupt/missing evidence cannot be replaced by the caller's new guard.
    path.write_text(json.dumps({**source_a, "reason": "wrong original"}))
    with pytest.raises((ValueError, RuntimeError), match="original observation"):
        prepare_turn_decision_capture(plan, journal_path=journal_path,
                                      decision=source_a, existing_journal=True)
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        prepare_turn_decision_capture(plan, journal_path=journal_path,
                                      decision=source_a, existing_journal=True)
    legacy = {**plan, "turn_envelope": {**plan["turn_envelope"], "detail_ref": {}}}
    with pytest.raises(ValueError, match="no original decision capture"):
        prepare_turn_decision_capture(legacy, journal_path=journal_path,
                                      decision=source_a, existing_journal=True)


@pytest.mark.parametrize("caller", ["native", "quota"])
def test_capture_compacts_layout_without_changing_values(tmp_path, caller):
    from loopx.cli_commands.quota_capture import capture_decision

    payload = {"ok": True, "context": {"text": '保留 two  spaces\nnew line\tand "quotes"',
        "values": [True, False, None, -0.0, 1.25], "nested": {"stop": "Do not publish."}}}
    path = tmp_path / "decision.json"
    if caller == "quota":
        capture_decision(tmp_path, payload)
    else:
        write_decision_capture(path, payload)
    raw = path.read_bytes()
    assert json.loads(raw) == payload
    assert raw.endswith(b"\n") and b"\n" not in raw[:-1]
    assert "保留".encode() in raw
    assert b"two  spaces" in raw and b"\\nnew line\\t" in raw
    assert len(raw) < len((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())
