"""Native launch preserves the admitted binding through real CLI and stores."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from host_process_fixture import write_python_host_fixture
from test_replan_successor_guard_reentry import AGENT, GOAL, _fixture


def _host(root: Path, *, material: bool) -> Path:
    # This is a deterministic process transport, not a model or task evaluator.
    # The material arm writes a distinct artifact checked by the outer validator;
    # the replan arm ends the iteration and earns no writeback or debit.
    return write_python_host_fixture(root / "native-host", f'''#!/usr/bin/env python3
import json, pathlib, sys
request = json.loads(sys.stdin.read().split("Turn request:\\n", 1)[1])
with pathlib.Path("host-requests.jsonl").open("a") as stream:
    stream.write(json.dumps(request) + "\\n")
material = {material!r}
if material:
    pathlib.Path("checked-source.txt").write_text("independently checked source\\n")
print(json.dumps({{"type": "thread.started", "thread_id": "fixture-launch-session"}}), flush=True)
result = {{
    "schema_version": "loopx_turn_result_v0", "turn_key": request["turn_key"],
    "result_kind": "validated_progress" if material else "iteration_failed",
    "completed_phases": ["host_execute", "typed_result"],
    "classification": "source_checked" if material else "",
    "recommended_action": "Retain the original source criteria." if material else "",
    "next_action": "Continue independent source validation." if material else "",
    "delivery_batch_scale": "single_surface" if material else "",
    "delivery_outcome": "outcome_progress" if material else "",
    "path_delta_mode": "unchanged" if material else "", "agent_vision_json": "",
    "vision_unchanged_reason": "Original source criteria still apply." if material else "",
    "progress_observation_json": "",
    "summary": "Source checked." if material else "Source remains unverified.",
}}
if material:
    result.update(work_evidence=["artifact:checked-source"],
                  validation_evidence=["check:independent-source-validator"])
args = sys.argv[1:]
pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(result))
''')


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("binding", ["todo", "autonomous_replan"])
def test_native_cli_uses_original_binding_and_replays_without_new_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, binding: str,
) -> None:
    call, runtime, _ = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    todo_id = None
    declared_text = None
    if binding == "todo":
        todo_id = call("todo", "add", "--goal-id", GOAL, "--role", "agent",
            "--claimed-by", AGENT, "--text", "Independently check the original source.",
            "--task-class", "advancement_task", "--action-kind", "validate")["todo_id"]
        declared_text = call("todo", "list", "--goal-id", GOAL,
            "--todo-id", todo_id)["todo"]["text"]
    project = tmp_path / "project"
    executable = _host(tmp_path, material=binding == "todo")
    decision = ["--goal-id", GOAL, "--agent-id", AGENT, "--host", "codex-cli",
        "--execution-mode", "isolated-headless", "--scheduler-owner", "outer_controller",
        "--iteration-context", "fresh", "--session-scope", "agent"]
    if todo_id:
        decision += ["--todo-id", todo_id]
    common = [*decision, "--project", str(project), "--codex-bin", str(executable),
        "--no-global-sync"]
    if todo_id:
        common += ["--validation-command-json", json.dumps([
            sys.executable, "-c", "from pathlib import Path; "
            "assert Path('checked-source.txt').read_text() == 'independently checked source\\n'",
        ])]
    turn = "native-launch-binding"
    preview = call("turn", "plan", *decision, "--turn-instance-id", turn,
        "--include-transaction-detail")
    assert preview["route"]["kind"] == ("ready_for_host" if todo_id else "replan_required")
    obligation_id = None
    subject = todo_id
    if not todo_id:
        envelope = preview["turn_envelope"]
        obligation_id = envelope["replan_action_packet"]["obligation_id"]
        assert obligation_id.startswith("replan-")
        subject = f"autonomous_replan:{obligation_id}"
    expected_effect = f"{GOAL}:{AGENT}:{subject}:{turn}"
    assert not (project / "host-requests.jsonl").exists()

    result = call("turn", "run-once", *common, "--turn-instance-id", turn, "--execute")
    assert result["effects"]["host_invoked"] is True, result
    assert result["receipt"]["settlement_effect_id"] == expected_effect
    lineage = result["receipt"]["lineage"]
    assert lineage["todo_id"] == (todo_id or "")
    assert lineage.get("replan_obligation_id") == obligation_id
    assert result["effects"]["state_written"] is bool(todo_id)
    assert result["quota_slot_spend_count"] == int(bool(todo_id))
    if todo_id:
        assert result["status"] == "committed" and result["validation"]["ok"] is True
        current = call("todo", "list", "--goal-id", GOAL, "--todo-id", todo_id)
        assert current["todo"]["text"] == declared_text
    else:
        assert result["status"] == "stopped" and result["result_kind"] == "iteration_failed"
        assert not (project / "checked-source.txt").exists()

    requests = (project / "host-requests.jsonl").read_bytes()
    sent = json.loads(requests)
    assert sent["turn_key"] == result["receipt"]["turn_key"]
    captures = {path: path.read_bytes() for path in runtime.rglob("decisions/*.json")}
    assert captures
    replay_args = list(common)
    if todo_id:
        rejected = call("turn", "run-once", *common, "--resume-turn-key", sent["turn_key"],
            "--execute", expected_code=1)
        assert "cannot retarget" in rejected["error"] and not any(rejected["effects"].values())
        index = replay_args.index("--todo-id")
        del replay_args[index:index + 2]
    replay = call("turn", "run-once", *replay_args, "--resume-turn-key", sent["turn_key"],
        "--execute")
    assert replay["replayed"] is True
    assert not any(replay["effects"].values())
    assert (project / "host-requests.jsonl").read_bytes() == requests
    assert all(path.read_bytes() == original for path, original in captures.items())
