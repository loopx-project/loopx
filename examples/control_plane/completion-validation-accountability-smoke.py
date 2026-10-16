#!/usr/bin/env python3
"""Keep accountable refresh and quota spend behind Todo validation authority."""

from __future__ import annotations

import json
import shlex
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.control_plane.quota.slot_accounting import (  # noqa: E402
    build_quota_slot_preview_for_decision,
)
from loopx.control_plane.todos.active_state_todo_parser import (  # noqa: E402
    parse_active_state_todos,
)
from loopx.state_refresh import refresh_state_run  # noqa: E402
from loopx.todos import add_goal_todo, complete_goal_todo  # noqa: E402

GOAL_ID = "completion-validation-accountability"
AGENT_ID = "codex-author"


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    project = root / "project"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "status: active",
                "updated_at: 2026-08-15T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runtime = root / "runtime"
    registry = root / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "benchmark_runner",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state.name,
                        "adapter": {"kind": "accountability_smoke_v0"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    import_fixture_authority(registry, GOAL_ID)
    return registry, runtime, state


def agent_summary(state: Path) -> dict:
    return parse_active_state_todos(
        state.read_text(encoding="utf-8"),
        item_limit=None,
    )["agent_todos"]


def quota_preview(
    *,
    runtime: Path,
    summary: dict,
    todo_id: str,
) -> dict:
    selected = next(item for item in summary["items"] if item["todo_id"] == todo_id)
    before = {
        "ok": True,
        "goal_id": GOAL_ID,
        "should_run": True,
        "state": "eligible",
        "effective_action": "normal_run",
        "normal_delivery_allowed": True,
        "selected_todo": selected,
        "workspace_repair_allowed": False,
        "safe_bypass_allowed": False,
        "recovery_delivery_allowed": False,
        "self_repair_allowed": False,
        "capability_repair_allowed": False,
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 1,
            "allowed_slots": 10,
            "spent_slots": 0,
        },
    }
    after = {**before, "quota": {**before["quota"], "spent_slots": 1}}
    return build_quota_slot_preview_for_decision(
        {
            "runtime_root": str(runtime),
            "attention_queue": {
                "items": [{"goal_id": GOAL_ID, "agent_todos": summary}]
            },
        },
        goal_id=GOAL_ID,
        before=before,
        after_decision=lambda _status: after,
        quota_status_builder=lambda goal, **_kwargs: goal["quota"],
        self_repair_spend_actions=frozenset(),
        agent_id=AGENT_ID,
        todo_id=todo_id,
    )


def main() -> None:
    with tempfile.TemporaryDirectory(
        prefix="loopx-completion-validation-accountability-"
    ) as tmp:
        root = Path(tmp)
        registry, runtime, state = write_fixture(root)
        independent_result = root / "controller-result.complete"
        validation_command = (
            f"{shlex.quote(sys.executable)} -c "
            + shlex.quote(
                "from pathlib import Path; import sys; "
                f"sys.exit(0 if Path({str(independent_result)!r}).is_file() else 1)"
            )
        )
        added = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Close out one independently verified benchmark result.",
            task_class="advancement_task",
            action_kind="benchmark_result_closeout",
            claimed_by=AGENT_ID,
            validation_command=validation_command,
            validation_label="independent runner result complete",
        )
        todo_id = str(added["todo_id"])

        open_summary = agent_summary(state)
        projected = next(
            item for item in open_summary["items"] if item["todo_id"] == todo_id
        )
        assert projected["completion_validation_required"] is True, projected
        serialized_projection = json.dumps(open_summary)
        assert str(independent_result) not in serialized_projection
        assert "validation_command" not in serialized_projection
        assert "validation_label" not in serialized_projection

        try:
            refresh_state_run(
                registry_path=registry,
                runtime_root_override=str(runtime),
                goal_id=GOAL_ID,
                project=None,
                state_file=None,
                classification="provisional_benchmark_reward",
                recommended_action="wait for the independent runner result",
                delivery_batch_scale="implementation",
                delivery_outcome="outcome_progress",
                agent_id=AGENT_ID,
                progress_scope="agent_lane",
                dry_run=False,
                sync_global=False,
            )
        except ValueError as exc:
            assert "completion validation" in str(exc), exc
        else:
            raise AssertionError("provisional reward became accountable")
        assert not (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").exists()

        blocked_spend = quota_preview(
            runtime=runtime,
            summary=open_summary,
            todo_id=todo_id,
        )
        assert blocked_spend["ok"] is False, blocked_spend
        assert "completion validation" in blocked_spend["reason"], blocked_spend

        blocked_completion = complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo_id,
            agent_id=AGENT_ID,
            evidence="provisional agent-side verifier result",
        )
        assert blocked_completion["validation_blocked_completion"] is True

        independent_result.write_text("complete\n", encoding="utf-8")
        completed = complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo_id,
            agent_id=AGENT_ID,
            evidence="independent runner result complete",
            no_followup=True,
        )
        assert completed["ok"] is True, completed
        assert completed["changed"] is True, completed

        done_summary = agent_summary(state)
        durable = next(
            item for item in done_summary["items"] if item["todo_id"] == todo_id
        )
        assert durable["status"] == "done", durable
        assert durable["completion_validation_required"] is True, durable

        refreshed = refresh_state_run(
            registry_path=registry,
            runtime_root_override=str(runtime),
            goal_id=GOAL_ID,
            project=None,
            state_file=None,
            classification="independent_benchmark_reward_validated",
            recommended_action="inspect the next bounded benchmark action",
            delivery_batch_scale="implementation",
            delivery_outcome="outcome_progress",
            agent_id=AGENT_ID,
            progress_scope="agent_lane",
            agent_vision_packet={
                "state": "active",
                "vision_summary": "Independent runner result is authoritative.",
            },
            dry_run=False,
            sync_global=False,
        )
        assert refreshed["appended"] is True, refreshed

        allowed_spend = quota_preview(
            runtime=runtime,
            summary=done_summary,
            todo_id=todo_id,
        )
        assert allowed_spend["ok"] is True, allowed_spend

    print("completion-validation-accountability-smoke ok")


if __name__ == "__main__":
    main()
