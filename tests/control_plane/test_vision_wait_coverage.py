"""Declared alternate paths must survive local failure, wait, and replan writeback."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.goals.goal_vision import normalize_goal_vision_packet
from loopx.control_plane.goals import goal_vision_wait
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.work_items.semantic_replan_writeback import (
    ReplanWritebackRejected,
    enforce_open_replan_writeback,
)
from loopx.quota import build_quota_should_run
from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture

GOAL = "fallback-wait-fixture"
AGENT = "worker"
COORDINATION = {"agent_model": "peer_v1", "registered_agents": [AGENT, "observer"]}
STATE = """# Active Goal State

## Agent Todo

- [ ] [P0] Observe recovery of source A.
  <!-- loopx:todo todo_id=todo_recovery status=open task_class=advancement_task claimed_by=observer -->
- [ ] [P0] Read source A after recovery.
  <!-- loopx:todo todo_id=todo_source_a status=deferred task_class=advancement_task claimed_by=worker resume_when=todo_done:todo_recovery -->
- [ ] [P2] Check source recovery on schedule.
  <!-- loopx:todo todo_id=todo_monitor status=open task_class=continuous_monitor claimed_by=worker cadence=1h next_due_at=2099-01-01T00:00:00+00:00 -->
"""


def vision_run(*, declared: bool = True) -> dict:
    vision = normalize_goal_vision_packet(
        {
            "state": "vision_drift_detected",
            "vision_patch": {
                "acceptance_summary": "Complete the bounded source check.",
                "replan_trigger_summary": "Source A is unavailable.",
            },
            "todo_delta": ["retain:todo_source_a", "create:todo_source_b"],
            "fallback_declarations": (
                [{"declaration_id": "source-b", "target_todo_id": "todo_source_b"}]
                if declared
                else []
            ),
        },
        goal_id=GOAL,
        agent_id=AGENT,
    )
    return {
        "classification": "source_path_checkpoint",
        "agent_id": AGENT,
        "generated_at": "2026-09-01T00:00:00+00:00",
        "agent_vision": vision,
    }


def decision(
    *,
    state: str = STATE,
    runs: list[dict] | None = None,
    profile: str = "outer_controller",
) -> dict:
    parsed = parse_active_state_todos(state, item_limit=None)
    payload = quota_status_payload(
        goal_id=GOAL,
        status="active",
        recommended_action="Wait for source A recovery.",
        agent_todos=parsed["agent_todos"],
        user_todos=parsed.get("user_todos"),
        coordination=COORDINATION,
        latest_runs=runs if runs is not None else [vision_run()],
    )
    return build_quota_should_run(
        payload,
        goal_id=GOAL,
        agent_id=AGENT,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            profile
        ),
    )


def fallback_state(metadata: str) -> str:
    return (
        STATE
        + f"""
- [ ] [P1] Read authorized source B.
  <!-- loopx:todo todo_id=todo_source_b task_class=advancement_task {metadata} -->
