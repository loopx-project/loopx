"""Real initial planning, retry and admission through the shared benchmark worker."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import pytest

from benchmark.runtime.planning import task_plan_packet
from benchmark.runtime.sforge_entry import prepare_entry
from benchmark.runtime.worker import loopx_command, run_once
from tests.control_plane.host_process_fixture import write_python_host_fixture
from tests.control_plane.test_benchmark_current_admission import _environment, fixture


PLANNER = r'''
import json, os, re, shlex, subprocess, sys
from pathlib import Path

print(json.dumps({"type": "thread.started", "thread_id": "batch-planning-fixture"}), flush=True)
hint, body = sys.stdin.read().split("Host-supplied planning checkpoint:\n", 1)
packet = json.loads(body)
run = shlex.split(re.search(r"Run (.+?) <proposal\.json>", hint).group(1))
control = Path(os.environ["LOOPX_PLANNING_RESULT"]).parent
path = control / "proposal.json"
if not path.exists():
    proposal = {
        "input_digest": packet["input_digest"],
        "expected_provider_revision": packet["goal_acceptance"]["provider_revision"],
        "outcome": {
            "objective": "Deliver and verify a correct fixture calculation.",
            "requirements": [{"id": "correct", "description": "Calculate and preserve the expected result."}],
            "source": {"ref": "task", "summary": "Deliver and verify a correct fixture calculation."},
        },
        "todos": [
            {"text": "[P0] Implement the fixture calculation.", "action_kind": "implement"},
            {"text": "[P1] Verify the result.", "action_kind": "validate"},
            {"text": "[P2] Deliver the verified result.", "action_kind": "submit", "target_key": "fixture-result"},
        ],
    }
    path.write_text(json.dumps(proposal))
if os.environ.get("FIXTURE_CHANGED_PROPOSAL"):
    proposal = json.loads(path.read_text())
    proposal["todos"][0]["text"] = "Replace the interrupted plan."
    path = control / "changed-proposal.json"
    path.write_text(json.dumps(proposal))
result = subprocess.run([*run, str(path)], capture_output=True, text=True)
(control / "tool-response.json").write_text(result.stdout)
if result.returncode:
    raise SystemExit(result.returncode)
Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(result.stdout)
'''


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("drop_receipt", [False, True], ids=["fresh", "lost-response"])
def test_batch_planning_recovers_original_work_before_execution(tmp_path, monkeypatch, provider, drop_receipt):
    env, project, runtime, source, _ = _environment(tmp_path, monkeypatch, provider, replan="empty")
    control = tmp_path / "planning"
    control.mkdir()
    result = control / "result.json"
    dropped = control / "dropped-receipt"
    original_cli = env["LOOPX_CLI"]
    env.update(
        LOOPX_TASK_ENTRY="loopx-planned", LOOPX_TASK_STAGE="plan", LOOPX_PLANNING_RESULT=str(result),
        LOOPX_PHASE_DEADLINE_EPOCH=str(time.time() + 600),
        CODEX_BIN=str(write_python_host_fixture(tmp_path / "planner", f"#!{sys.executable}\n" + PLANNER)),
    )
    if drop_receipt:
        env["LOOPX_CLI"] = str(write_python_host_fixture(tmp_path / "drop-receipt", f"""#!{sys.executable}
import subprocess, sys
from pathlib import Path
args = sys.argv[1:]
response = subprocess.run([{original_cli!r}, *args], capture_output=True)
marker = Path({str(dropped)!r})
effect = args[args.index("--operation-id") + 1] if "--operation-id" in args else ""
if response.returncode == 0 and "add" in args and effect.endswith(":todo:1") and not marker.exists():
    marker.touch()
    raise SystemExit(1)
sys.stdout.buffer.write(response.stdout)
sys.stderr.buffer.write(response.stderr)
raise SystemExit(response.returncode)
"""))

    def read():
        return task_plan_packet(env, loopx_command(env))

    first = run_once(env)
    original_ids = set()
    if drop_receipt:
        # The second Todo's write succeeded. Its lost response leaves the
        # binding and the caller's ready result absent.
        assert not first["ok"] and dropped.exists() and not result.exists()
        assert json.loads((control / "tool-response.json").read_text())["error"] == (
            "LoopX todo add returned no JSON receipt"
        )
        partial = read()
        original_ids = set(partial["runnable_todo_ids"])
        assert len(original_ids) == len(partial["existing_todos"]) == 2
        assert not partial["goal_acceptance"]["planning_requirements"]["linked_todo_ids"]

        assert not run_once(env | {"FIXTURE_CHANGED_PROPOSAL": "yes"})["ok"]
        assert "declaration is missing or changed" in json.loads((control / "tool-response.json").read_text())["error"]
        assert set(read()["runnable_todo_ids"]) == original_ids and not result.exists()
        # SForge uses the same worker for its timed initial plan.
        assert prepare_entry(env)
    else:
        assert first["ok"], (Path(env["LOOPX_WAKE_LOG_DIR"]) / first["turn_id"] / "stderr.log").read_text()

    saved = json.loads(result.read_text())
    ids = saved["todo_ids"]
    assert len(ids) == 3 and original_ids < set(ids)
    assert saved["state_readback_verified"] is True
    assert saved["goal_requirements_readback"]["linked_todo_ids"] == ids
    assert saved["execution_owner"] == "caller"
    stored = {item["todo_id"]: item for item in read()["existing_todos"]}
    assert set(stored) == set(ids)
    assert [stored[item]["action_kind"] for item in ids] == ["implement", "validate", "submit"]
    assert stored[ids[2]]["target_key"] == "fixture-result"
    assert all(stored[item]["claimed_by"] == fixture.AGENT_ID for item in ids)

    replay = run_once(env)
    assert replay["ok"] and replay["planning"]["todo_ids"] == ids
    assert len(read()["existing_todos"]) == 3
    assert fixture._spend_run_count(runtime) == 0
    log = runtime / "goals" / fixture.GOAL_ID / "rollout-event-log.jsonl"
    assert not log.exists() or "quota_should_run" not in log.read_text()

    code, admitted = fixture._run_cli(
        source, runtime, "quota", "should-run", "--goal-id", fixture.GOAL_ID,
        "--agent-id", fixture.AGENT_ID, "--todo-id", ids[0], "--runtime-profile", "generic_cli",
        "--context-delivery", "off", "--delta-delivery", "off",
        "--turn-instance-id", "batch-fixture-execution", cwd=project,
    )
    assert code == 0 and admitted["should_run"] and admitted["selected_todo"]["todo_id"] == ids[0]
    events = fixture._heartbeat_receipt_events(runtime, "batch-fixture-execution")
    assert [entry["event_id"] for entry in events] == [admitted["heartbeat_receipt"]["event_id"]]
    assert fixture._spend_run_count(runtime) == 0
