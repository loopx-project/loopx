"""Real CLI recovery: missing checkpoint must not require another work Turn."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from loopx.state_refresh import render_state_refresh_markdown

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    REPO_ROOT,
    GOAL_ID,
    TODO_ID,
    TURN_ID,
    _initialize_git_checkout,
    _run_cli,
    _write_fixture,
    _spend_run_count,
)


def _assert_checkpoint_instructions(rendered: str) -> None:
    assert "checkpoint-context" in rendered
    assert "--checkpoint-read-context" in rendered
    assert "same Goal, Agent, Todo/obligation, Turn, and delivery fields" in rendered
    assert "Remove previously executed state-mutation options" in rendered
    for option in (
        "--next-action", "--next-action-basis", "--autonomous-replan-recorded", "--repair-delta-kind",
        "--usage-json", "--usage-codex-session",
    ):
        assert option in rendered
    assert "even when their values are unchanged" in rendered
    assert "only one valid vision decision" in rendered


@pytest.fixture
def first_refresh():
    return {
        "ok": True, "appended": True, "dry_run": False,
        "refresh_recovery": {"decision": "append", "reason": "first_writeback"},
        "settlement_identity": {"turn_instance_id": TURN_ID},
        "vision_checkpoint": {
            "required": True, "satisfied": False, "decision": "missing_required",
            "required_resolution": ["write_vision_patch"],
        },
        "state_projection_gap": {"recommended_action": "Inspect the remaining scope."},
    }


@pytest.mark.parametrize("missing_baseline", [True, False, None])
def test_first_markdown_explains_checkpoint_without_mutating_payload(
    first_refresh, missing_baseline,
):
    if missing_baseline is not None:
        first_refresh["vision_checkpoint"]["missing_baseline"] = missing_baseline
    before = deepcopy(first_refresh)
    rendered = render_state_refresh_markdown(first_refresh)
    _assert_checkpoint_instructions(rendered)
    assert "writeback succeeded" in rendered
    assert "required vision checkpoint is still unsatisfied" in rendered
    assert "one-spend settlement order" in rendered
    assert "does not certify acceptance or bypass replan, identity, or terminal gates" in rendered
    if missing_baseline is True:
        assert "No persisted vision baseline is available" in rendered
        assert "--vision-unchanged-reason" not in rendered
    else:
        assert "No persisted vision baseline is available" not in rendered
        assert "only if a persisted vision exists and its scope and acceptance still apply" in rendered
    assert rendered.index("vision_checkpoint_required_resolution") < rendered.index("writeback succeeded")
    assert rendered.index("writeback succeeded") < rendered.index("state_projection_gap")
    assert render_state_refresh_markdown(first_refresh) == rendered
    assert first_refresh == before


@pytest.mark.parametrize("field,value", [
    ("ok", False), ("ok", 1), ("appended", False), ("appended", 1),
    ("dry_run", True), ("dry_run", 0), ("dry_run", None),
    ("settlement_identity", None), ("settlement_identity", {}),
    ("settlement_identity", "invalid"), ("refresh_recovery", None),
    ("vision_checkpoint", None), ("vision_checkpoint", "invalid"),
    ("refresh_recovery.reason", "other"), ("refresh_recovery.reason", None),
    ("refresh_recovery.decision", "supplement_checkpoint"),
    ("vision_checkpoint.required", False), ("vision_checkpoint.required", 1),
    ("vision_checkpoint.satisfied", True), ("vision_checkpoint.satisfied", 0),
    ("vision_checkpoint.decision", "not_required"),
])
def test_first_markdown_hint_requires_first_committed_missing_checkpoint(
    first_refresh, field, value,
):
    # Stay within the existing renderer's supported input domain.
    keys = field.split(".")
    target = first_refresh if len(keys) == 1 else first_refresh[keys[0]]
    if value is None:
        target.pop(keys[-1])
    else:
        target[keys[-1]] = value
    rendered = render_state_refresh_markdown(first_refresh)
    assert "Submit a checkpoint-only refresh" not in rendered
    assert "state_projection_gap" in rendered


@pytest.mark.parametrize("decision", ["replay", "repair_receipt", "reject"])
def test_recovery_markdown_preserves_routing_and_error_precedence(decision):
    payload = {
        "ok": decision != "reject",
        "refresh_recovery": {"decision": decision, "reason": "original_writeback_preserved"},
        "vision_checkpoint": {"decision": "missing_required", "satisfied": False},
    }
    if decision == "reject":
        payload["error"] = "committed_writeback_payload_conflict"
    rendered = render_state_refresh_markdown(payload)
    assert "missing_required" in rendered
    assert "original writeback preserved" in rendered
    assert "writeback succeeded" not in rendered
    assert "None" not in rendered
    if decision == "reject":
        assert payload["error"] in rendered
        assert "Submit a checkpoint-only refresh" not in rendered
    else:
        _assert_checkpoint_instructions(rendered)
        assert "only if a persisted vision exists and its scope and acceptance still apply" in rendered
    payload["vision_checkpoint"].update(decision="patched", satisfied=True)
    assert "Submit a checkpoint-only refresh" not in render_state_refresh_markdown(payload)


@pytest.mark.parametrize("decision", ["unchanged", "patch"])
def test_same_turn_checkpoint_supplement_with_read_context_is_idempotent(tmp_path: Path, decision: str):
    project, runtime, registry = _write_fixture(tmp_path)
    rc, initial = _run_cli(
        registry,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--vision-summary",
        "Validate the scoped change.",
        "--vision-acceptance",
        "Focused validation passes.",
        "--no-global-sync",
        "--suppress-external-sinks",
        cwd=project,
    )
    assert rc == 0, initial
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )
    rc, guard = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert rc == 0, guard
    args = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--classification",
        "validated_change",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    mutation = ("--next-action", "Verify the scoped delivery evidence.") if decision == "patch" else ()
    if mutation:
        args += ("--progress-scope", "goal")
        # Capture the actual first Markdown stdout, not a JSON-then-replay result.
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "markdown", *args, *mutation],
            cwd=project, capture_output=True, text=True, check=False,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        _assert_checkpoint_instructions(result.stdout)
        assert "writeback succeeded" in result.stdout
        rows = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        first = json.loads(rows.read_text(encoding="utf-8").splitlines()[-1])
    else:
        rc, first = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0, first
        assert first["ok"] is True and first["appended"] is True
        assert first["dry_run"] is False
        _assert_checkpoint_instructions(render_state_refresh_markdown(first))
    assert first["refresh_recovery"]["decision"] == "append"
    assert first["refresh_recovery"]["reason"] == "first_writeback"
    assert first["settlement_identity"]["turn_instance_id"] == TURN_ID
    assert first["vision_checkpoint"]["decision"] == "missing_required"
    original_bytes = Path(first["json_path"]).read_bytes()
    state_path = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    original_state = state_path.read_bytes()
    if mutation:
        assert mutation[1] not in original_state.decode("utf-8")
        assert first["recommended_action_resolution"]["recommended_action_source"] == "agent_lane_step"
        assert first["recommended_action_resolution"]["recommended_action"] == mutation[1]
    supplement = (
        (
            "--vision-unchanged-reason",
            "The accepted scope and evidence remain applicable.",
        )
        if decision == "unchanged"
        else ("--vision-last-patch", "Validation evidence checked.")
    )
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    before_preview = index.read_bytes()
    if mutation:
        rc, rejected = _run_cli(registry, runtime, *args, *mutation, *supplement, cwd=project)
        assert rc == 1, rejected
        assert rejected["refresh_recovery"]["reason"] == "checkpoint_supplement_must_not_repeat_mutations"
        assert index.read_bytes() == before_preview
        assert Path(first["json_path"]).read_bytes() == original_bytes
        assert state_path.read_bytes() == original_state
        assert _spend_run_count(runtime) == 0
    rc, context = _run_cli(registry, runtime, "checkpoint-context", "--goal-id", GOAL_ID, *binding, cwd=project)
    assert rc == 0, context
    supplement += ("--checkpoint-read-context", context["read_context_id"])
    rc, preview = _run_cli(
        registry, runtime, *args, *supplement, "--dry-run", cwd=tmp_path
    )
    assert rc == 0, preview
    assert index.read_bytes() == before_preview
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: _run_cli(registry, runtime, *args, *supplement, cwd=tmp_path),
                range(2),
            )
        )
    assert all(rc == 0 for rc, _ in results), results
    assert sum(result["appended"] for _, result in results) == 1
    repaired = next(result for _, result in results if result["appended"])
    assert repaired["appended"] is True
    assert repaired["refresh_recovery"]["decision"] == "supplement_checkpoint"
    assert repaired["vision_checkpoint"]["satisfied"] is True
    assert repaired["settlement_identity"] == first["settlement_identity"]
    assert repaired["delivery_workspace"] == first["delivery_workspace"]
    assert Path(first["json_path"]).read_bytes() == original_bytes
    assert state_path.read_bytes() == original_state
    after_supplement = index.read_bytes()
    rc, replay = _run_cli(registry, runtime, *args, *supplement, cwd=tmp_path)
    assert rc == 0, replay
    assert replay["appended"] is False
    assert replay["idempotent_replay"] is True
    assert replay["vision_checkpoint"]["satisfied"] is True
    assert index.read_bytes() == after_supplement
    rc, conflict = _run_cli(
        registry,
        runtime,
        *args,
        "--vision-unchanged-reason",
        "A different checkpoint decision.",
        cwd=project,
    )
    assert rc == 1, conflict
    assert conflict["appended"] is False
    assert (
        conflict["refresh_recovery"]["reason"] == "committed_vision_decision_conflict"
    )
    assert _spend_run_count(runtime) == 0
    spend = (
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--source",
        "heartbeat",
        "--slots",
        "1",
        "--execute",
        "--scan-path",
        str(project),
    )
    for _ in range(2):
        rc, result = _run_cli(registry, runtime, *spend, cwd=project)
        assert rc == 0, result
    assert _spend_run_count(runtime) == 1


def test_checkpoint_only_recovery_bypasses_open_todo_completion_validation(
    tmp_path: Path,
) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_git_checkout(project)
    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_text = state_path.read_text(encoding="utf-8")
    selected_marker = (
        f"todo_id={TODO_ID} status=open "
        "task_class=advancement_task action_kind=validate "
        "-->"
    )
    assert selected_marker in state_text
    turn_id = "turn-checkpoint-open-validator"
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn_id,
    )
    rc, guard = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert rc == 0, guard
    assert guard["decision"] == "run"
    assert guard["selected_todo"]["todo_id"] == TODO_ID

    delivery = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--classification",
        "validated_progress",
        "--progress-scope",
        "goal",
        "--delivery-batch-scale",
        "implementation",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-workspace-path",
        str(project),
        "--progress-result-class",
        "advanced",
        "--progress-surface-id",
        "surface:checkpoint-recovery",
        "--progress-probe-kind",
        "probe:checkpoint-recovery",
        "--progress-evidence-id",
        "evidence:checkpoint-recovery",
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    mutations = (
        "--next-action",
        "Inspect the remaining checkpoint recovery scope.",
    )
    rc, first = _run_cli(registry, runtime, *delivery, *mutations, cwd=project)
    assert rc == 0, first
    assert first["appended"] is True
    assert first["vision_checkpoint"]["decision"] == "missing_required"

    checkpoint = first["vision_checkpoint"]
    assert checkpoint["missing_baseline"] is True
    assert checkpoint["required_resolution"] == ["write_vision_patch"]
    instructions = render_state_refresh_markdown(first)
    _assert_checkpoint_instructions(instructions)
    assert "No persisted vision baseline is available" in instructions
    assert "--vision-unchanged-reason" not in instructions

    # A controller may install completion validation before checkpoint repair.
    # The supplement repairs the existing receipt while the Todo remains open.
    state_path.write_text(
        state_path.read_text(encoding="utf-8").replace(
            selected_marker,
            selected_marker.replace(" -->", " validation_command=pytest -->"),
            1,
        ),
        encoding="utf-8",
    )

    vision = (
        "--vision-summary",
        "Continue the accepted checkpoint recovery scope.",
        "--vision-acceptance",
        "The exact recovery preserves validation and identity fences.",
    )
    rc, rejected = _run_cli(
        registry, runtime, *delivery, *mutations, *vision, cwd=project
    )
    assert rc == 1, rejected
    assert (
        rejected["refresh_recovery"]["reason"]
        == "checkpoint_supplement_must_not_repeat_mutations"
    )

    wrong_binding = list(delivery)
    wrong_todo_index = wrong_binding.index(TODO_ID)
    wrong_binding[wrong_todo_index] = "todo_chain_000000000001"
    rc, wrong_identity = _run_cli(
        registry, runtime, *wrong_binding, *vision, cwd=project
    )
    assert rc == 1, wrong_identity
    assert "settlement binding does not match" in wrong_identity["error"]

    rc, context = _run_cli(registry, runtime, "checkpoint-context", "--goal-id", GOAL_ID, *binding, cwd=project)
    assert rc == 0, context
    rc, repaired = _run_cli(registry, runtime, *delivery, *vision,
        "--checkpoint-read-context", context["read_context_id"], cwd=project)
    assert rc == 0, repaired
    assert repaired["appended"] is True
    assert repaired["refresh_recovery"]["decision"] == "supplement_checkpoint"
    assert repaired["vision_checkpoint"]["satisfied"] is True
    assert repaired["settlement_identity"] == first["settlement_identity"]

    rc, conflict = _run_cli(
        registry,
        runtime,
        *delivery,
        "--vision-summary",
        "Choose a conflicting recovery path.",
        cwd=project,
    )
    assert rc == 1, conflict
    assert (
        conflict["refresh_recovery"]["reason"]
        == "committed_vision_decision_conflict"
    )
    assert _spend_run_count(runtime) == 0


def test_invalid_supplement_leaves_original_writeback_intact(tmp_path: Path):
    project, runtime, registry = _write_fixture(tmp_path)
    binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        TURN_ID,
    )
    rc, guard = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--scan-path",
        str(project),
        cwd=project,
    )
    assert rc == 0, guard
    args = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        *binding,
        "--delivery-outcome",
        "outcome_progress",
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    rc, first = _run_cli(registry, runtime, *args, cwd=project)
    assert rc == 0, first
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    before = index.read_bytes()
    rc, rejected = _run_cli(
        registry,
        runtime,
        *args,
        "--vision-unchanged-reason",
        "No baseline exists.",
        cwd=project,
    )
    assert rc == 1, rejected
    assert "existing vision" in rejected["error"]
    assert index.read_bytes() == before
    rc, bad_patch = _run_cli(
        registry, runtime, *args, "--vision-summary", "x" * 421, cwd=project
    )
    assert rc == 1, bad_patch
    assert index.read_bytes() == before
    assert _spend_run_count(runtime) == 0