"""
    )


def test_vision_wait_composition_uses_one_runtime_crossing(monkeypatch) -> None:
    methods = []
    original = goal_vision_wait.effect_runtime_result

    def counted(method, request):
        methods.append(method)
        return original(method, request)

    monkeypatch.setattr(goal_vision_wait, "effect_runtime_result", counted)
    decision()

    assert methods == ["goal.vision_wait.project"]


@pytest.mark.parametrize(
    "extra_count,reverse", [(0, False), (8, False), (40, False), (40, True)]
)
@pytest.mark.parametrize("declared", [False, True])
def test_cli_wait_coverage_is_independent_of_display_size(
    tmp_path: Path, capsys, extra_count: int, reverse: bool, declared: bool
) -> None:
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    extra = "".join(
        f"\n- [ ] [P1] Unrelated external wait {i}.\n  <!-- loopx:todo todo_id=todo_unrelated_{i} status=deferred task_class=advancement_task claimed_by=worker resume_when=todo_done:todo_recovery -->\n"
        for i in range(extra_count)
    )

    def state(metadata: str | None) -> str:
        b = fallback_state(metadata)[len(STATE) :] if metadata else ""
        return STATE + (b + extra if reverse else extra + b)

    state_file.write_text(state(None))
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL,
                        "status": "active",
                        "domain": "engineering",
                        "waiting_on": "codex",
                        "state_file": str(state_file),
                        "repo": str(tmp_path),
                        "adapter": {
                            "kind": "fixture_connected_delivery_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "slot_minutes": 1,
                        },
                        "coordination": COORDINATION,
                    }
                ]
            }
        )
    )
    runtime = tmp_path / "runtime"
    runs = runtime / "goals" / GOAL / "runs"
    runs.mkdir(parents=True)
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state_file)
    if not declared:
        assert (
            cli_main(
                [
                    "--format",
                    "json",
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime),
                    "refresh-state",
                    "--goal-id",
                    GOAL,
                    "--agent-id",
                    AGENT,
                    "--progress-scope",
                    "agent_lane",
                    "--delivery-outcome",
                    "surface_only",
                    "--vision-state",
                    "vision_drift_detected",
                    "--vision-acceptance",
                    "Complete both bounded source checks.",
                    "--vision-replan-trigger",
                    "Both source checks still require evidence.",
                    "--vision-todo-delta",
                    "retain:todo_source_a",
                    "--vision-todo-delta",
                    "create:todo_source_b",
                    "--suppress-external-sinks",
                ]
            )
            == 0
        )
        authored = json.loads(capsys.readouterr().out)
        assert authored["ok"] is True
    else:
        run = vision_run(declared=declared)
        json_path = runs / "source-checkpoint.json"
        markdown_path = runs / "source-checkpoint.md"
        json_path.write_text(json.dumps(run) + "\n")
        markdown_path.write_text("# Source checkpoint\n")
        (runs / "index.jsonl").write_text(
            json.dumps(
                {
                    **run,
                    "json_path": str(json_path),
                    "markdown_path": str(markdown_path),
                }
            )
            + "\n"
        )
    args = [
        "--format",
        "json",
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "quota",
        "should-run",
        "--verbose",
        "--goal-id",
        GOAL,
        "--agent-id",
        AGENT,
        "--runtime-profile",
        "ark_managed_agent_goal",
    ]
    assert cli_main(args) == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing["effective_action"] == "autonomous_replan_required"
    runtime_revision = 0

    def reload_source_authority() -> None:
        nonlocal runtime, runtime_revision
        next_runtime = tmp_path / f"runtime-source-{runtime_revision}"
        shutil.copytree(runtime / "goals" / GOAL / "runs", next_runtime / "goals" / GOAL / "runs")
        runtime = next_runtime
        runtime_revision += 1
        initialize_canonical_source_fixture(runtime, GOAL, state_path=state_file)
        args[args.index("--runtime-root") + 1] = str(runtime)

    state_file.write_text(state("status=open claimed_by=worker"))
    reload_source_authority()
    assert cli_main(args) == 0
    continued = json.loads(capsys.readouterr().out)
    assert continued["selected_todo"]["todo_id"] == "todo_source_b"
    state_file.write_text(
        state("status=deferred claimed_by=worker resume_when=todo_done:todo_recovery")
    )
    reload_source_authority()
    assert cli_main(args) == 0
    waiting = json.loads(capsys.readouterr().out)
    assert waiting["execution_obligation"]["must_attempt_work"] is False
    assert (
        waiting["scheduler_hint"]["goal_runtime_continuation"]["disposition"] == "defer"
    )


@pytest.mark.parametrize(
    "metadata",
    [
        "status=open claimed_by=observer",
        "status=open claimed_by=worker excluded_agents=worker",
        "status=deferred claimed_by=worker",
        "status=done claimed_by=worker no_followup=true",
    ],
)
def test_uncovered_causal_todo_does_not_borrow_source_a_wait(metadata: str) -> None:
    result = decision(state=fallback_state(metadata), runs=[vision_run(declared=False)])
    assert (result.get("selected_todo") or {}).get("todo_id") != "todo_source_b"
    assert result["goal_frontier_projection"].get("vision_wait_state") is None
    assert result["effective_action"] == "autonomous_replan_required"


def test_missing_link_does_not_borrow_an_unrelated_blocker() -> None:
    state = (
        STATE
        + """
- [ ] [P1] Await unrelated owner input.
  <!-- loopx:todo todo_id=todo_unrelated_blocker status=blocked task_class=blocker claimed_by=worker reason=Await%20input -->
