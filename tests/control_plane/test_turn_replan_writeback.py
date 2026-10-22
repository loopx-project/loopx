"""Host outcomes are journal evidence, not implicit edits to task context."""
from __future__ import annotations

import json
import sys

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    SELECTED_REPLAN_TODO_ID,
    _bind_selected_replan_guard,
    _classification_count,
    _configure_selected_todo_replan_fixture,
    _initialize_git_checkout,
    _run_cli,
    _write_fixture,
)


def _todo(registry, runtime):
    rc, payload = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID,
                           "--todo-id", SELECTED_REPLAN_TODO_ID)
    assert rc == 0, payload
    return payload["todo"]


def _fixture(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _initialize_git_checkout(project)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    rc, listed = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID)
    assert rc == 0, listed
    initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID, handoff_mode="soft_claim", todos=listed["todos"],
    ), state_path=state, provider=provider)
    rc, updated = _run_cli(registry, runtime, "todo", "update", "--goal-id", GOAL_ID,
        "--todo-id", SELECTED_REPLAN_TODO_ID, "--agent-id", AGENT_ID,
        "--note", "Retain the original review context.",
        "--evidence", "fixture:original-task-evidence")
    assert rc == 0, updated
    turn = "replan-writeback-fixture"
    rc, initial = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", turn)
    assert rc == 0 and initial["decision"] == "autonomous_replan_required", initial
    _bind_selected_replan_guard(registry, runtime, project, turn)
    return project, runtime, registry, state, turn


def _runner(root, candidate, *, follow=False):
    # Exercise semantic writeback after the existing execution-profile evidence
    # gate; the independent command below checks these actual Host artifacts.
    candidate = {**candidate, "work_evidence": ["artifact:answer.txt" if follow else "artifact:plan.json"],
                 "validation_evidence": ["check:independent-command"]}
    runner = root / ("follow.py" if follow else "host.py")
    runner.write_text(
        "import json\nfrom pathlib import Path\n"
        f"candidate = {candidate!r}\n"
        "def run_dsh_turn(*, workspace, **kwargs):\n"
        "    root = Path(workspace)\n"
        "    counter = root / 'host-count.txt'\n"
        "    counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))\n"
        + (
            "    plan = json.loads((root / 'plan.json').read_text())\n"
            "    (root / 'answer.txt').write_text(str(sum(plan['inputs'])))\n"
            if follow else
            "    (root / 'plan.json').write_text(json.dumps({'inputs': [17, 25]}))\n"
        )
        + "    return json.dumps(candidate)\n",
        encoding="utf-8",
    )
    return runner


def _run_turn(project, runtime, registry, runner, *, turn=None, resume=None, follow=False):
    validator = (
        "from pathlib import Path; assert Path('answer.txt').read_text() == '42'"
        if follow else
        "import json; from pathlib import Path; "
        "assert json.loads(Path('plan.json').read_text()) == {'inputs': [17, 25]}"
    )
    return _run_cli(registry, runtime, "turn", "run-once", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--host", "dsh", "--execution-mode", "isolated-headless",
        "--project", str(project), "--dsh-runner", str(runner),
        "--dsh-home", str(project.parent / "dsh-home"),
        "--validation-command-json", json.dumps([sys.executable, "-c", validator]),
        "--scan-root", str(project), "--no-global-sync", "--execute",
        *(["--turn-instance-id", turn] if turn else []),
        *(["--resume-turn-key", resume] if resume else []), cwd=project)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("kind", ["repair_required", "replan_required"])
def test_semantic_refusal_preserves_task_context(tmp_path, monkeypatch, provider, kind):
    project, runtime, registry, state, turn = _fixture(tmp_path, monkeypatch, provider)
    before, state_bytes = _todo(registry, runtime), state.read_bytes()
    candidate = {"result_kind": kind, "classification": "fixture_missing_semantic_delta",
                 "summary": "A host diagnostic must not replace task context.",
                 "next_action": "Inspect the missing semantic result."}
    runner = _runner(tmp_path, candidate)
    rc, refused = _run_turn(project, runtime, registry, runner, turn=turn)
    assert rc == 1 and "requires a typed semantic delta" in refused["error"], refused
    assert _todo(registry, runtime) == before
    assert state.read_bytes() == state_bytes
    assert _classification_count(runtime, "quota_slot_spent") == 0
    [journal_path] = (runtime / "goals" / GOAL_ID / "turns").glob("*.json")
    journal = json.loads(journal_path.read_text())
    assert journal["task_validation"]["status"] == "passed"
    assert journal["host_result"]["summary"] == candidate["summary"]
    assert journal["host_result"]["next_action"] == candidate["next_action"]
    # Recover the same journal without a second Host attempt or task mutation.
    rc, _retry = _run_turn(project, runtime, registry, runner, resume=journal["turn_key"])
    assert rc == 1
    assert (project / "host-count.txt").read_text() == "1"
    assert _todo(registry, runtime) == before
    assert _classification_count(runtime, "quota_slot_spent") == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_accepted_replan_retains_context_and_drives_next_work(tmp_path, monkeypatch, provider):
    project, runtime, registry, _state, turn = _fixture(tmp_path, monkeypatch, provider)
    before = _todo(registry, runtime)
    vision = {"schema_version": "goal_vision_replan_contract_v0", "state": "active",
        "vision_patch": {"acceptance_summary": "Validate the plan, then execute the retained task."},
        "todo_delta": ["retain:" + SELECTED_REPLAN_TODO_ID],
        "path_delta": {"outcome": "replan", "prior_assumption": "A plan was not yet verified.",
            "observed_reality": "The plan passed independent validation.",
            "changed": ["Use the verified plan for the next task step."],
            "evidence_refs": ["fixture:validated-plan"]}}
    candidate = {"result_kind": "replan_required", "classification": "fixture_verified_plan",
        "summary": "A verified plan is ready.", "next_action": "Execute the verified plan.",
        "path_delta_mode": "material_replan", "agent_vision_json": json.dumps(vision)}
    runner = _runner(tmp_path, candidate)
    rc, accepted = _run_turn(project, runtime, registry, runner, turn=turn)
    assert rc == 0 and accepted["status"] == "committed", accepted
    assert _todo(registry, runtime) == before
    rc, replay = _run_turn(project, runtime, registry, runner, resume=accepted["resume_turn_key"])
    assert rc == 0 and not any(replay["effects"].values()), replay
    assert _classification_count(runtime, "quota_slot_spent") == 1
    follow = _runner(tmp_path, {"result_kind": "validated_progress",
        "classification": "fixture_executed_plan", "summary": "The retained task consumed the plan.",
        "next_action": "Assess remaining accepted work."}, follow=True)
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", turn + "-next",
        "--todo-id", SELECTED_REPLAN_TODO_ID)
    assert rc == 0 and guard.get("autonomous_replan_obligation") is None, guard
    rc, result = _run_turn(project, runtime, registry, follow, turn=turn + "-next", follow=True)
    assert rc == 0 and result["status"] == "committed", result
    assert result["validation"]["status"] == "passed"
    assert (project / "answer.txt").read_text() == "42"
    assert (project / "host-count.txt").read_text() == "2"
    assert _classification_count(runtime, "quota_slot_spent") == 2
    assert _todo(registry, runtime) == before
