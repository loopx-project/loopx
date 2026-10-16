from __future__ import annotations

import json
from pathlib import Path

from loopx.capabilities.periodic_report.pending_intent import (
    pending_periodic_report_intents,
)
from loopx.todos import add_goal_todo
from settlement_capability_dispatch_fixture import (
    AGENT_ID,
    COMPLETED_TODO,
    GOAL_ID,
    complete_todo_via_cli,
)


def _write_unclaimed_frontier_state(project: Path) -> None:
    """Leave an active successor after the earlier stage has been superseded."""

    project.joinpath("goal.md").write_text(
        f"""# Goal

## User Todo

## Agent Todo

- [ ] Ship the localized weekly report slice.
  <!-- loopx:todo todo_id={COMPLETED_TODO} status=open task_class=advancement_task claimed_by={AGENT_ID} continuation_policy=same_agent_non_delivery -->
- [ ] Advance the next research frontier.
  <!-- loopx:todo todo_id=todo_unclaimed_frontier status=open task_class=advancement_task -->
""",
        encoding="utf-8",
    )


def _claim_gated_successors(registry: Path) -> None:
    """Evolve durable state after the dispatch, as the next Turn would."""

    add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Re-run the outbound channel sync after network capacity returns.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
        action_kind="gated_work",
        resume_when="capacity_available:network",
    )
    add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Outline the follow-up frontier analysis.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
    )


def _intent_sidecar(runtime: Path) -> dict[str, object]:
    sidecar_dir = runtime / "goals" / GOAL_ID / "post_writeback_hooks"
    sidecars = sorted(sidecar_dir.glob("pwh_*.json"))
    assert len(sidecars) == 1
    return json.loads(sidecars[0].read_text(encoding="utf-8"))


def test_later_todo_completion_does_not_replay_superseded_successor_milestone(
    tmp_path: Path,
) -> None:
    captured, registry, runtime = complete_todo_via_cli(
        tmp_path,
        journal_capabilities=["network"],
        write_state=_write_unclaimed_frontier_state,
    )

    assert captured["available_capabilities"] == ["network"]
    intents = pending_periodic_report_intents(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert intents == []
    sidecar = _intent_sidecar(runtime)
    assert sidecar["intent"] is None


def test_later_todo_completion_without_capabilities_does_not_replay_milestone(
    tmp_path: Path,
) -> None:
    _captured, registry, runtime = complete_todo_via_cli(
        tmp_path,
        journal_capabilities=[],
        write_state=_write_unclaimed_frontier_state,
    )

    assert _intent_sidecar(runtime)["intent"] is None
    assert pending_periodic_report_intents(
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    ) == []


def test_post_writeback_frontier_and_progress_share_one_canonical_snapshot(
    tmp_path, monkeypatch
):
    from loopx.capabilities.periodic_report.post_writeback_hook import (
        build_periodic_report_post_writeback_projection,
    )
    from loopx.capabilities.periodic_report import todo_source

    _captured, registry, runtime = complete_todo_via_cli(
        tmp_path,
        journal_capabilities=["network"],
        write_state=_write_unclaimed_frontier_state,
    )
    _claim_gated_successors(registry)
    state = registry.parent / "goal.md"
    state.unlink()
    reads = []
    original = todo_source.read_canonical_todos_if_promoted

    def observe(**kwargs):
        result = original(**kwargs)
        reads.append(result["provider_revision"])
        return result

    monkeypatch.setattr(todo_source, "read_canonical_todos_if_promoted", observe)
    result = build_periodic_report_post_writeback_projection(
        payload={"available_capabilities": ["network"]},
        registry_path=registry,
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert len(reads) == 1
    # The history contains a later active successor, while this ordinary
    # post-writeback payload has no exact refresh identity. Canonical Todo
    # loading still occurs once, but it cannot recover the older milestone.
    assert result == {}
    assert not state.exists()