"""
    )
    result = decision(state=state, runs=[vision_run(declared=False)])
    assert result["goal_frontier_projection"].get("vision_wait_state") is None
    assert result["goal_frontier_projection"]["acceptance_gaps"]


@pytest.mark.parametrize("declared", [False, True])
def test_explicit_terminal_vision_keeps_existing_lifecycle_semantics(
    declared: bool,
) -> None:
    run = vision_run(declared=declared)
    run["agent_vision"]["state"] = "no_followup"
    assert not decision(runs=[run])["goal_frontier_projection"]["acceptance_gaps"]


def test_real_wait_clears_readback_and_writeback_obligation_without_declaration() -> (
    None
):
    state = fallback_state(
        "status=deferred claimed_by=worker resume_when=todo_done:todo_recovery"
    )
    assert (
        enforce_open_replan_writeback(
            newest_first_runs=[vision_run(declared=False)],
            state_text=state,
            agent_id=AGENT,
            goal_id=GOAL,
            registry_goal={"coordination": COORDINATION},
        )
        is None
    )


def test_empty_writeback_cannot_settle_uncovered_acceptance() -> None:
    with pytest.raises(ReplanWritebackRejected):
        enforce_open_replan_writeback(
            newest_first_runs=[vision_run(declared=False)],
            state_text=STATE,
            agent_id=AGENT,
            goal_id=GOAL,
            registry_goal={"coordination": COORDINATION},
        )


def test_single_authorized_path_may_wait_without_inventing_an_alternative() -> None:
    run = vision_run(declared=False)
    run["agent_vision"]["todo_delta"] = ["retain:todo_source_a"]
    result = decision(runs=[run])
    assert result["execution_obligation"]["must_attempt_work"] is False
    assert (
        result["goal_frontier_projection"]["vision_wait_state"]["selected_todo_id"]
        == "todo_source_a"
    )


@pytest.mark.parametrize("archived", [False, True])
def test_completed_predecessor_needs_a_real_waiting_successor(archived: bool) -> None:
    run = vision_run(declared=False)
    run["agent_vision"]["todo_delta"] = ["retain:todo_old_route"]
    source = fallback_state(
        "status=deferred claimed_by=worker resume_when=todo_done:todo_recovery"
    )
    if archived:
        source += "\n## Completed Work Archive\n"
    source += """
- [x] [P1] Replaced source route.
  <!-- loopx:todo todo_id=todo_old_route status=done task_class=advancement_task claimed_by=worker successor_todo_ids=todo_source_b -->
"""
    parsed = parse_active_state_todos(
        source, item_limit=None, goal={"latest_runs": [run]}
    )
    proof = parsed["agent_todos"]["vision_wait_states"][0]
    assert proof["selected_todo_id"] == "todo_source_b"
    assert enforce_open_replan_writeback(
        newest_first_runs=[run], state_text=source, agent_id=AGENT, goal_id=GOAL,
        registry_goal={"coordination": COORDINATION},
    ) is None
    # Finishing a predecessor alone does not close an open acceptance.
    without_successor = source.replace(" successor_todo_ids=todo_source_b", "")
    unproven = parse_active_state_todos(
        without_successor, item_limit=None, goal={"latest_runs": [run]}
    )
    assert not unproven["agent_todos"].get("vision_wait_states")


def test_monitor_and_excluded_blocker_cannot_cover_causal_work() -> None:
    for metadata in [
        "status=open task_class=continuous_monitor claimed_by=worker cadence=1h next_due_at=2099-01-01T00:00:00+00:00",
        "status=blocked task_class=blocker claimed_by=worker excluded_agents=worker reason=External%20wait",
    ]:
        source = (
            STATE
            + f"\n- [ ] [P1] Source B observation.\n  <!-- loopx:todo todo_id=todo_source_b {metadata} -->\n"
        )
        result = decision(state=source, runs=[vision_run(declared=False)])
        assert not result["goal_frontier_projection"].get("vision_wait_state")
        assert result["goal_frontier_projection"]["acceptance_gaps"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("priority", ["P0", "P2"])
def test_cli_current_agent_wait_preserves_canonical_blocker_priority(
    tmp_path: Path, capsys, provider: str, priority: str,
) -> None:
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        f"# Active Goal State\n\n## Agent Todo\n\n"
        f"- [ ] [{priority}] Await authorized source recovery.\n"
        "  <!-- loopx:todo todo_id=todo_source_a status=blocked "
        "task_class=blocker claimed_by=worker -->\n"
    )
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{
        "id": GOAL, "status": "active", "domain": "engineering",
        "waiting_on": "codex", "state_file": str(state_file), "repo": str(tmp_path),
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "quota": {"compute": 1.0, "window_hours": 24, "slot_minutes": 1},
        "coordination": COORDINATION,
    }]}))
    runtime = tmp_path / "runtime"
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state_file, provider=provider)
    runs = runtime / "goals" / GOAL / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run = vision_run(declared=False)
    run["agent_vision"]["todo_delta"] = ["retain:todo_source_a"]
    json_path, markdown_path = runs / "checkpoint.json", runs / "checkpoint.md"
    json_path.write_text(json.dumps(run))
    markdown_path.write_text("# Source checkpoint\n")
    (runs / "index.jsonl").write_text(json.dumps({
        **run, "json_path": str(json_path), "markdown_path": str(markdown_path),
    }) + "\n")

    assert cli_main([
        "--format", "json", "--registry", str(registry), "--runtime-root", str(runtime),
        "quota", "should-run", "--verbose", "--goal-id", GOAL, "--agent-id", AGENT,
        "--runtime-profile", "ark_managed_agent_goal",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    wait = result["goal_frontier_projection"]["vision_wait_state"]
    assert wait["reason_code"] == "current_agent_blocker"
    assert wait["selected_todo_id"] == "todo_source_a"
    assert wait["selected_todo_priority"] == priority
    assert wait["automatic_resume"] is False
