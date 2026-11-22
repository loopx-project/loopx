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


def test_planning_batch_cli_help_and_schema_are_self_contained():
    import subprocess
    from benchmark.runtime.planning_batch import planning_batch_guidance, planning_batch_schema

    help_proc = subprocess.run(
        [sys.executable, "-m", "benchmark.runtime.planning_batch", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert help_proc.returncode == 0, help_proc.stderr
    assert "--schema" in help_proc.stdout
    assert "outcome.source.ref" in help_proc.stdout

    schema_proc = subprocess.run(
        [sys.executable, "-m", "benchmark.runtime.planning_batch", "--schema"],
        capture_output=True, text=True, timeout=30,
    )
    assert schema_proc.returncode == 0, schema_proc.stderr
    schema = json.loads(schema_proc.stdout)
    assert schema == planning_batch_schema()
    assert schema["fields"]["outcome"]["properties"]["source"]["ref"]["max_chars"] == 128
    assert schema["fields"]["outcome"]["properties"]["requirements"]["max_items"] == 32
    guidance = planning_batch_guidance()
    assert "--schema" in guidance
    assert 'status="ready"' in guidance


def _base_proposal():
    return {
        "input_digest": "digest-1",
        "expected_provider_revision": "rev-1",
        "outcome": {
            "objective": "Deliver fix",
            "requirements": [{"id": "req-1", "description": "Requirement passes"}],
            "source": {"ref": "checkpoint:init", "summary": "Initial planning checkpoint"},
        },
        "todos": [{"text": "[P0] Implement fix", "action_kind": "implement"}],
    }


@pytest.mark.parametrize(("mutate", "expected_substrings", "forbidden"), [
    (
        lambda p: p["outcome"]["source"].__setitem__("ref", "/frontier/task/repo/SECRET_REF/" + "x" * 120),
        ("outcome.source.ref", "got 151 chars, max 128", "no '/' paths or spaces"),
        "SECRET_REF",
    ),
    (
        lambda p: p["outcome"]["requirements"][0].__setitem__("id", "bad id with spaces"),
        ("outcome.requirements[0].id", "opaque identifier"),
        "bad id with spaces",
    ),
    (
        lambda p: p["todos"][0].__setitem__("action_kind", "do invalid action"),
        ("todos[0].action_kind", "^[a-z][a-z0-9_-]{0,63}$"),
        "do invalid action",
    ),
    (
        lambda p: p["outcome"].__setitem__("PRIVATE_EXTRA_KEY", "SECRET_VAL"),
        ("outcome", "unsupported field count: 1", "allowed: objective, requirements, source"),
        "PRIVATE_EXTRA_KEY",
    ),
    (
        lambda p: p["outcome"]["source"].__setitem__("summary", "S" * 513),
        ("outcome.source.summary", "got 513 chars, max 512"),
        "SSSSSSSS",
    ),
    (
        lambda p: p["outcome"].__setitem__("objective", "O" * 1025),
        ("outcome.objective", "got 1025 chars, max 1024"),
        "OOOOOOOO",
    ),
    (
        lambda p: p["outcome"]["requirements"][0].__setitem__("description", "D" * 2001),
        ("outcome.requirements[0].description", "got 2001 chars, max 2000"),
        "DDDDDDDD",
    ),
    (
        lambda p: p["outcome"].__setitem__(
            "requirements",
            [{"id": f"r-{i}", "description": f"desc {i}"} for i in range(33)],
        ),
        ("outcome.requirements exceeds capacity", "got 33 items, max 32"),
        None,
    ),
])
def test_planning_batch_upfront_validation_rejects_invalid_proposals_without_loopx(
    tmp_path, mutate, expected_substrings, forbidden,
):
    import subprocess
    from benchmark.runtime.planning_batch import apply_initial_plan

    proposal = _base_proposal()
    mutate(proposal)
    env = {"LOOPX_TASK_STAGE": "plan", "LOOPX_CLI": str(tmp_path / "missing-loopx")}
    with pytest.raises(ValueError) as exc_info:
        apply_initial_plan(env, proposal)
    message = str(exc_info.value)
    for snippet in expected_substrings:
        assert snippet in message, message
    if forbidden:
        assert forbidden not in message

    proposal_path = tmp_path / "bad-proposal.json"
    proposal_path.write_text(json.dumps(proposal))
    proc = subprocess.run(
        [sys.executable, "-m", "benchmark.runtime.planning_batch", str(proposal_path)],
        env={"PATH": "", "PYTHONPATH": str(Path(__file__).resolve().parents[2]), "LOOPX_TASK_STAGE": "plan"},
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    for snippet in expected_substrings:
        assert snippet in payload["error"], payload
    if forbidden:
        assert forbidden not in proc.stdout


def test_planning_batch_preserves_upstream_error_detail_on_refine_rejection(tmp_path, monkeypatch):
    from benchmark.runtime import planning_batch

    fake_cli = write_python_host_fixture(tmp_path / "fake-loopx", f"""#!{sys.executable}
import json, sys
args = sys.argv[1:]
if "todo" in args and "plan" in args:
    print(json.dumps({{
        "ok": True,
        "schema_version": "loopx_task_planning_v0",
        "goal_id": "goal-1",
        "agent_id": "agent-1",
        "execution_handoff": {{"owner": "caller"}},
        "input_digest": "digest-1",
        "existing_todos": [],
        "goal_waiting_on": [],
        "blocking_todo_ids": [],
        "goal_acceptance": {{
            "provider_revision": "rev-1",
            "goal_acceptance_contract": {{}},
        }},
    }}))
    raise SystemExit(0)
if "goal-acceptance" in args and "refine" in args:
    print(json.dumps({{
        "ok": False,
        "error": "goal_acceptance_invalid_request",
        "error_code": "goal_acceptance_invalid_request",
        "error_detail": "source ref must be bounded nonempty text (got 144 chars, max 128)",
    }}))
    raise SystemExit(1)
raise AssertionError(args)
""")
    proposal_path = tmp_path / "proposal.json"
    proposal = _base_proposal()
    # Allow explicit null optional target_key cleanly in normalized payload too.
    proposal["todos"][0]["target_key"] = None
    proposal_path.write_text(json.dumps(proposal))
    monkeypatch.setenv("LOOPX_TASK_STAGE", "plan")
    monkeypatch.setenv("LOOPX_CLI", str(fake_cli))
    monkeypatch.setenv("LOOPX_GOAL_ID", "goal-1")
    monkeypatch.setenv("LOOPX_AGENT_ID", "agent-1")
    monkeypatch.setenv("LOOPX_PROJECT", str(tmp_path))
    monkeypatch.setenv("LOOPX_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.setenv("LOOPX_RUNTIME_ROOT", str(tmp_path / "runtime"))
    task_doc = tmp_path / "task.md"
    task_doc.write_text("Task description\n")
    monkeypatch.setenv("LOOPX_TASK_DOC", str(task_doc))

    import contextlib
    import io
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = planning_batch.main([str(proposal_path)])
    assert code == 1
    payload = json.loads(stdout.getvalue())
    assert payload["ok"] is False
    assert payload["stage"] == "goal-acceptance refine"
    assert payload["error_code"] == "goal_acceptance_invalid_request"
    assert payload["error_detail"] == "source ref must be bounded nonempty text (got 144 chars, max 128)"
    assert payload["error_detail"] in payload["error"]
